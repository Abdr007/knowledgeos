"""Search DTOs — the retrieval debug surface (§8)."""

from __future__ import annotations

import uuid

from pydantic import Field

from app.schemas.common import Schema


class SearchRequest(Schema):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=50)
    document_ids: list[uuid.UUID] | None = None


class SearchHit(Schema):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    content: str
    page_label: str | None
    section: str | None
    score: float
    #: Which retriever found it, and at what rank. Exposed because "why did this
    #: chunk win" is the first question when an answer is wrong, and the answer
    #: is usually visible right here.
    dense_rank: int | None
    sparse_rank: int | None
    found_by_both: bool


class SearchResponse(Schema):
    query: str
    hits: list[SearchHit]
    dense_candidates: int
    sparse_candidates: int
    fused_candidates: int
    took_ms: int
