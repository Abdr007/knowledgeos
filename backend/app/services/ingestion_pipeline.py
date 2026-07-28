"""Document ingestion: parse → chunk → embed → index (§11).

Runs in the worker, never in a request. Framework-free, so the same function is
callable from a test, a CLI or a future scheduler.

Ordering is deliberate: **Qdrant is written before the document is marked READY**,
so a READY document is always searchable. Deletion runs the other way — vectors
first, then SQL — so a deleted document is never retrievable, not even mid-delete
(§13).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.ids import uuid7
from app.db.models.content import Chunk, Document, IngestionJob
from app.db.models.enums import DocumentStatus, JobStatus, UsageKind
from app.db.models.identity import Workspace
from app.providers.embeddings.local_onnx import get_embedding_provider
from app.providers.parsers.formats import get_parser
from app.providers.storage.local_disk import get_storage
from app.providers.vector.base import VectorRecord
from app.providers.vector.registry import get_vector_store
from app.services.chunking import chunk_document
from app.services.usage_recorder import record_usage

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass(frozen=True, slots=True)
class IngestionResult:
    document_id: uuid.UUID
    chunks: int
    pages: int
    tokens: int
    duration_ms: int


class IngestionError(Exception):
    """Parsing or indexing failed for a reason worth showing an operator."""


def process_document(
    db: Session, *, document_id: uuid.UUID, job_id: uuid.UUID | None = None
) -> IngestionResult:
    """Run the full pipeline for one document. Commits its own state changes.

    State transitions are committed as they happen rather than at the end, so a
    document visibly moves PENDING → PROCESSING → READY in the UI while the work
    is in flight, instead of appearing stuck and then jumping.
    """
    started = datetime.now(UTC)

    document = db.get(Document, document_id)
    if document is None:
        raise IngestionError(f"Document {document_id} no longer exists.")

    job = db.get(IngestionJob, job_id) if job_id else None

    document.status = DocumentStatus.PROCESSING
    document.error_message = None
    if job is not None:
        job.status = JobStatus.RUNNING
        job.started_at = started
        job.attempts += 1
    db.commit()

    try:
        result = _run(db, document)
    except Exception as exc:
        db.rollback()
        # Re-fetch: the rollback detached the objects loaded above.
        document = db.get(Document, document_id)
        if document is not None:
            document.status = DocumentStatus.FAILED
            document.error_message = str(exc)[:2000]
        if job_id:
            job = db.get(IngestionJob, job_id)
            if job is not None:
                job.status = JobStatus.FAILED
                job.last_error = str(exc)[:2000]
                job.finished_at = datetime.now(UTC)
        db.commit()
        logger.exception(
            "ingestion failed",
            extra={"event": "ingest.failed", "document_id": str(document_id)},
        )
        raise

    if job_id:
        job = db.get(IngestionJob, job_id)
        if job is not None:
            job.status = JobStatus.SUCCEEDED
            job.finished_at = datetime.now(UTC)
    db.commit()

    logger.info(
        "document ingested",
        extra={
            "event": "document.ingested",
            "document_id": str(document_id),
            "chunks": result.chunks,
            "pages": result.pages,
            "tokens": result.tokens,
            "duration_ms": result.duration_ms,
        },
    )
    return result


def _run(db: Session, document: Document) -> IngestionResult:
    started = datetime.now(UTC)

    if not document.storage_key:
        raise IngestionError("Document has no stored content.")

    raw = get_storage().get(document.storage_key)

    # ── parse ────────────────────────────────────────────────────────────
    parser = get_parser(document.source_type)
    parsed = parser.parse(raw, filename=document.source_uri)

    if parsed.is_empty:
        # Overwhelmingly a scanned PDF: an image of text, with no text layer.
        # Failing loudly beats a "successful" document that never appears in an
        # answer and gives no clue why.
        raise IngestionError(
            "No extractable text found. Scanned documents need OCR, which this "
            "build does not perform."
        )

    if parsed.title and document.title.startswith("Untitled"):
        document.title = parsed.title[:500]

    # ── chunk ────────────────────────────────────────────────────────────
    chunks = chunk_document(parsed)
    if not chunks:
        raise IngestionError("Document produced no chunks.")

    # ── replace any previous version ─────────────────────────────────────
    # Reprocessing must not double the corpus. Vectors go first so no window
    # exists where a stale vector resolves to a deleted row.
    store = get_vector_store()
    store.ensure_collection()
    store.delete_by_document(document.id)
    db.execute(delete(Chunk).where(Chunk.document_id == document.id))
    db.flush()

    # ── embed ────────────────────────────────────────────────────────────
    embedder = get_embedding_provider()
    embed_started = datetime.now(UTC)
    vectors: list[list[float]] = []
    batch_size = settings.embedding_batch_size
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        vectors.extend(embedder.embed([c.content for c in batch], kind="document"))
    embed_ms = int((datetime.now(UTC) - embed_started).total_seconds() * 1000)

    # ── persist ──────────────────────────────────────────────────────────
    rows: list[Chunk] = []
    records: list[VectorRecord] = []
    total_tokens = 0

    for text_chunk, _vector in zip(chunks, vectors, strict=True):
        vector_id = uuid7()
        row = Chunk(
            document_id=document.id,
            workspace_id=document.workspace_id,
            ordinal=text_chunk.ordinal,
            content=text_chunk.content,
            token_count=text_chunk.token_estimate,
            page_from=text_chunk.page_from,
            page_to=text_chunk.page_to,
            section=text_chunk.section,
            vector_id=vector_id,
        )
        rows.append(row)
        total_tokens += text_chunk.token_estimate

    db.add_all(rows)
    db.flush()  # assigns chunk ids, which the vector payload needs

    for row, vector in zip(rows, vectors, strict=True):
        assert row.vector_id is not None  # set when the row was constructed
        records.append(
            VectorRecord(
                vector_id=row.vector_id,
                chunk_id=row.id,
                document_id=document.id,
                workspace_id=document.workspace_id,
                vector=vector,
                ordinal=row.ordinal,
                page_from=row.page_from,
                page_to=row.page_to,
            )
        )

    # Commit the chunk rows BEFORE indexing them.
    #
    # Not cosmetic. A VectorStore may run in its own database session — the
    # pgvector backend does, because the protocol hands it records, not a
    # Session. Rows that have only been flushed are invisible to that session,
    # so its UPDATE matches nothing. And an UPDATE that matches nothing is not
    # an error: ingestion reports success, the document is marked READY, and
    # every later query returns zero dense candidates. The gate then refuses
    # questions the corpus does answer, which looks like bad retrieval rather
    # than a missing write.
    #
    # Committing here is safe: the document is still PROCESSING, so nothing is
    # retrievable yet. If the upsert fails after this point the chunks exist
    # without vectors, the document never reaches READY, and reprocessing fixes
    # it — the same failure mode the external-index path already had.
    db.commit()

    # Index BEFORE marking READY: a READY document must be searchable.
    store.upsert(records)

    document.status = DocumentStatus.READY
    document.chunk_count = len(rows)
    document.token_count = total_tokens
    document.page_count = parsed.page_count
    document.processed_at = datetime.now(UTC)

    # Embedding is a real cost even when it is CPU rather than a vendor invoice;
    # recording it is what makes cost-per-document honest (§7).
    workspace = db.get(Workspace, document.workspace_id)
    if workspace is not None:
        record_usage(
            db,
            org_id=workspace.org_id,
            workspace_id=workspace.id,
            user_id=document.uploaded_by,
            kind=UsageKind.EMBED,
            model=embedder.model_name,
            input_tokens=total_tokens,
            output_tokens=0,
            latency_ms=embed_ms,
        )

    duration_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
    return IngestionResult(
        document_id=document.id,
        chunks=len(rows),
        pages=parsed.page_count,
        tokens=total_tokens,
        duration_ms=duration_ms,
    )


def delete_document(db: Session, *, document: Document) -> None:
    """Remove a document everywhere. Vectors first (§13)."""
    get_vector_store().delete_by_document(document.id)
    db.delete(document)
    db.flush()


def pending_document_ids(db: Session, limit: int = 100) -> list[uuid.UUID]:
    return list(
        db.scalars(
            select(Document.id)
            .where(Document.status == DocumentStatus.PENDING)
            .order_by(Document.created_at)
            .limit(limit)
        )
    )
