"""Chat DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.db.models.enums import FinishReason, MessageRole
from app.schemas.common import Schema


class ConversationCreate(Schema):
    title: str | None = Field(default=None, max_length=300)


class ConversationOut(Schema):
    id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID
    title: str
    created_at: datetime
    last_message_at: datetime | None
    message_count: int = 0


class CitationOut(Schema):
    marker: int
    chunk_id: uuid.UUID | None
    document_id: uuid.UUID | None
    document_title: str | None
    page_label: str | None
    score: float | None
    snippet: str | None


class MessageOut(Schema):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: MessageRole
    content: str
    model: str | None
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    ttft_ms: int | None
    latency_ms: int | None
    finish_reason: FinishReason | None
    groundedness: float | None
    created_at: datetime
    citations: list[CitationOut] = Field(default_factory=list)


class AskRequest(Schema):
    content: str = Field(min_length=1, max_length=8000)


class FeedbackRequest(Schema):
    rating: int = Field(ge=-1, le=1, description="-1 or +1")
    comment: str | None = Field(default=None, max_length=2000)
