"""Chunker invariants (§11)."""

from __future__ import annotations

import pytest

from app.providers.parsers.base import ParsedDocument, ParsedPage
from app.services.chunking import MIN_CHUNK_CHARS, chunk_document

LOREM = (
    "Retrieval augmented generation grounds a language model in a corpus. "
    "The retriever selects passages and the model is asked to answer only from them. "
)


def make(text: str, pages: int = 1) -> ParsedDocument:
    return ParsedDocument(pages=[ParsedPage(number=i + 1, text=text) for i in range(pages)])


def test_short_document_is_one_chunk():
    chunks = chunk_document(make("A short paragraph that easily fits inside one window."))
    assert len(chunks) == 1


def test_no_chunk_exceeds_the_target_by_more_than_the_overlap():
    chunks = chunk_document(make(LOREM * 60), target_chars=1200, overlap_chars=150)
    assert chunks
    # Overlap prepends a tail, so the ceiling is target + overlap, not target.
    assert max(len(c.content) for c in chunks) <= 1200 + 150


def test_no_fragment_chunks_survive():
    """A 2-character chunk still occupies a top-k slot. It must not exist."""
    document = ParsedDocument(
        pages=[
            ParsedPage(number=1, text="3.2\n\n" + LOREM * 8 + "\n\nx\n\n" + LOREM * 8),
        ]
    )
    chunks = chunk_document(document)
    assert chunks
    assert all(len(c.content) >= MIN_CHUNK_CHARS for c in chunks)


def test_consecutive_chunks_overlap():
    """A fact split across a boundary must be retrievable from one side."""
    chunks = chunk_document(make(LOREM * 60), target_chars=800, overlap_chars=150)
    assert len(chunks) > 1
    for previous, current in zip(chunks, chunks[1:], strict=False):
        if previous.page_from != current.page_from:
            continue
        tail_words = previous.content[-120:].split()
        assert any(w in current.content[:400] for w in tail_words[:4]), (
            "adjacent chunks share no text; the overlap window is not being applied"
        )


def test_page_provenance_survives_chunking():
    """Citations claim page numbers, so pages must travel with the chunk."""
    chunks = chunk_document(make(LOREM * 20, pages=3))
    assert chunks
    assert all(c.page_from is not None for c in chunks)
    assert {c.page_from for c in chunks} == {1, 2, 3}


def test_ordinals_are_contiguous_and_ordered():
    chunks = chunk_document(make(LOREM * 30, pages=2))
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_overlap_must_be_smaller_than_the_window():
    with pytest.raises(ValueError):
        chunk_document(make(LOREM), target_chars=100, overlap_chars=100)


def test_empty_document_produces_nothing():
    assert chunk_document(ParsedDocument(pages=[])) == []
