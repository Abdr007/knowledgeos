"""Refresh-token lifecycle: issue, rotate, detect reuse, revoke (§9).

The security property this module exists to provide: a stolen refresh token
buys one use, not persistent access. Every refresh rotates the token and marks
the old one spent. Presenting a spent token means two parties hold it — the
legitimate client and a thief — so the entire family is revoked and both are
forced to re-authenticate.

Framework-free by design (§6): takes a Session, returns domain values, raises
domain errors. The worker and the test suite use it without an HTTP stack.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.clients import get_redis
from app.core.config import get_settings
from app.core.errors import AuthenticationError
from app.core.security import generate_refresh_token, hash_refresh_token
from app.db.models.identity import RefreshToken

logger = logging.getLogger(__name__)
settings = get_settings()

_DENYLIST_PREFIX = "kos:v1:auth:jti"


@dataclass(frozen=True, slots=True)
class IssuedRefreshToken:
    """The plaintext token (returned to the client once) and its record."""

    token: str
    record: RefreshToken


def issue(
    db: Session,
    *,
    user_id: uuid.UUID,
    family_id: uuid.UUID | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> IssuedRefreshToken:
    """Create a refresh token. A new ``family_id`` starts a new login session."""
    token = generate_refresh_token()
    record = RefreshToken(
        user_id=user_id,
        family_id=family_id or uuid.uuid4(),
        token_hash=hash_refresh_token(token),
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days),
        ip=ip,
        user_agent=(user_agent or "")[:400] or None,
    )
    db.add(record)
    db.flush()
    return IssuedRefreshToken(token=token, record=record)


def rotate(
    db: Session,
    *,
    presented_token: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[uuid.UUID, IssuedRefreshToken]:
    """Exchange a refresh token for its successor.

    Returns ``(user_id, new_token)``. Raises AuthenticationError on an unknown,
    expired, or already-spent token — and in the last case revokes the family
    first, because a replay means the token is in two places at once.
    """
    token_hash = hash_refresh_token(presented_token)
    record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))

    if record is None:
        # Unknown token: either forged, or from a family already purged.
        raise AuthenticationError("Invalid refresh token.")

    now = datetime.now(UTC)

    if record.revoked_at is not None:
        # REUSE DETECTED. The legitimate client rotated this token already, so a
        # second presentation means someone else has a copy. Kill the family.
        logger.critical(
            "refresh token reuse detected — revoking family",
            extra={
                "user_id": str(record.user_id),
                "family_id": str(record.family_id),
                "event": "auth.refresh_reuse_detected",
            },
        )
        revoke_family(db, family_id=record.family_id)
        raise AuthenticationError("Invalid refresh token.")

    if record.expires_at <= now:
        raise AuthenticationError("Refresh token has expired.")

    successor = issue(
        db,
        user_id=record.user_id,
        family_id=record.family_id,
        ip=ip,
        user_agent=user_agent,
    )
    record.revoked_at = now
    record.replaced_by = successor.record.id
    db.flush()
    return record.user_id, successor


def revoke_family(db: Session, *, family_id: uuid.UUID) -> int:
    """Revoke every token in a family. Returns the number newly revoked."""
    result = db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    db.flush()
    return int(result.rowcount or 0)


def revoke_all_for_user(db: Session, *, user_id: uuid.UUID) -> int:
    """Log the user out everywhere. Used on password change and by incident response."""
    result = db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    db.flush()
    return int(result.rowcount or 0)


def revoke_by_token(db: Session, *, presented_token: str) -> None:
    """Logout. Revokes the whole family, not just this token.

    Revoking one token would leave any successor already issued to another device
    in the same family alive, which is not what "log out" means to a user.
    """
    record = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(presented_token))
    )
    if record is not None:
        revoke_family(db, family_id=record.family_id)


# ── access-token denylist ────────────────────────────────────────────────
# Access tokens are stateless and short-lived, so revocation needs somewhere to
# record "this jti is dead" until it would have expired anyway. Redis, with a TTL
# equal to the remaining lifetime, so the entry cleans itself up.


def denylist_access_token(jti: str, *, ttl_seconds: int) -> None:
    if ttl_seconds <= 0:
        return
    try:
        get_redis().setex(f"{_DENYLIST_PREFIX}:{jti}", ttl_seconds, "1")
    except Exception:
        # Fails CLOSED conceptually: we log loudly because a revocation that did
        # not persist is a security event, not a cache miss.
        logger.error("failed to denylist access token", extra={"jti": jti})
        raise


def is_access_token_denied(jti: str) -> bool:
    """True when the token has been explicitly revoked.

    Fails **closed**: if Redis is unreachable we cannot prove the token is still
    valid, and treating unknown as allowed would silently un-revoke every
    revoked token during an outage (§14).
    """
    try:
        return get_redis().exists(f"{_DENYLIST_PREFIX}:{jti}") == 1
    except Exception:
        logger.error("denylist unavailable — rejecting token", extra={"jti": jti})
        return True
