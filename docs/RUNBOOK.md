# Runbook

How to operate KnowledgeOS: what the probes mean, what breaks, and what to do
about it. Written for whoever is on call, which may be you in six months.

---

## Health probes

| Probe | Checks | Meaning |
|---|---|---|
| `GET /healthz` | nothing | The process is alive. **Never** touches a dependency — an orchestrator restarting on this must not cycle the whole fleet because the database blinked. |
| `GET /readyz` | Postgres, Redis, Qdrant | Can this replica serve traffic. Returns `503` and **names the failing dependency** in `failing[]`. |

```bash
curl -s localhost:8000/readyz | jq
# {"status":"ready","checks":{"database":true,"redis":true,"qdrant":true},"failing":[]}
```

A `degraded` response is actionable on its own: the failing dependency is named, so
the next step is a specific service, not an investigation.

---

## Common failures

### Documents stuck in `PENDING`

The worker is not consuming. Check in order:

```bash
docker compose ps worker            # is it running?
docker compose logs --tail=50 worker
curl -s localhost:8000/api/v1/workspaces/<ws>/admin/system | jq '.queue_pending, .queue_processing'
```

- `queue_pending` climbing, `queue_processing` at 0 → no worker is claiming.
  Restart it; scale with `docker compose up -d --scale worker=4`.
- `queue_processing` stuck above 0 for >15 min → a worker died mid-job. The reaper
  returns it after a 15-minute visibility timeout automatically; nothing to do but
  confirm it recovers.
- **First run is slow.** The embedding model (~90 MB ONNX) downloads on first use.
  It is cached in the `model_cache` volume afterwards.

### Documents reaching `FAILED`

The reason is on the document and in `/admin/jobs`.

| Message | Cause | Action |
|---|---|---|
| "No extractable text found" | A scanned PDF — an image of text with no text layer | Expected. OCR is not implemented; this fails loudly rather than ingesting an empty document that would silently never appear in an answer. |
| "The PDF is password-protected" | Encrypted source | Supply an unprotected copy. |
| "Could not read the …" | Corrupt or mislabelled file | Check the real format; magic-byte sniffing rejects a `.pdf` that is actually a zip. |

Retry with `POST /api/v1/documents/{id}/reprocess` (ADMIN).

### Chat returns 502 / 503

`502 provider_error` — the LLM provider failed. Check the key is set and live:

```bash
curl -s localhost:8000/api/v1/workspaces/<ws>/admin/system | jq '.llm_provider, .llm_configured'
```

`llm_configured: false` means no key for the selected provider. **Ingestion, search
and retrieval are unaffected** — embeddings run locally. To keep the product usable
while a provider is down, set `LLM_PROVIDER=scripted`.

### Everything answers "I could not find anything"

The refusal gate is firing on everything. Either:

1. **The corpus is genuinely empty or unindexed.** Check `vector_count` in
   `/admin/system` against the chunk count in Postgres. A mismatch means Qdrant and
   Postgres have drifted — Postgres is the source of truth, so re-index by
   reprocessing the affected documents.
2. **`RELEVANCE_FLOOR` is too high for the current embedding model.** Thresholds
   are model-specific. Re-measure: run `POST /workspaces/{id}/search` with known-good
   and known-bad questions and compare the top scores. See `docs/DECISIONS.md`
   ADR-002 for the method.

A refusal rate of *exactly zero* is also suspicious — it usually means the gate is
broken, not that the answers are perfect.

### Login returns 500

Historically caused by `request.client.host` not being a valid IP (unix socket,
some proxy configurations) being inserted into the `INET` column. Now coerced and
dropped when unparseable. If it recurs, check the logs for `DataError` naming a
column type.

---

## Signals worth alerting on

**Page:**

| Signal | Threshold |
|---|---|
| `tenant_isolation_violations` | **any** non-zero value |
| `auth.refresh_reuse_detected` | any — a refresh token was replayed |
| Availability error-budget burn | fast burn |
| Ingestion queue stalled | `oldest_job_age` > 15 min |

**Ticket, don't page:**

`retrieval_zero_hit_ratio` > 5 % · `citation_stripped_ratio` > 2 % ·
`groundedness_p10` < 0.6 · `cost_per_query_p95` above budget · dependency drift.

Alert on symptoms, never on causes. High CPU with healthy latency is a working
system, and paging on it trains people to ignore the pager.

---

## Incident procedures

### Suspected credential compromise

```bash
# 1. Revoke every session immediately.
#    Rotating SECRET_KEY invalidates all access tokens at once.
#    Refresh families must be revoked in the database.
docker compose exec backend python -c "
from app.db.session import SessionLocal
from app.db.models.identity import RefreshToken
from datetime import datetime, UTC
db = SessionLocal()
n = db.query(RefreshToken).filter(RefreshToken.revoked_at.is_(None)).update(
    {'revoked_at': datetime.now(UTC)})
db.commit(); print(f'revoked {n} refresh tokens')"

# 2. Rotate SECRET_KEY in the secret manager and redeploy.
# 3. Rotate provider API keys.
# 4. Export the audit trail for the window.
```

### Restoring after data loss

Postgres is the source of truth; **Qdrant is a derived index**. The recovery path is
restore Postgres, then re-index — which is why chunk *text* is stored in Postgres
and not only vectorized.

```bash
# after restoring Postgres:
docker compose exec backend python -c "
from app.db.session import SessionLocal
from app.services import document_service
from app.db.models.content import Document
from app.db.models.enums import DocumentStatus
db = SessionLocal()
docs = db.query(Document).filter(Document.status == DocumentStatus.READY).all()
for d in docs:
    document_service.requeue(db, document=d)
db.commit(); print(f'requeued {len(docs)} documents')"
```

This must be **exercised, not assumed**. An untested restore is a hope.

---

## Routine operations

```bash
make up            # start everything (migrations run first, as a separate job)
make logs          # follow all services
make ps            # service status
make test          # full suite against real infrastructure
make check         # lint + types + tests, what CI runs
make scan          # fail if anything resembling a secret is committed
make down          # stop, keep data
make nuke          # stop and DELETE all volumes
```

**Migrations run as a pre-deploy job**, never at application start — N replicas
booting simultaneously would race Alembic. They must be backward-compatible for one
release (expand → migrate → contract) so a rollback does not meet a schema it
cannot read.

**Scaling.** The two axes are independent by design: chat load scales `backend`, a
bulk import scales `worker`.

```bash
docker compose up -d --scale worker=4
```
