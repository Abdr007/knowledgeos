"""Reciprocal Rank Fusion and diversity selection (D4, §3.2).

**Why RRF and not a weighted score blend.** Cosine similarity lives on [-1, 1];
Postgres ``ts_rank`` is unbounded and corpus-dependent. Blending them needs
per-corpus normalisation that drifts as documents are added, and a weight nobody
can justify. RRF throws away magnitudes and uses only *rank position*, so it is
scale-free, needs no tuning, and degrades gracefully when one retriever returns
nothing — a query of pure proper nouns finds nothing dense, a paraphrase finds
nothing lexical, and hybrid covers both.

``k = 60`` is the constant from the original RRF paper. It damps the dominance of
rank 1 enough that a strong second place from the *other* retriever can still win.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

RRF_K = 60


@dataclass(slots=True)
class FusedHit:
    chunk_id: uuid.UUID
    score: float = 0.0
    dense_rank: int | None = None
    sparse_rank: int | None = None
    #: Raw per-retriever scores, kept for the debug surface (§8 /search) rather
    #: than for ranking — they are not comparable, which is the whole point.
    dense_score: float | None = None
    sparse_score: float | None = None
    sources: set[str] = field(default_factory=set)

    @property
    def found_by_both(self) -> bool:
        return self.dense_rank is not None and self.sparse_rank is not None


def reciprocal_rank_fusion(
    dense: list[tuple[uuid.UUID, float]],
    sparse: list[tuple[uuid.UUID, float]],
    *,
    k: int = RRF_K,
) -> list[FusedHit]:
    """Fuse two ranked lists. Inputs must be ordered best-first."""
    merged: dict[uuid.UUID, FusedHit] = {}

    for rank, (chunk_id, score) in enumerate(dense, start=1):
        hit = merged.setdefault(chunk_id, FusedHit(chunk_id=chunk_id))
        hit.dense_rank = rank
        hit.dense_score = score
        hit.score += 1.0 / (k + rank)
        hit.sources.add("dense")

    for rank, (chunk_id, score) in enumerate(sparse, start=1):
        hit = merged.setdefault(chunk_id, FusedHit(chunk_id=chunk_id))
        hit.sparse_rank = rank
        hit.sparse_score = score
        hit.score += 1.0 / (k + rank)
        hit.sources.add("sparse")

    return sorted(merged.values(), key=lambda h: (-h.score, str(h.chunk_id)))


def normalize_scores(hits: list[FusedHit]) -> list[FusedHit]:
    """Rescale fused scores to (0, 1] so a single relevance floor is meaningful.

    Raw RRF scores depend on how many retrievers matched — a chunk found by both
    tops out near 2/(k+1) — which makes an absolute threshold uninterpretable.
    Scaling against the theoretical maximum keeps RELEVANCE_FLOOR a stable
    configuration value rather than one that shifts with corpus size.
    """
    if not hits:
        return hits
    best_possible = 2.0 / (RRF_K + 1)
    for hit in hits:
        hit.score = min(1.0, hit.score / best_possible)
    return hits


def diversify(
    hits: list[FusedHit],
    contents: dict[uuid.UUID, str],
    documents: dict[uuid.UUID, uuid.UUID],
    *,
    limit: int,
    max_per_document: int = 4,
) -> list[FusedHit]:
    """Greedy diversity pass.

    Two failure modes this prevents, both common and both invisible without it:

    1. **One document monopolises the context.** Eight chunks from the same page
       answer one facet of the question exhaustively and leave the rest unanswered.
    2. **Near-duplicate chunks.** Overlap means adjacent chunks share text by
       construction; retrieving both spends context on the same sentences twice.

    A relevance-ordered list is not the same as an informative one.
    """
    selected: list[FusedHit] = []
    per_document: dict[uuid.UUID, int] = {}

    for hit in hits:
        if len(selected) >= limit:
            break
        document_id = documents.get(hit.chunk_id)
        if document_id is not None and per_document.get(document_id, 0) >= max_per_document:
            continue
        text = contents.get(hit.chunk_id, "")
        if any(_too_similar(text, contents.get(s.chunk_id, "")) for s in selected):
            continue
        selected.append(hit)
        if document_id is not None:
            per_document[document_id] = per_document.get(document_id, 0) + 1

    # If diversity filtering left us short, backfill by pure relevance rather
    # than returning fewer sources than the caller asked for.
    if len(selected) < limit:
        chosen = {h.chunk_id for h in selected}
        for hit in hits:
            if len(selected) >= limit:
                break
            if hit.chunk_id not in chosen:
                selected.append(hit)

    return selected[:limit]


def _too_similar(a: str, b: str, *, threshold: float = 0.75) -> bool:
    """Jaccard overlap on word sets.

    Cheap and adequate: this runs on ~40 candidates and only needs to catch the
    overlap the chunker itself created. An embedding-based MMR would cost another
    round of vector maths to answer a question token sets already answer.
    """
    if not a or not b:
        return False
    wa, wb = set(a.lower().split()), set(b.lower().split())
    if not wa or not wb:
        return False
    intersection = len(wa & wb)
    union = len(wa | wb)
    return union > 0 and intersection / union >= threshold
