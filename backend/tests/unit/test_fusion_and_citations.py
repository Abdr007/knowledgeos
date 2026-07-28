"""Rank fusion (D4) and citation validation (D9)."""

from __future__ import annotations

import uuid

from app.services.citation_service import heuristic_groundedness, validate
from app.services.fusion import RRF_K, diversify, normalize_scores, reciprocal_rank_fusion
from app.services.retrieval_service import RetrievedChunk


def cid() -> uuid.UUID:
    return uuid.uuid4()


# ── fusion ───────────────────────────────────────────────────────────────


def test_a_chunk_found_by_both_retrievers_outranks_one_found_by_either():
    both, dense_only, sparse_only = cid(), cid(), cid()
    fused = reciprocal_rank_fusion(
        dense=[(dense_only, 0.99), (both, 0.5)],
        sparse=[(sparse_only, 9.9), (both, 0.1)],
    )
    assert fused[0].chunk_id == both
    assert fused[0].found_by_both


def test_fusion_ignores_score_magnitude_entirely():
    """The reason RRF was chosen: cosine and ts_rank are incomparable scales.

    A sparse score of 900 must not outrank a dense score of 0.9 — only rank
    position may matter.
    """
    a, b = cid(), cid()
    fused = reciprocal_rank_fusion(dense=[(a, 0.0001)], sparse=[(b, 9999.0)])
    assert fused[0].score == fused[1].score


def test_rank_one_from_each_retriever_beats_rank_one_from_only_one():
    a, b = cid(), cid()
    fused = reciprocal_rank_fusion(dense=[(a, 1.0), (b, 0.9)], sparse=[(a, 1.0)])
    assert fused[0].chunk_id == a
    assert fused[0].score == 2 / (RRF_K + 1)


def test_fusion_degrades_gracefully_when_one_retriever_returns_nothing():
    a = cid()
    assert [h.chunk_id for h in reciprocal_rank_fusion(dense=[(a, 0.7)], sparse=[])] == [a]
    assert [h.chunk_id for h in reciprocal_rank_fusion(dense=[], sparse=[(a, 3.1)])] == [a]
    assert reciprocal_rank_fusion([], []) == []


def test_normalisation_is_bounded():
    a = cid()
    hits = normalize_scores(reciprocal_rank_fusion(dense=[(a, 1.0)], sparse=[(a, 1.0)]))
    assert 0 < hits[0].score <= 1.0


# ── diversity ────────────────────────────────────────────────────────────


def test_one_document_cannot_monopolise_the_context():
    doc_a, doc_b = cid(), cid()
    ids = [cid() for _ in range(8)]
    hits = reciprocal_rank_fusion(dense=[(i, 1 - n / 10) for n, i in enumerate(ids)], sparse=[])
    contents = {
        i: f"passage {n} " + " ".join(f"w{n}x{k}" for k in range(12)) for n, i in enumerate(ids)
    }
    # First six belong to one document, last two to another.
    documents = {i: (doc_a if n < 6 else doc_b) for n, i in enumerate(ids)}

    selected = diversify(hits, contents, documents, limit=6, max_per_document=4)
    from_a = sum(1 for h in selected if documents[h.chunk_id] == doc_a)
    assert from_a <= 4
    assert any(documents[h.chunk_id] == doc_b for h in selected)


def test_near_duplicate_chunks_are_collapsed():
    a, b = cid(), cid()
    hits = reciprocal_rank_fusion(dense=[(a, 0.9), (b, 0.8)], sparse=[])
    identical = "the quick brown fox jumps over the lazy dog every single morning"
    selected = diversify(hits, {a: identical, b: identical}, {a: cid(), b: cid()}, limit=2)
    # Backfill may re-add it, but the duplicate must not be preferred over new
    # information when other candidates exist.
    assert selected[0].chunk_id == a


# ── citations ────────────────────────────────────────────────────────────


def chunk(marker: int) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid(),
        document_id=cid(),
        document_title=f"Doc {marker}",
        content=f"Content supporting claim {marker} in detail.",
        ordinal=marker,
        page_label=str(marker),
        section=None,
        score=0.9,
        dense_rank=marker,
        sparse_rank=None,
        found_by_both=False,
    )


def test_valid_markers_are_kept():
    sources = [chunk(1), chunk(2)]
    result = validate("Revenue grew [1]. Costs fell [2].", sources)
    assert result.validated_markers == [1, 2]
    assert result.stripped_markers == []
    assert len(result.citations) == 2


def test_fabricated_markers_are_stripped():
    """A [7] with six sources points at nothing and must not render."""
    result = validate("Revenue grew [7].", [chunk(1)])
    assert result.stripped_markers == [7]
    assert "[7]" not in result.text
    assert result.citations == []


def test_mixed_markers_keep_the_valid_half():
    result = validate("Both things are true [1, 9].", [chunk(1)])
    assert result.validated_markers == [1]
    assert result.stripped_markers == [9]
    assert "[1]" in result.text and "[9]" not in result.text


def test_stripping_does_not_leave_dangling_punctuation():
    assert validate("A claim [4] .", [chunk(1)]).text == "A claim."


def test_ordinary_bracketed_prose_is_untouched():
    text = "The policy [see appendix] applies."
    assert validate(text, [chunk(1)]).text == text


def test_groundedness_separates_supported_from_invented_text():
    sources = [chunk(1)]
    supported = heuristic_groundedness("Content supporting claim detail.", sources)
    invented = heuristic_groundedness(
        "Quarterly dividends increased across European subsidiaries.", sources
    )
    assert supported > invented
    assert 0.0 <= invented <= supported <= 1.0
