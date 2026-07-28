# Decision log

Append-only. Each entry records what was decided, what it cost, and what would
make us revisit it. Decisions D1–D10 are argued in full in [`TDD.md`](TDD.md) §0;
this file records the ones taken *during* implementation, plus amendments.

---

## ADR-001 — Model files grouped by aggregate, not one per entity

**Status:** accepted · **Amends:** TDD §4

TDD §4 lists fourteen model modules, one per entity. Implementation grouped them
into four along aggregate boundaries: `identity`, `content`, `conversation`, `ops`.

**Why.** These entities form mutually-referencing clusters. Splitting a cluster
across modules in SQLAlchemy produces import cycles, which are then worked around
with string-literal relationship targets and deferred imports — the same coupling,
made invisible. The domain decomposition is unchanged; only the file count is.

**Cost.** Four files are longer. No change to the ER model, the API, or any layer
boundary.

---

## ADR-002 — The refusal gate reads raw cosine, never the fused score

**Status:** accepted · **Supersedes:** the original reading of TDD §10

The gate originally compared the normalised RRF score against `RELEVANCE_FLOOR`.
**It never fired.**

**Why it failed.** RRF measures *rank agreement between retrievers*, not similarity
to the question. An ANN index always returns its k nearest neighbours however far
away they are, so for a completely off-corpus question the dense half still returns
40 results, the sparse half matches a token or two, and the chunk both rank first
normalises to a perfect 1.0. The headline anti-hallucination control was inert
while appearing to work — and it failed *silently*, as a plausible answer rather
than an error.

**Resolution.** The gate compares the best raw cosine similarity, which is absolute
and comparable across queries. The threshold was set by measurement on this corpus
with `BAAI/bge-small-en-v1.5`:

| | top cosine |
|---|---|
| on-topic | 0.63 – 0.76 |
| off-corpus | 0.49 – 0.52 |
| **floor** | **0.58** |

**Revisit when:** the embedding model changes. Score distributions are
model-specific, so the floor must be re-measured, not carried over. This is part of
the §29.3 migration checklist.

---

## ADR-003 — Sparse retrieval ORs its terms

**Status:** accepted

`websearch_to_tsquery` and `plainto_tsquery` both join terms with **AND**, which
requires every word of a natural-language question to appear in one chunk. In
practice that matched nothing, so hybrid search was silently running dense-only
while reporting healthy.

Terms are now OR'd and ranked by `ts_rank_cd`, which scores by how many distinct
query terms a chunk covers and how close together they are — the behaviour BM25
provides in a dedicated search engine, obtained from the database already holding
the text. Query tokens come from `\w+` only, so nothing reaching `to_tsquery` can
carry operators.

---

## ADR-004 — `ScriptedProvider` ships in the application, not the test suite

**Status:** accepted · **Extends:** D1

A third `LLMProvider` implementation lives in `app/providers/llm/`: deterministic,
offline, extractive.

**Why in the application rather than under `tests/`.** It serves two purposes that
both need it importable at runtime — a reproducible test fixture, *and* a working
demo before a live credential exists. Combined with local embeddings (D2), it means
every milestone was verifiable on the day it was written rather than blocked on a
vendor key.

**Honesty constraint.** It is extractive, not generative. The interface labels
responses produced this way. Presenting extraction as generation would be a lie to
whoever is reading the demo, so the label is not optional.

---

## ADR-005 — Query prefixes are keyed by embedding model

**Status:** accepted

The BGE instruction prefix was hardcoded. BGE expects a prefix on queries only; E5
expects `query:`/`passage:` on both sides; multilingual MiniLM paraphrase models
expect none. Applying the wrong one prepends noise to every query and costs recall
**while everything still appears to work**.

Prefixes now live in a map keyed by model name, so changing `EMBEDDING_MODEL`
cannot silently apply the wrong convention.

---

## ADR-006 — Arabic support deferred

**Status:** accepted · **Relates to:** TDD §29.3

Multilingual retrieval was implemented and then reverted at the project owner's
direction. Recorded because the finding is worth keeping:

- **TDD §29.3 named a model that does not exist in fastembed.** `multilingual-e5-small`
  is not available; fastembed's multilingual E5 is 1024-d and 2.24 GB. The real
  384-d drop-in — same width, so no Qdrant change — is
  `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`.
- **Postgres ships an Arabic snowball stemmer.** `الإجازات → اجاز` is correct root
  stemming, materially better than the `simple` fallback. A bilingual corpus wants
  two generated `tsvector` columns OR'd together, since one column carries one
  stemmer.

**Cost of deferring:** a re-embed of the corpus, and nothing else. It stays cheap
until real documents are loaded.

---

## ADR-007 — Tests run against real infrastructure

**Status:** accepted

The suite talks to real Postgres, Redis and Qdrant. Only the LLM is substituted.

**Why not mocks.** Mocking the database means never exercising the generated
`tsvector` column, the `ON DELETE` rules, or the unique constraints — precisely the
things most likely to be wrong, and the things that fail in production rather than
in CI. Two of the bugs found during the build (the INET column rejecting a non-IP
client host, and the migration leaking enum types on downgrade) are invisible to a
mocked suite.

**Cost.** CI needs service containers, and the suite is slower than a unit-only
one. Accepted.

---

## ADR-008 — Warnings are errors, exemptions are individual

**Status:** accepted

`filterwarnings = ["error"]` and a strict ruff rule set. Where a third-party
deprecation cannot be resolved yet (Starlette's TestClient wanting `httpx2`, which
both LLM SDKs conflict with), the exemption is narrow, dated by its comment, and
explains what would let it be removed — rather than a blanket ignore that quietly
grows.
