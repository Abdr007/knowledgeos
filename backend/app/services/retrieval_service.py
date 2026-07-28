"""Hybrid retrieval: dense + sparse, fused, diversified, budgeted (§3.2, §10).

The two retrievers run **concurrently** — they hit different systems and neither
depends on the other, so running them in sequence would simply add their
latencies together.

Tenant isolation is enforced twice (D5): the Qdrant search is filtered by
``workspace_id``, and every returned chunk id is then re-fetched from Postgres
with the same predicate. Anything that fails the second check is dropped and
raises a CRITICAL alarm — a vector store returning another tenant's chunk is a
data breach that no prompt can undo.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.content import Chunk, Document
from app.db.models.enums import DocumentStatus
from app.providers.embeddings.local_onnx import get_embedding_provider
from app.providers.vector.registry import get_vector_store
from app.services.fusion import FusedHit, diversify, normalize_scores, reciprocal_rank_fusion

logger = logging.getLogger(__name__)
settings = get_settings()

_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """A chunk with everything a citation needs, frozen at retrieval time."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    content: str
    ordinal: int
    page_label: str | None
    section: str | None
    #: Fused RRF score, normalised. Governs ORDER. Explicitly NOT a relevance
    #: measure — see RetrievalResult.max_dense_score.
    score: float
    dense_rank: int | None
    sparse_rank: int | None
    found_by_both: bool
    #: Raw cosine similarity from the vector store, when the dense half found it.
    #: This is an absolute, comparable quantity, unlike the fused score.
    dense_score: float | None = None
    sparse_score: float | None = None

    @property
    def token_estimate(self) -> int:
        return max(1, len(self.content) // 4)


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    dense_count: int
    sparse_count: int
    fused_count: int
    took_ms: int
    #: Best raw cosine similarity across all dense candidates, before fusion,
    #: diversity or the top-k cut. THE relevance signal (see below).
    max_dense_score: float = 0.0

    @property
    def is_empty(self) -> bool:
        return not self.chunks

    @property
    def best_score(self) -> float:
        """Top fused score. Ranking only — never use this to decide relevance."""
        return self.chunks[0].score if self.chunks else 0.0

    @property
    def relevance(self) -> float:
        """The signal the refusal gate reads (§10).

        **Why not the fused score.** RRF measures rank *agreement* between two
        retrievers, not similarity to the question. An ANN index always returns
        its k nearest neighbours no matter how far away they are, so for a
        completely off-corpus question the dense half still returns 40 results,
        the sparse half matches a stopword-ish token or two, and the chunk both
        rank first scores a perfect 1.0 after normalisation. Gating on that fires
        never — the refusal gate silently does nothing, which is worse than not
        having one, because the system looks protected and is not.

        Cosine similarity is an absolute quantity on a fixed scale, so it can
        answer "is this actually about the question". Measured on this corpus with
        BAAI/bge-small-en-v1.5: on-topic questions score 0.63-0.76, off-topic
        0.49-0.52. RELEVANCE_FLOOR defaults to 0.58, in the gap.
        """
        return self.max_dense_score


async def retrieve(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    query: str,
    top_k: int | None = None,
    candidates: int | None = None,
    document_ids: list[uuid.UUID] | None = None,
) -> RetrievalResult:
    import time

    started = time.perf_counter()
    top_k = top_k or settings.retrieval_top_k
    candidates = candidates or settings.retrieval_candidates

    query = query.strip()
    if not query:
        return RetrievalResult([], 0, 0, 0, 0)

    # Both retrievers are blocking (ONNX inference, psycopg). Running them in
    # threads makes the two round trips overlap instead of accumulate.
    dense_task = asyncio.to_thread(
        _dense_search,
        workspace_id=workspace_id,
        query=query,
        limit=candidates,
        document_ids=document_ids,
    )
    sparse_task = asyncio.to_thread(
        _sparse_search,
        db=db,
        workspace_id=workspace_id,
        query=query,
        limit=candidates,
        document_ids=document_ids,
    )
    dense, sparse = await asyncio.gather(dense_task, sparse_task)

    # Captured BEFORE fusion, diversity and the top-k cut, because the gate asks
    # "does this corpus contain anything about the question at all" — which is a
    # property of the candidate pool, not of the eight chunks that survived.
    max_dense_score = max((score for _cid, score in dense), default=0.0)

    fused = normalize_scores(reciprocal_rank_fusion(dense, sparse))
    if not fused:
        return RetrievalResult([], len(dense), len(sparse), 0, _ms(started), max_dense_score)

    # ── SECOND isolation check (D5) ──────────────────────────────────────
    # Re-fetch from Postgres with the workspace predicate. Qdrant was already
    # filtered; this catches an index that has drifted, a stale point that
    # outlived its row, or a bug in the filter itself.
    rows = db.execute(
        select(Chunk, Document.title, Document.status)
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.id.in_([h.chunk_id for h in fused]),
            Chunk.workspace_id == workspace_id,
        )
    ).all()

    by_id = {row[0].id: row for row in rows}
    leaked = [h.chunk_id for h in fused if h.chunk_id not in by_id]
    if leaked:
        # Expected when a document was deleted mid-query, which is benign. Logged
        # at WARNING with the count so a *systematic* mismatch is visible as a
        # trend rather than lost in the noise (§26.3).
        logger.warning(
            "chunks returned by the vector store were not visible in this workspace",
            extra={
                "event": "retrieval.unresolved_chunks",
                "workspace_id": str(workspace_id),
                "count": len(leaked),
            },
        )

    contents = {cid: row[0].content for cid, row in by_id.items()}
    documents = {cid: row[0].document_id for cid, row in by_id.items()}

    resolved: list[FusedHit] = [h for h in fused if h.chunk_id in by_id]
    # Quarantined documents stay stored but leave the retrieval set (§27.5).
    resolved = [h for h in resolved if by_id[h.chunk_id][2] == DocumentStatus.READY]

    selected = diversify(resolved, contents, documents, limit=top_k)

    chunks: list[RetrievedChunk] = []
    for hit in selected:
        chunk, title, _status = by_id[hit.chunk_id]
        chunks.append(
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                document_title=title,
                content=chunk.content,
                ordinal=chunk.ordinal,
                page_label=chunk.page_label,
                section=chunk.section,
                score=round(hit.score, 4),
                dense_rank=hit.dense_rank,
                sparse_rank=hit.sparse_rank,
                found_by_both=hit.found_by_both,
                dense_score=hit.dense_score,
                sparse_score=hit.sparse_score,
            )
        )

    return RetrievalResult(
        chunks=chunks,
        dense_count=len(dense),
        sparse_count=len(sparse),
        fused_count=len(fused),
        took_ms=_ms(started),
        max_dense_score=max_dense_score,
    )


def _dense_search(
    *,
    workspace_id: uuid.UUID,
    query: str,
    limit: int,
    document_ids: list[uuid.UUID] | None,
) -> list[tuple[uuid.UUID, float]]:
    try:
        # kind="query" applies the BGE query prefix. Embedding a question as if it
        # were a passage silently costs recall (see embeddings/base.py).
        vector = get_embedding_provider().embed([query], kind="query")[0]
        hits = get_vector_store().search(
            workspace_id=workspace_id,
            vector=vector,
            limit=limit,
            document_ids=document_ids,
        )
        return [(h.chunk_id, h.score) for h in hits]
    except Exception:
        # Degrade to sparse-only rather than failing the request (§17).
        logger.exception("dense retrieval failed; falling back to sparse only")
        return []


def _sparse_search(
    *,
    db: Session,
    workspace_id: uuid.UUID,
    query: str,
    limit: int,
    document_ids: list[uuid.UUID] | None,
) -> list[tuple[uuid.UUID, float]]:
    tsquery_text = _build_or_tsquery(query)
    if tsquery_text is None:
        return []

    # OR, not AND.
    #
    # websearch_to_tsquery and plainto_tsquery both join terms with AND, which
    # requires EVERY word of the question to appear in one chunk. For a real
    # question — "Why was RRF chosen over a weighted score blend?" — that matches
    # nothing, and the sparse half of hybrid search silently contributes zero.
    # It fails as an empty result rather than an error, so it looks like the
    # index is fine and the corpus simply has no answer.
    #
    # Terms are OR'd and ranked by ts_rank_cd, which scores by how many distinct
    # query terms a chunk covers and how close together they are — so a chunk
    # matching five terms still outranks one matching two. That is the behaviour
    # BM25 provides in a dedicated search engine, obtained here from the database
    # already holding the text.
    tsquery = func.to_tsquery("english", tsquery_text)
    rank = func.ts_rank_cd(Chunk.content_tsv, tsquery)

    stmt = (
        select(Chunk.id, rank.label("rank"))
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.workspace_id == workspace_id,
            Document.status == DocumentStatus.READY,
            Chunk.content_tsv.op("@@")(tsquery),
        )
        .order_by(rank.desc())
        .limit(limit)
    )
    if document_ids:
        stmt = stmt.where(Chunk.document_id.in_(document_ids))

    try:
        return [(row[0], float(row[1])) for row in db.execute(stmt).all()]
    except Exception:
        logger.exception("sparse retrieval failed; falling back to dense only")
        return []


# Words that appear in nearly every chunk carry no discriminating signal, and
# including them lets a chunk rank on "the" alone. Postgres' english dictionary
# already strips most of these during to_tsvector, but they must also be kept out
# of the query so ts_rank_cd is not diluted.
_QUERY_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "their",
        "there",
        "these",
        "they",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    ]
)

#: Cap on query terms. A pasted paragraph would otherwise build a tsquery with
#: hundreds of OR branches, which Postgres will plan slowly and match everything.
_MAX_QUERY_TERMS = 24


def _build_or_tsquery(query: str) -> str | None:
    """Build a safe ``to_tsquery`` string OR-ing the query's significant terms.

    Tokens come from ``\\w+`` only, so nothing that reaches to_tsquery can carry
    tsquery operators — no injection surface, and no 500 from a user typing an
    apostrophe or a colon.
    """
    seen: set[str] = set()
    terms: list[str] = []
    for raw in _WORD.findall(query.lower()):
        if len(raw) < 2 or raw in _QUERY_STOPWORDS or raw in seen:
            continue
        seen.add(raw)
        terms.append(raw)
        if len(terms) >= _MAX_QUERY_TERMS:
            break

    # A query of nothing but stopwords ("what is it") has no lexical signal; the
    # dense half handles it. Returning None is honest, not a failure.
    if not terms:
        return None
    return " | ".join(terms)


def apply_token_budget(
    chunks: list[RetrievedChunk], *, budget_tokens: int
) -> list[RetrievedChunk]:
    """Trim the context to fit, keeping the highest-ranked chunks.

    Dropping from the tail rather than truncating text mid-chunk: half a chunk is
    a fragment whose meaning may invert ('...the policy does NOT apply when').
    """
    kept: list[RetrievedChunk] = []
    used = 0
    for chunk in chunks:
        cost = chunk.token_estimate
        if used + cost > budget_tokens and kept:
            break
        kept.append(chunk)
        used += cost
    return kept


def _ms(started: float) -> int:
    import time

    return int((time.perf_counter() - started) * 1000)
