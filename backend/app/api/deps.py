"""FastAPI dependencies: authentication, tenancy resolution, authorization.

The design rule here is that **a handler cannot obtain a workspace without also
obtaining the caller's role in it**. `WorkspaceContext` is the only way to reach a
workspace, and building one performs the membership check. A route that forgets
to authorize therefore cannot be written — it has no other source for the object
it needs (§9).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Path, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AuthenticationError, AuthorizationError, NotFoundError
from app.core.security import decode_access_token
from app.db.models.enums import Role
from app.db.models.identity import Membership, Organization, User, Workspace
from app.db.session import get_db
from app.services import token_service

logger = logging.getLogger(__name__)

# auto_error=False so a missing header raises our AuthenticationError with the
# standard error envelope, rather than FastAPI's differently-shaped 403.
_bearer = HTTPBearer(auto_error=False, description="Bearer access token")

DbSession = Annotated[Session, Depends(get_db)]


def client_ip(request: Request) -> str | None:
    """Client address, honouring the proxy header uvicorn validated.

    uvicorn is started with --proxy-headers, so request.client already reflects
    X-Forwarded-For when it came from a trusted proxy. Reading the raw header
    here instead would let any client spoof its own address and defeat per-IP
    rate limiting.
    """
    return request.client.host if request.client else None


def get_current_user(
    request: Request,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    """Resolve the bearer token to an active user.

    Order matters: signature and expiry first (cheap, no I/O), then the
    revocation denylist, then the database. Hitting Postgres for a token that
    fails its signature check would make an invalid-token flood a database load
    problem.
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Authentication required.")

    payload = decode_access_token(credentials.credentials)

    jti = payload["jti"]
    if token_service.is_access_token_denied(jti):
        raise AuthenticationError("Token has been revoked.")

    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, KeyError) as exc:
        raise AuthenticationError("Invalid token.") from exc

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        # The account was deleted or disabled after the token was issued.
        raise AuthenticationError("Account is not available.")

    request.state.user_id = str(user.id)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_superuser(user: CurrentUser) -> User:
    """Platform operator, not a tenant role. Guards /admin/system only."""
    if not user.is_superuser:
        raise AuthorizationError("Requires platform administrator privileges.")
    return user


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    """A workspace the caller is proven to have access to, plus their role."""

    user: User
    workspace: Workspace
    organization: Organization
    role: Role

    def require(self, minimum: Role) -> None:
        if not self.role.satisfies(minimum):
            raise AuthorizationError(
                f"Requires {minimum.value} role in this workspace; you have {self.role.value}."
            )


def workspace_context(
    db: DbSession,
    user: CurrentUser,
    workspace_id: Annotated[uuid.UUID, Path(description="Workspace id")],
) -> WorkspaceContext:
    """Resolve workspace → organization → membership in one query.

    A workspace the caller cannot see raises **NotFoundError, not
    AuthorizationError**: a 403 would confirm the id exists, which is an
    enumeration oracle across tenants (§17).
    """
    row = db.execute(
        select(Workspace, Organization, Membership)
        .join(Organization, Organization.id == Workspace.org_id)
        .join(
            Membership,
            (Membership.org_id == Organization.id) & (Membership.user_id == user.id),
        )
        .where(Workspace.id == workspace_id)
    ).first()

    if row is None:
        raise NotFoundError("Workspace not found.")

    workspace, organization, membership = row
    return WorkspaceContext(
        user=user,
        workspace=workspace,
        organization=organization,
        role=membership.role,
    )


WsContext = Annotated[WorkspaceContext, Depends(workspace_context)]


def require_role(minimum: Role):
    """Dependency factory enforcing a minimum role in the path's workspace.

    Usage:  ``dependencies=[Depends(require_role(Role.ADMIN))]``
    """

    def _dependency(ctx: WsContext) -> WorkspaceContext:
        ctx.require(minimum)
        return ctx

    return _dependency


def organization_membership(
    db: DbSession,
    user: CurrentUser,
    org_id: Annotated[uuid.UUID, Path(description="Organization id")],
) -> tuple[Organization, Role]:
    """Same contract as workspace_context, one level up."""
    row = db.execute(
        select(Organization, Membership)
        .join(Membership, Membership.org_id == Organization.id)
        .where(Organization.id == org_id, Membership.user_id == user.id)
    ).first()
    if row is None:
        raise NotFoundError("Organization not found.")
    organization, membership = row
    return organization, membership.role
