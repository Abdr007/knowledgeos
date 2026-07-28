# Interview topics, answered

The blueprint lists "Explain why RAG, why vector DB, why FastAPI, why Redis,
retrieval flow, security, scalability, trade-offs, monitoring, deployment
decisions." Those questions are answered here from *this* system, with the real
numbers, rather than in the abstract.

Every answer below is checkable against the code. If an interviewer opens the
repository mid-answer, the claim should hold.

---

## "Walk me through what happens when a user asks a question."

1. `POST /conversations/{id}/messages` — POST, not GET, because a question can
   exceed URL length limits and must not land in proxy access logs.
2. Auth: JWT signature and expiry checked, `typ` claim verified, Redis revocation
   denylist consulted, then the user loaded. Cheap checks first — hitting Postgres
   for a token that fails its signature makes an invalid-token flood a database
   problem.
3. The user's turn is persisted **before** streaming, so a disconnect mid-answer
   still leaves a coherent transcript.
4. Retrieval: the query is embedded (locally, ~15 ms, Redis-cached) and dense +
   sparse searches run **concurrently** via `asyncio.gather`. They hit different
   systems and neither depends on the other, so running them in sequence would
   simply add their latencies. Measured: ~40 ms dense, ~20 ms sparse, ~60 ms total
   rather than ~60 ms serial.
5. Fusion: reciprocal rank fusion, then a diversity pass, then a token budget.
6. **The refusal gate.** If the best raw cosine is below 0.58, the system answers
   "not in this workspace" and *never calls the model*.
7. Prompt assembly: sources wrapped in delimited, HTML-escaped `<source>` blocks.
8. SSE stream: `meta` (sources) → `token`× → `citations` → `usage` → `done`.
9. Citation markers are validated against the retrieved set; unresolvable ones are
   stripped and counted.
10. Persist message, citations and a usage event.

Real numbers from the running system: retrieval 19–120 ms, time-to-first-token
9–115 ms with the offline provider, 8 sources, 3 citations validated, 0 stripped.

---

## "Why RAG rather than fine-tuning?"

Three reasons, in order of how much they matter here:

1. **Freshness.** Company documents change weekly. Fine-tuning bakes knowledge into
   weights; re-training on every document change is absurd. RAG updates by writing
   a row.
2. **Attribution.** A fine-tuned model cannot tell you *where* an answer came from.
   This product's entire value proposition is the chain from a rendered sentence
   back to page 47 of a specific PDF.
3. **Access control.** Weights cannot be filtered by tenant. Retrieval can — every
   query carries a `workspace_id` predicate. A fine-tuned model trained on one
   customer's documents can leak them to another, and there is no fix short of
   retraining.

Fine-tuning is the right tool for *behaviour* — tone, format, task shape. Not for
facts.

---

## "Why a vector database? Why not just Postgres with pgvector?"

pgvector is a legitimate choice and the `VectorStore` protocol means switching is
one module. Qdrant was chosen for three properties:

- **Payload-indexed filtering.** `workspace_id` is an indexed keyword, so a filtered
  ANN search *narrows the graph traversal* rather than post-filtering results. That
  is the difference between filtering working at scale and not.
- **Quantization.** int8 scalar quantization gives ~4× memory reduction for ~1%
  recall — the right trade long before the corpus is large.
- **Workload isolation.** HNSW is memory-hungry. Keeping it off the OLTP database
  means a large index cannot contend with transactional queries.

The cost is one more service to run, back up and reconcile. That is a real cost, and
it is why the reconciliation story is written down: Postgres is the source of truth,
Qdrant is derived, and disaster recovery is "restore Postgres, re-index".

---

## "Why hybrid search? Isn't semantic search enough?"

No, and the failure is asymmetric.

- **Dense retrieval misses exact tokens.** A query for `BRPOPLPUSH` or an invoice
  number or a product SKU has no useful semantic neighbourhood. Embeddings smear it
  into "something about databases".
- **Sparse retrieval misses paraphrase.** "How do we stop one customer seeing
  another's files?" shares almost no vocabulary with a document that says
  "tenant isolation is enforced by a workspace predicate".

Verified on this corpus: the rare-token query is carried entirely by the lexical
half; the paraphrase query is carried by the dense half. Either alone loses one of
them.

**Why RRF and not a weighted blend** — this is the question worth getting right.
Cosine similarity lives on `[-1, 1]`. Postgres `ts_rank` is unbounded and
corpus-dependent. Adding `0.7 × cosine + 0.3 × ts_rank` requires normalising two
incomparable scales, and the normalisation drifts as documents are added. RRF
discards magnitudes entirely and uses only **rank position**: `Σ 1/(k + rank)`.
Scale-free, no tuning, and it degrades gracefully when one retriever returns
nothing. `k = 60` comes from the original paper and damps rank-1 dominance enough
that a strong second place from the *other* retriever can win.

---

## "How do you stop it hallucinating?"

Four mechanisms, and the first is the one that matters:

1. **The refusal gate.** Nothing above the floor → no LLM call at all. A model
   handed zero context still answers, fluently, from parametric memory. You cannot
   prompt that away reliably. Not making the call is the only robust fix.
2. **Verified citations.** Markers are checked against the sources actually
   supplied; a `[7]` when six were given is stripped. An unresolvable citation is
   worse than none — it manufactures the appearance of grounding.
3. **Groundedness scoring**, persisted per message, with the **bottom decile**
   tracked, because a mean hides exactly the answers worth looking at.
4. **Injection containment.** Retrieved text is untrusted data in delimited,
   escaped blocks; the system prompt states source content is never instruction.

### "How did you pick the threshold?"

By measurement, not intuition. Same corpus, same model:

| | top cosine |
|---|---|
| on-topic questions | 0.63 – 0.76 |
| off-corpus questions | 0.49 – 0.52 |
| floor | **0.58**, in the gap |

**And the bug worth mentioning:** the gate originally compared the *fused RRF
score*, and never fired. RRF measures rank agreement, not relevance — and an ANN
index returns its k nearest neighbours however far away they are, so a completely
off-corpus question still produced a top score of 1.0. The control was inert while
appearing to work. It now reads raw cosine, which is absolute and comparable.

---

## "How is multi-tenancy enforced?"

Every row and every vector carries `workspace_id`, and it is enforced **twice**:

1. The Qdrant search **requires** `workspace_id` — there is no overload without it,
   so omitting the tenant filter is a *type error*, not a runtime data breach.
2. Returned chunk ids are re-fetched from Postgres with the same predicate. Anything
   that fails is dropped and logged as a critical signal.

`WorkspaceContext` is the only way a handler can obtain a workspace, and building
one performs the membership check — so a route that forgets to authorize cannot be
written, because it has no other source for the object it needs.

Cross-tenant lookups return **404, not 403**. A 403 confirms the resource exists,
which is an enumeration oracle.

Tested: a second tenant querying the first's workspace, documents, chunks and
analytics gets 404 every time, and two tenants holding byte-identical documents see
strictly their own.

---

## "Why FastAPI?"

Async-native, which matters on exactly two hot paths — LLM streaming and concurrent
dense+sparse retrieval — and Pydantic validation that doubles as the OpenAPI schema,
which is what lets the frontend generate its types from the backend so a field
rename becomes a *compile* error rather than a runtime `undefined`.

**The trade I'd defend:** SQLAlchemy here is *sync*, run in a threadpool. Mixing an
async ORM with a synchronous ONNX embedding call yields the worst of both, and async
SQLAlchemy's ergonomic cost exceeds its return at this scale. The genuinely
I/O-bound paths are `async def`; the DB-bound ones are not.

---

## "Why Redis? What's actually in it?"

Six distinct jobs, and they have different failure requirements:

| Use | TTL | On Redis failure |
|---|---|---|
| Embedding cache | 7 d | fail open — recompute |
| Retrieval cache | 5 min | fail open |
| Rate limiting | window | **fail open** — an outage in the limiter should degrade protection, not availability |
| Ingestion queue | — | work stalls, nothing lost |
| Access-token denylist | until exp | **fail closed** — cannot prove a token is valid, so reject |
| Distributed lock | 10 min | job may double-process; idempotent |

That the denylist fails *closed* while everything else fails *open* is the point:
treating Redis uniformly as "just a cache" would silently un-revoke every revoked
token during an outage.

Redis runs with AOF persistence for the same reason.

---

## "Walk me through authentication."

Short-lived access JWT (30 min) + a **rotating** refresh token (14 d) in an
`httpOnly`, `SameSite=Lax` cookie scoped to `/api/v1/auth`.

- Access token in memory only, never `localStorage` — a script that can read storage
  can exfiltrate the session.
- The refresh cookie is the *only* cookie-authenticated route, so CSRF has no
  surface: every other endpoint needs a `Bearer` header a cross-site form cannot set.
- Only the SHA-256 of the refresh token is stored. A database dump cannot mint
  sessions.
- **Rotation with reuse detection**: every refresh issues a successor and marks the
  old one spent. Presenting a spent token means two parties hold it, so the entire
  *family* is revoked and both are forced to re-authenticate. That turns a stolen
  refresh token from persistent access into a single-use theft that trips an alarm.
- Argon2id, memory-hard, ~100 ms — GPU cracking of a leaked dump costs RAM, not just
  cores.
- Unknown email and wrong password are indistinguishable in status, body **and
  timing** (a dummy hash is verified so the code paths cost the same).

---

## "How would you scale this?"

In the order the bottlenecks actually arrive:

1. **Web tier** — stateless already, no sticky sessions, no in-process state. Add
   replicas.
2. **Ingestion** — scale workers on queue depth. Independent axis by design: a
   100-document import must not degrade chat latency, which is exactly what
   in-request ingestion would cause.
3. **Embedding CPU** — the honest ceiling of local inference. Escape hatches in
   order: more worker replicas → GPU node → `EMBEDDING_PROVIDER=openai`. The
   protocol seam is why the last one is a config change, and why choosing local now
   is safe.
4. **Postgres connections** — bounded pools per process; PgBouncer in transaction
   mode when replica count outgrows the connection budget.
5. **Analytics** — read replica. `usage_events` is append-only and rolls up.
6. **Qdrant memory** — quantization is already on; then shard by hash.

Capacity: ~5–10 chunks/s embedding on CPU, so a 200-page PDF is 1–2 minutes —
acceptable *because* ingestion is asynchronous, and unacceptable if it were not.
Retrieval is ~60 ms; TTFT is provider-dominated. The bottleneck sits at the
provider, which is the correct place for it.

---

## "What would you do differently, or next?"

**Next, in priority order:**

1. **The evaluation harness.** Recall@k, MRR, nDCG, refusal accuracy, citation
   precision, against a golden set built from real documents, gating CI on
   regression. Without it every retrieval change is a guess. It is specified in full
   (TDD §25) and deliberately not claimed as done.
2. **Cross-encoder reranking** — the seam exists; RRF + MMR gets most of the benefit
   at zero added latency, so it was correctly deferred rather than skipped.
3. **OCR**, so scanned PDFs stop being a hard failure.

**What I'd reconsider:** the groundedness score is currently a lexical heuristic,
not a judge model. It is labelled as such wherever it surfaces, but it is a weaker
signal than the dashboard's prominence implies.

**What I'd keep:** the refusal gate, the double isolation check, and running tests
against real infrastructure. Two of the bugs found during this build — an `INET`
column rejecting a non-IP client host, and migrations leaking enum types on
downgrade so the next upgrade failed — are invisible to a mocked test suite and
would have surfaced in production.

---

## Questions I would ask back

- What is the acceptable refusal rate? It is a product decision, not a technical
  one — the floor trades false refusals against confident wrong answers, and where
  it sits should be someone's explicit choice.
- Who owns the corpus quality? Retrieval cannot fix a knowledge base that is
  contradictory or out of date, and the analytics here are designed to make that
  visible rather than to paper over it.
- What is the latency budget? It determines whether reranking is affordable.
