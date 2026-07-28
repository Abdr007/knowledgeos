"""Content aggregate: documents, chunks, ingestion jobs."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Computed,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey
from app.db.models.enums import DocumentStatus, JobStatus, SourceType


def _enum(python_enum: type, name: str) -> Enum:
    return Enum(
        python_enum,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        native_enum=True,
    )


class Document(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        # Re-uploading a byte-identical file into the same workspace is a no-op
        # rather than a duplicated corpus — the commonest way retrieval quality
        # quietly rots, because duplicates crowd out diverse results.
        UniqueConstraint(
            "workspace_id", "checksum_sha256", name="uq_documents_workspace_checksum"
        ),
        Index("ix_documents_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(
        _enum(SourceType, "source_type"), nullable=False
    )
    #: Original URL for URL ingests; original filename otherwise. Display only —
    #: never used to build a filesystem path (§18 path traversal).
    source_uri: Mapped[str | None] = mapped_column(Text)
    #: Opaque generated key in object storage. Never derived from user input.
    storage_key: Mapped[str | None] = mapped_column(String(300))
    mime_type: Mapped[str | None] = mapped_column(String(150))
    byte_size: Mapped[int | None] = mapped_column(BigInteger)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    status: Mapped[DocumentStatus] = mapped_column(
        _enum(DocumentStatus, "document_status"),
        default=DocumentStatus.PENDING,
        nullable=False,
    )
    #: Operator-facing reason for FAILED. Surfaced in /admin/jobs, never to end users raw.
    error_message: Mapped[str | None] = mapped_column(Text)

    page_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )
    jobs: Mapped[list[IngestionJob]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def is_retrievable(self) -> bool:
        """Quarantined documents stay stored but leave the retrieval set (§27.5)."""
        return self.status is DocumentStatus.READY


class Chunk(UUIDPrimaryKey, Base):
    """A retrievable span of a document.

    Holds the text itself, which is what makes Postgres the source of truth and
    Qdrant a derived index (§13) — and what makes an embedding-model migration
    possible at all, since the corpus can be re-embedded from here (§29.3).
    """

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_ordinal"),
        # The sparse half of hybrid retrieval (D4). GIN over the generated tsvector.
        Index("ix_chunks_content_tsv", "content_tsv", postgresql_using="gin"),
        # Every retrieval filters on workspace first; this index serves that predicate.
        Index("ix_chunks_workspace", "workspace_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    #: Denormalized from documents deliberately: this is the security predicate,
    #: and requiring a join to establish tenancy makes it easy to omit (§7).
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )

    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: GENERATED ALWAYS — the search vector cannot drift from the text, because
    #: it is not independently writable.
    content_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', content)", persisted=True),
        nullable=False,
    )
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    page_from: Mapped[int | None] = mapped_column(Integer)
    page_to: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(400))
    #: Point id in Qdrant. Same value on both sides so identity never has to be mapped.
    vector_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)

    document: Mapped[Document] = relationship(back_populates="chunks")

    @property
    def page_label(self) -> str | None:
        if self.page_from is None:
            return None
        if self.page_to and self.page_to != self.page_from:
            return f"{self.page_from}-{self.page_to}"
        return str(self.page_from)


class IngestionJob(UUIDPrimaryKey, Base):
    """One attempt at processing a document. Retained for the audit trail."""

    __tablename__ = "ingestion_jobs"
    __table_args__ = (Index("ix_ingestion_jobs_status_queued", "status", "queued_at"),)

    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(
        _enum(JobStatus, "job_status"), default=JobStatus.QUEUED, nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[Document] = relationship(back_populates="jobs")
