"""Document endpoints (§8)."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Query, UploadFile, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, WsContext
from app.core.config import get_settings
from app.core.errors import NotFoundError, PayloadTooLargeError
from app.core.rate_limit import check_rate_limit
from app.db.models.content import Chunk, Document, IngestionJob
from app.db.models.enums import DocumentStatus, Role, SourceType
from app.db.models.identity import Membership, Workspace
from app.schemas.common import Message
from app.schemas.document import ChunkOut, DocumentOut, JobOut, UploadAccepted, UrlIngestRequest
from app.services import document_service, ingestion_pipeline, url_fetcher

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(tags=["documents"])


def _load_document(db, user, document_id: uuid.UUID) -> tuple[Document, Role]:
    """Fetch a document only if the caller is a member of its organization.

    Joining through workspace → organization → membership in the same query is
    what makes the tenancy check unforgettable: there is no way to load the
    document without it.
    """
    row = db.execute(
        select(Document, Membership.role)
        .join(Workspace, Workspace.id == Document.workspace_id)
        .join(
            Membership,
            (Membership.org_id == Workspace.org_id) & (Membership.user_id == user.id),
        )
        .where(Document.id == document_id)
    ).first()
    if row is None:
        raise NotFoundError("Document not found.")
    document, role = row
    return document, Role(role)


@router.post(
    "/workspaces/{workspace_id}/documents",
    response_model=UploadAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a document for ingestion",
)
async def upload_document(
    ctx: WsContext,
    db: DbSession,
    file: Annotated[UploadFile, File(description="PDF, DOCX, PPTX, Markdown or text")],
) -> UploadAccepted:
    """202, not 201: the document is accepted and queued, not yet searchable."""
    ctx.require(Role.MEMBER)
    check_rate_limit(
        str(ctx.user.id), action="upload", limit=settings.rate_limit_upload_per_minute
    )

    # Read with a hard cap enforced *while streaming*, so an oversized upload is
    # rejected without first buffering it into memory. Trusting Content-Length
    # would let a lying client OOM the process.
    chunks: list[bytes] = []
    total = 0
    while piece := await file.read(1 << 20):
        total += len(piece)
        if total > settings.max_upload_bytes:
            raise PayloadTooLargeError(
                f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB limit."
            )
        chunks.append(piece)
    data = b"".join(chunks)

    result = document_service.intake_bytes(
        db,
        workspace_id=ctx.workspace.id,
        uploaded_by=ctx.user.id,
        data=data,
        filename=file.filename,
        mime_type=file.content_type,
    )
    db.commit()
    db.refresh(result.document)
    return UploadAccepted(
        document=DocumentOut.model_validate(result.document),
        duplicate=result.duplicate,
    )


@router.post(
    "/workspaces/{workspace_id}/documents/url",
    response_model=UploadAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest a web page by URL",
)
def ingest_url(payload: UrlIngestRequest, ctx: WsContext, db: DbSession) -> UploadAccepted:
    ctx.require(Role.MEMBER)
    check_rate_limit(
        str(ctx.user.id), action="upload", limit=settings.rate_limit_upload_per_minute
    )

    # SSRF-guarded: resolves DNS, rejects private ranges, pins the vetted IP and
    # re-validates every redirect (§18 / url_fetcher).
    fetched = url_fetcher.fetch(str(payload.url))

    source_type = (
        SourceType.PDF if fetched.content_type == "application/pdf" else SourceType.URL
    )
    result = document_service.intake_bytes(
        db,
        workspace_id=ctx.workspace.id,
        uploaded_by=ctx.user.id,
        data=fetched.content,
        filename=None,
        source_type=source_type,
        source_uri=fetched.url,
        title=payload.title or fetched.url,
        mime_type=fetched.content_type,
    )
    db.commit()
    db.refresh(result.document)
    return UploadAccepted(
        document=DocumentOut.model_validate(result.document),
        duplicate=result.duplicate,
    )


@router.get(
    "/workspaces/{workspace_id}/documents",
    response_model=list[DocumentOut],
    summary="List documents in a workspace",
)
def list_documents(
    ctx: WsContext,
    db: DbSession,
    document_status: Annotated[DocumentStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[DocumentOut]:
    stmt = select(Document).where(Document.workspace_id == ctx.workspace.id)
    if document_status is not None:
        stmt = stmt.where(Document.status == document_status)
    rows = db.scalars(stmt.order_by(Document.created_at.desc()).limit(limit)).all()
    return [DocumentOut.model_validate(d) for d in rows]


@router.get("/documents/{document_id}", response_model=DocumentOut, summary="Get a document")
def get_document(document_id: uuid.UUID, user: CurrentUser, db: DbSession) -> DocumentOut:
    document, _role = _load_document(db, user, document_id)
    return DocumentOut.model_validate(document)


@router.get(
    "/documents/{document_id}/chunks",
    response_model=list[ChunkOut],
    summary="Inspect the chunks retrieval sees",
)
def get_chunks(
    document_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[ChunkOut]:
    document, _role = _load_document(db, user, document_id)
    rows = db.scalars(
        select(Chunk)
        .where(Chunk.document_id == document.id)
        .order_by(Chunk.ordinal)
        .limit(limit)
    ).all()
    return [ChunkOut.model_validate(c) for c in rows]


@router.get(
    "/documents/{document_id}/jobs",
    response_model=list[JobOut],
    summary="Ingestion history for a document",
)
def get_jobs(document_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[JobOut]:
    document, _role = _load_document(db, user, document_id)
    rows = db.scalars(
        select(IngestionJob)
        .where(IngestionJob.document_id == document.id)
        .order_by(IngestionJob.queued_at.desc())
    ).all()
    return [JobOut.model_validate(j) for j in rows]


@router.post(
    "/documents/{document_id}/reprocess",
    response_model=DocumentOut,
    summary="Re-run the ingestion pipeline",
)
def reprocess(document_id: uuid.UUID, user: CurrentUser, db: DbSession) -> DocumentOut:
    document, role = _load_document(db, user, document_id)
    if not role.satisfies(Role.ADMIN):
        from app.core.errors import AuthorizationError

        raise AuthorizationError("Requires ADMIN role to reprocess a document.")
    document_service.requeue(db, document=document)
    db.commit()
    db.refresh(document)
    return DocumentOut.model_validate(document)


@router.delete(
    "/documents/{document_id}",
    response_model=Message,
    summary="Delete a document, its chunks and its vectors",
)
def delete_document(document_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Message:
    document, role = _load_document(db, user, document_id)
    if not role.satisfies(Role.ADMIN):
        from app.core.errors import AuthorizationError

        raise AuthorizationError("Requires ADMIN role to delete a document.")
    ingestion_pipeline.delete_document(db, document=document)
    db.commit()
    return Message(detail="Document deleted.")
