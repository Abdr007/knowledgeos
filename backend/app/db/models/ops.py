"""Operations aggregate: usage accounting and the audit trail.

Both tables are append-only. Nothing in the application updates or deletes a row
here — an audit record that can be edited by the system it audits is not evidence.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey
from app.db.models.enums import UsageKind


class UsageEvent(UUIDPrimaryKey, Base):
    """One billable provider call — chat, embedding, titling or judging.

    The single source of truth for cost and latency analytics (§11). Recording
    embeddings here as well as chat is what makes the reported cost per document
    and per query actually reconcile against the provider's invoice, rather than
    counting only the visible half.
    """

    __tablename__ = "usage_events"
    __table_args__ = (
        Index("ix_usage_events_org_created", "org_id", "created_at"),
        Index("ix_usage_events_workspace_created", "workspace_id", "created_at"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    #: Nullable: embedding work during ingestion is workspace-scoped, but some
    #: org-level operations have no workspace.
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    kind: Mapped[UsageKind] = mapped_column(
        Enum(UsageKind, name="usage_kind", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Computed at write time from the model's price table. Stored rather than
    #: derived on read because prices change, and a historical cost must not be
    #: silently restated by a price update.
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class AuditEvent(UUIDPrimaryKey, Base):
    """Security-relevant actions: who did what, to what, from where (§28.7)."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_org_created", "org_id", "created_at"),
        Index("ix_audit_events_action", "action"),
    )

    #: Nullable so failed logins — where there is no established org — are still recorded.
    org_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(80))
    target_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: Structured detail. Never contains credentials or document text (§16 redaction).
    detail: Mapped[dict | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
