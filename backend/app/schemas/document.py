"""Document DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field, HttpUrl

from app.db.models.enums import DocumentStatus, JobStatus, SourceType
from app.schemas.common import Schema


class DocumentOut(Schema):
    id: uuid.UUID
    workspace_id: uuid.UUID
    title: str
    source_type: SourceType
    source_uri: str | None
    mime_type: str | None
    byte_size: int | None
    status: DocumentStatus
    error_message: str | None
    page_count: int | None
    chunk_count: int
    token_count: int
    created_at: datetime
    processed_at: datetime | None
    uploaded_by: uuid.UUID


class UrlIngestRequest(Schema):
    url: HttpUrl
    title: str | None = Field(default=None, max_length=500)


class ChunkOut(Schema):
    """What retrieval actually sees.

    Exposed deliberately: the fastest way to debug a bad answer is to look at the
    text the model was given, not to re-read the prompt (§8).
    """

    id: uuid.UUID
    ordinal: int
    content: str
    token_count: int
    page_from: int | None
    page_to: int | None
    section: str | None


class JobOut(Schema):
    id: uuid.UUID
    document_id: uuid.UUID
    status: JobStatus
    attempts: int
    last_error: str | None
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class UploadAccepted(Schema):
    """202 body.

    'Accepted', not 'created': the document has been stored and queued, and is
    not yet searchable. Saying otherwise would make the client poll for a state
    it was told already existed.
    """

    document: DocumentOut
    duplicate: bool = Field(
        default=False,
        description="True when this file was already present; ingestion was skipped.",
    )
