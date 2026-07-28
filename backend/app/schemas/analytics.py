"""Analytics DTOs."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import Field

from app.schemas.common import Schema


class AnalyticsOverview(Schema):
    documents: int
    documents_ready: int
    documents_failed: int
    chunks: int
    conversations: int
    messages: int
    answered: int
    refused: int
    #: How often the corpus did not cover the question. Healthy in moderation —
    #: a refusal rate of zero usually means the gate is not working (§26.3).
    refusal_rate: float
    latency_p50_ms: int | None
    latency_p95_ms: int | None
    ttft_p50_ms: int | None
    avg_groundedness: float | None
    total_cost_usd: float
    input_tokens: int
    output_tokens: int
    cost_per_answer_usd: float


class UsagePoint(Schema):
    date: date
    input_tokens: int
    output_tokens: int
    cost_usd: float
    calls: int


class QualityMetrics(Schema):
    feedback_count: int
    positive_feedback: int
    satisfaction_rate: float | None
    groundedness_p10: float | None = Field(
        default=None, description="Bottom decile — where fabrication hides."
    )
    groundedness_p50: float | None
    total_citations: int
    citations_per_answer: float


class DocumentLeaderboardEntry(Schema):
    document_id: uuid.UUID
    title: str
    chunks: int
    citations: int


class SystemStatus(Schema):
    environment: str
    llm_provider: str
    chat_model: str
    llm_configured: bool
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    vector_count: int
    queue_pending: int
    queue_processing: int
    relevance_floor: float
    retrieval_top_k: int
    chunk_size_chars: int
    chunk_overlap_chars: int
