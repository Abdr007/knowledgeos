"""Organization and workspace endpoints (§8)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, WsContext, organization_membership
from app.core.errors import ConflictError, NotFoundError
from app.db.models.content import Chunk, Document
from app.db.models.enums import DocumentStatus, Role
from app.db.models.identity import Membership, Organization, User, Workspace
from app.schemas.common import Message
from app.schemas.workspace import (
    MemberOut,
    OrganizationOut,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspaceUpdate,
)
from app.services.auth_service import slugify

router = APIRouter(tags=["workspaces"])

OrgMembership = Annotated[tuple[Organization, Role], Depends(organization_membership)]


# ── organizations ────────────────────────────────────────────────────────


@router.get("/organizations", response_model=list[OrganizationOut], summary="List my organizations")
def list_organizations(user: CurrentUser, db: DbSession) -> list[OrganizationOut]:
    # Counts come from correlated subqueries rather than N+1 follow-up queries;
    # an organization list is rendered on every page load.
    ws_count = (
        select(func.count())
        .select_from(Workspace)
        .where(Workspace.org_id == Organization.id)
        .scalar_subquery()
    )
    member_count = (
        select(func.count())
        .select_from(Membership)
        .where(Membership.org_id == Organization.id)
        .scalar_subquery()
    )
    rows = db.execute(
        select(Organization, Membership.role, ws_count, member_count)
        .join(Membership, Membership.org_id == Organization.id)
        .where(Membership.user_id == user.id)
        .order_by(Organization.created_at)
    ).all()
    return [
        OrganizationOut(
            id=org.id,
            name=org.name,
            slug=org.slug,
            plan=org.plan,
            created_at=org.created_at,
            role=Role(role),
            workspace_count=wc,
            member_count=mc,
        )
        for org, role, wc, mc in rows
    ]


@router.get(
    "/organizations/{org_id}/members",
    response_model=list[MemberOut],
    summary="List organization members",
)
def list_members(membership: OrgMembership, db: DbSession) -> list[MemberOut]:
    organization, _role = membership
    rows = db.execute(
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == organization.id)
        .order_by(Membership.created_at)
    ).all()
    return [
        MemberOut(
            user_id=u.id,
            email=u.email,
            full_name=u.full_name,
            role=Role(m.role),
            joined_at=m.created_at,
        )
        for u, m in rows
    ]


# ── workspaces ───────────────────────────────────────────────────────────


@router.get(
    "/organizations/{org_id}/workspaces",
    response_model=list[WorkspaceOut],
    summary="List workspaces in an organization",
)
def list_workspaces(membership: OrgMembership, db: DbSession) -> list[WorkspaceOut]:
    organization, role = membership

    doc_count = (
        select(func.count())
        .select_from(Document)
        .where(Document.workspace_id == Workspace.id)
        .scalar_subquery()
    )
    ready_count = (
        select(func.count())
        .select_from(Document)
        .where(
            Document.workspace_id == Workspace.id,
            Document.status == DocumentStatus.READY,
        )
        .scalar_subquery()
    )
    chunk_count = (
        select(func.count())
        .select_from(Chunk)
        .where(Chunk.workspace_id == Workspace.id)
        .scalar_subquery()
    )

    rows = db.execute(
        select(Workspace, doc_count, ready_count, chunk_count)
        .where(Workspace.org_id == organization.id)
        .order_by(Workspace.created_at)
    ).all()
    return [
        WorkspaceOut(
            id=w.id,
            org_id=w.org_id,
            name=w.name,
            slug=w.slug,
            description=w.description,
            created_at=w.created_at,
            role=role,
            document_count=dc,
            ready_document_count=rc,
            chunk_count=cc,
        )
        for w, dc, rc, cc in rows
    ]


@router.post(
    "/organizations/{org_id}/workspaces",
    response_model=WorkspaceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a workspace",
)
def create_workspace(
    payload: WorkspaceCreate,
    membership: OrgMembership,
    user: CurrentUser,
    db: DbSession,
) -> WorkspaceOut:
    organization, role = membership
    if not role.satisfies(Role.ADMIN):
        from app.core.errors import AuthorizationError

        raise AuthorizationError("Requires ADMIN role to create a workspace.")

    slug = slugify(payload.name, fallback="workspace")
    exists = db.scalar(
        select(Workspace.id).where(
            Workspace.org_id == organization.id, Workspace.slug == slug
        )
    )
    if exists is not None:
        raise ConflictError(f"A workspace with the slug '{slug}' already exists.")

    workspace = Workspace(
        org_id=organization.id,
        name=payload.name,
        slug=slug,
        description=payload.description,
        created_by=user.id,
    )
    db.add(workspace)
    db.commit()
    db.refresh(workspace)

    return WorkspaceOut(
        id=workspace.id,
        org_id=workspace.org_id,
        name=workspace.name,
        slug=workspace.slug,
        description=workspace.description,
        created_at=workspace.created_at,
        role=role,
    )


@router.get(
    "/workspaces/{workspace_id}",
    response_model=WorkspaceOut,
    summary="Get a workspace",
)
def get_workspace(ctx: WsContext, db: DbSession) -> WorkspaceOut:
    w = ctx.workspace
    counts = db.execute(
        select(
            func.count(Document.id),
            func.count(Document.id).filter(Document.status == DocumentStatus.READY),
        ).where(Document.workspace_id == w.id)
    ).one()
    chunks = db.scalar(select(func.count()).select_from(Chunk).where(Chunk.workspace_id == w.id))
    return WorkspaceOut(
        id=w.id,
        org_id=w.org_id,
        name=w.name,
        slug=w.slug,
        description=w.description,
        created_at=w.created_at,
        role=ctx.role,
        document_count=counts[0],
        ready_document_count=counts[1],
        chunk_count=chunks or 0,
    )


@router.patch(
    "/workspaces/{workspace_id}",
    response_model=WorkspaceOut,
    summary="Update a workspace",
)
def update_workspace(payload: WorkspaceUpdate, ctx: WsContext, db: DbSession) -> WorkspaceOut:
    ctx.require(Role.ADMIN)
    w = ctx.workspace
    if payload.name is not None:
        w.name = payload.name
    if payload.description is not None:
        w.description = payload.description
    db.commit()
    db.refresh(w)
    return WorkspaceOut(
        id=w.id,
        org_id=w.org_id,
        name=w.name,
        slug=w.slug,
        description=w.description,
        created_at=w.created_at,
        role=ctx.role,
    )


@router.delete(
    "/workspaces/{workspace_id}",
    response_model=Message,
    summary="Delete a workspace and everything in it",
)
def delete_workspace(ctx: WsContext, db: DbSession) -> Message:
    ctx.require(Role.ADMIN)
    # Vectors are removed first, then the SQL cascade — a deleted workspace must
    # never be retrievable, not even during the delete (§13).
    from app.providers.vector.qdrant_store import get_vector_store

    get_vector_store().delete_by_workspace(ctx.workspace.id)
    db.delete(ctx.workspace)
    db.commit()
    return Message(detail="Workspace deleted.")


@router.get(
    "/workspaces",
    response_model=list[WorkspaceOut],
    summary="List every workspace the caller can access",
)
def list_all_workspaces(user: CurrentUser, db: DbSession) -> list[WorkspaceOut]:
    """Flat list across organizations — what the workspace switcher renders."""
    rows = db.execute(
        select(Workspace, Membership.role)
        .join(Membership, Membership.org_id == Workspace.org_id)
        .where(Membership.user_id == user.id)
        .order_by(Workspace.created_at)
    ).all()
    return [
        WorkspaceOut(
            id=w.id,
            org_id=w.org_id,
            name=w.name,
            slug=w.slug,
            description=w.description,
            created_at=w.created_at,
            role=Role(role),
        )
        for w, role in rows
    ]


@router.get("/workspaces/{workspace_id}/_probe", include_in_schema=False)
def _probe(ctx: WsContext, workspace_id: Annotated[uuid.UUID, Path()]) -> dict[str, str]:
    """Authorization probe used by the isolation test suite."""
    if ctx.workspace.id != workspace_id:
        raise NotFoundError("Workspace not found.")
    return {"workspace_id": str(ctx.workspace.id), "role": ctx.role.value}
