"""Document intake: validate, deduplicate, store, enqueue (§11)."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import PayloadTooLargeError, ValidationError
from app.db.models.content import Document, IngestionJob
from app.db.models.enums import DocumentStatus, SourceType
from app.providers.parsers.formats import source_type_for
from app.providers.storage.local_disk import get_storage
from app.services import queue

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass(frozen=True, slots=True)
class IntakeResult:
    document: Document
    duplicate: bool


def _safe_title(filename: str | None, fallback: str = "Untitled document") -> str:
    """Display title from a filename.

    Path separators are stripped because the value is user-controlled and ends up
    in logs and UI. It is never used to build a filesystem path — storage keys are
    generated (§18).
    """
    if not filename:
        return fallback
    name = filename.replace("\\", "/").split("/")[-1].strip()
    return (name or fallback)[:500]


def intake_bytes(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    uploaded_by: uuid.UUID,
    data: bytes,
    filename: str | None,
    source_type: SourceType | None = None,
    source_uri: str | None = None,
    title: str | None = None,
    mime_type: str | None = None,
) -> IntakeResult:
    """Store a document and queue it for processing.

    Returns the existing document unchanged when the same bytes are already in
    the workspace — re-uploading a file is idempotent rather than a second copy
    of the same content competing with itself in retrieval (§7).
    """
    if not data:
        raise ValidationError("The uploaded file is empty.")
    if len(data) > settings.max_upload_bytes:
        raise PayloadTooLargeError(
            f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB limit."
        )

    # Content decides the format; the extension only disambiguates OOXML (§18).
    resolved_type = source_type or source_type_for(filename, data)

    checksum = hashlib.sha256(data).hexdigest()
    existing = db.scalar(
        select(Document).where(
            Document.workspace_id == workspace_id,
            Document.checksum_sha256 == checksum,
        )
    )
    if existing is not None:
        logger.info(
            "duplicate upload ignored",
            extra={"event": "ingest.duplicate", "document_id": str(existing.id)},
        )
        return IntakeResult(document=existing, duplicate=True)

    document = Document(
        workspace_id=workspace_id,
        uploaded_by=uploaded_by,
        title=title or _safe_title(filename),
        source_type=resolved_type,
        source_uri=source_uri or _safe_title(filename, fallback=""),
        mime_type=mime_type,
        byte_size=len(data),
        checksum_sha256=checksum,
        status=DocumentStatus.PENDING,
    )
    db.add(document)
    db.flush()

    # Key is generated from ids, never from the filename.
    document.storage_key = f"{workspace_id}/{document.id}.bin"
    get_storage().put(document.storage_key, data, content_type=mime_type)

    job = IngestionJob(document_id=document.id)
    db.add(job)
    db.flush()

    # Enqueue after the row exists but before commit would be a lost-update race
    # in the other direction; the caller commits immediately after this returns,
    # and the worker retries a not-yet-visible document rather than dropping it.
    queue.enqueue(queue.IngestJob(document_id=document.id, job_id=job.id))

    return IntakeResult(document=document, duplicate=False)


def requeue(db: Session, *, document: Document) -> IngestionJob:
    """Reprocess an existing document — new chunker, new model, or after a fix."""
    document.status = DocumentStatus.PENDING
    document.error_message = None
    job = IngestionJob(document_id=document.id)
    db.add(job)
    db.flush()
    queue.enqueue(queue.IngestJob(document_id=document.id, job_id=job.id))
    return job
