"""Conversation aggregate: conversations, messages, citations, feedback."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey
from app.db.models.enums import FinishReason, MessageRole


def _enum(python_enum: type, name: str) -> Enum:
    return Enum(
        python_enum,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        native_enum=True,
    )


class Conversation(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        # The conversation list is ordered by recency within a workspace.
        Index("ix_conversations_workspace_last", "workspace_id", "last_message_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), default="New conversation", nullable=False)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.created_at",
    )


class Message(UUIDPrimaryKey, TimestampMixin, Base):
    """One turn.

    Carries its own usage and latency figures denormalized for display; the
    authoritative billing record is UsageEvent (§7), which is append-only and
    covers embeddings and titling too.
    """

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(
        _enum(MessageRole, "message_role"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")

    model: Mapped[str | None] = mapped_column(String(120))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0, nullable=False)
    #: Time to first token — the latency users actually perceive (§26.2).
    ttft_ms: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    finish_reason: Mapped[FinishReason | None] = mapped_column(
        _enum(FinishReason, "finish_reason")
    )
    #: 0..1 self-scored support of the answer by its sources (§10). A metric, not a gate.
    groundedness: Mapped[float | None] = mapped_column(Float)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    citations: Mapped[list[Citation]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Citation.marker",
    )
    feedback: Mapped[list[Feedback]] = relationship(
        back_populates="message", cascade="all, delete-orphan", passive_deletes=True
    )


class Citation(UUIDPrimaryKey, Base):
    """A validated link from an answer to the chunk that supports it.

    A real table rather than JSON on the message, so "which documents does this
    team actually rely on" is a GROUP BY instead of a scan (§7).
    """

    __tablename__ = "citations"
    __table_args__ = (
        UniqueConstraint("message_id", "marker", name="uq_citations_message_marker"),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    #: SET NULL, not CASCADE: deleting a document must not silently rewrite
    #: history by removing the citations from answers that were given.
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("chunks.id", ondelete="SET NULL")
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL")
    )
    #: The [n] the model emitted, after validation against the retrieved set.
    marker: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    #: Frozen at answer time so the citation still renders if the chunk is deleted.
    snippet: Mapped[str | None] = mapped_column(Text)
    document_title: Mapped[str | None] = mapped_column(String(500))
    page_label: Mapped[str | None] = mapped_column(String(40))

    message: Mapped[Message] = relationship(back_populates="citations")


class Feedback(UUIDPrimaryKey, TimestampMixin, Base):
    """Thumbs up/down. Feeds the quality metrics and the review queue (§27.5)."""

    __tablename__ = "feedback"
    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_feedback_message_user"),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # -1 or +1
    comment: Mapped[str | None] = mapped_column(Text)

    message: Mapped[Message] = relationship(back_populates="feedback")
