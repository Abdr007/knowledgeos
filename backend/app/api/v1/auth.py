"""Authentication endpoints (§8, §9)."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, Response, status

from app.api.deps import CurrentUser, DbSession, client_ip
from app.core.config import get_settings
from app.core.errors import AuthenticationError
from app.core.rate_limit import check_rate_limit
from app.core.security import create_access_token
from app.db.models.enums import Role
from app.schemas.auth import (
    LoginRequest,
    MembershipSummary,
    MeResponse,
    RegisterRequest,
    TokenResponse,
    UserProfile,
)
from app.schemas.common import Message
from app.services import auth_service, token_service

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "kos_refresh"
#: Scoped to the refresh endpoint only. The browser then sends this cookie on
#: exactly one route, so no other endpoint is cookie-authenticated and CSRF has
#: no surface to attack — every other route needs a Bearer header, which a
#: cross-site form cannot set.
REFRESH_COOKIE_PATH = "/api/v1/auth"


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        max_age=settings.refresh_token_ttl_days * 24 * 3600,
        path=REFRESH_COOKIE_PATH,
        httponly=True,  # unreadable from JavaScript, so XSS cannot exfiltrate it
        secure=settings.is_production,  # http is required for localhost development
        samesite="lax",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)


def _token_response(user_id, response: Response) -> TokenResponse:
    access_token, _jti, _exp = create_access_token(user_id)
    return TokenResponse(
        access_token=access_token,
        expires_in=settings.access_token_ttl_minutes * 60,
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account, its organization and a default workspace",
)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: DbSession,
) -> TokenResponse:
    ip = client_ip(request)
    check_rate_limit(ip or "anonymous", action="register", limit=settings.rate_limit_auth_per_minute)

    result = auth_service.register(
        db,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        organization_name=payload.organization_name,
    )
    issued = token_service.issue(
        db,
        user_id=result.user.id,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
    )
    # The route owns the transaction boundary; services only flush (§6).
    db.commit()

    _set_refresh_cookie(response, issued.token)
    return _token_response(result.user.id, response)


@router.post("/login", response_model=TokenResponse, summary="Exchange credentials for tokens")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession,
) -> TokenResponse:
    ip = client_ip(request)
    # Limited per IP *and* per email: per-IP alone lets a botnet spray one
    # account, per-email alone lets one host walk a user list.
    check_rate_limit(ip or "anonymous", action="login-ip", limit=settings.rate_limit_auth_per_minute)
    check_rate_limit(
        auth_service.normalize_email(payload.email),
        action="login-email",
        limit=settings.rate_limit_auth_per_minute,
    )

    user = auth_service.authenticate(db, email=payload.email, password=payload.password)
    issued = token_service.issue(
        db,
        user_id=user.id,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()

    logger.info("login", extra={"event": "auth.login", "user_id": str(user.id)})
    _set_refresh_cookie(response, issued.token)
    return _token_response(user.id, response)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate the refresh token and mint a new access token",
)
def refresh(
    request: Request,
    response: Response,
    db: DbSession,
    kos_refresh: Annotated[str | None, Cookie()] = None,
) -> TokenResponse:
    """Rotation with reuse detection.

    Presenting an already-spent token revokes the whole family — see
    token_service.rotate for why that is the correct response rather than simply
    rejecting the request.
    """
    if not kos_refresh:
        raise AuthenticationError("No refresh token supplied.")

    try:
        user_id, issued = token_service.rotate(
            db,
            presented_token=kos_refresh,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except AuthenticationError:
        # Commit first: rotate() may have revoked a family on reuse detection,
        # and that revocation must persist even though the request fails.
        db.commit()
        _clear_refresh_cookie(response)
        raise

    db.commit()
    _set_refresh_cookie(response, issued.token)
    return _token_response(user_id, response)


@router.post("/logout", response_model=Message, summary="Revoke this session")
def logout(
    response: Response,
    db: DbSession,
    kos_refresh: Annotated[str | None, Cookie()] = None,
) -> Message:
    """Revokes the refresh family and clears the cookie.

    Deliberately does not require a valid access token: a user whose access token
    has already expired must still be able to log out.
    """
    if kos_refresh:
        token_service.revoke_by_token(db, presented_token=kos_refresh)
        db.commit()
    _clear_refresh_cookie(response)
    return Message(detail="Signed out.")


@router.get("/me", response_model=MeResponse, summary="Current user and memberships")
def me(user: CurrentUser, db: DbSession) -> MeResponse:
    memberships = auth_service.load_memberships(db, user_id=user.id)
    return MeResponse(
        user=UserProfile.model_validate(user),
        memberships=[
            MembershipSummary(
                org_id=org.id,
                org_name=org.name,
                org_slug=org.slug,
                role=Role(m.role),
            )
            for m, org in memberships
        ],
    )
