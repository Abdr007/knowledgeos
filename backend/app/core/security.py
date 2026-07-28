"""Password hashing and JWT issuance/verification (§9).

Two token types share a signing key and are told apart by a verified ``typ``
claim. Skipping that check is the classic mistake: without it a refresh token is
a valid access token, and the 14-day refresh lifetime silently becomes the
session lifetime.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings
from app.core.errors import AuthenticationError

logger = logging.getLogger(__name__)
settings = get_settings()

TokenType = Literal["access", "refresh"]

# Argon2id. Memory-hard, so cracking a leaked dump costs GPU RAM rather than
# GPU cores — the property bcrypt lacks. Tuned to roughly 100 ms on the target
# hardware: expensive in bulk, imperceptible to a user logging in once.
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)

# Verified against on unknown-email logins so a failed lookup costs the same as a
# failed password. Without it, response timing enumerates registered accounts.
_DUMMY_HASH = _hasher.hash("dummy-password-for-constant-time-comparison")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Constant-time-ish verification that never reveals whether the user exists."""
    if password_hash is None:
        # Burn the same CPU as a real verification so a missing account and a
        # wrong password take the same wall-clock time. The dummy comparison
        # always mismatches, so its exception is expected and swallowed —
        # letting it escape would turn "unknown email" into a 500 and give the
        # timing oracle back as a status-code oracle.
        with contextlib.suppress(VerifyMismatchError, VerificationError, InvalidHashError):
            _hasher.verify(_DUMMY_HASH, "wrong")
        return False
    try:
        _hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash predates the current cost parameters."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def create_access_token(
    subject: uuid.UUID | str,
    *,
    extra_claims: dict[str, Any] | None = None,
) -> tuple[str, str, datetime]:
    """Return ``(token, jti, expires_at)``.

    The ``jti`` is returned so the caller can denylist this exact token on logout
    without waiting for it to expire (§14).
    """
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.access_token_ttl_minutes)
    jti = uuid.uuid4().hex
    payload: dict[str, Any] = {
        "sub": str(subject),
        "typ": "access",
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, jti, expires_at


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate, or raise AuthenticationError.

    Signature, expiry and the ``typ`` claim are all checked. The error message is
    deliberately generic — telling a caller precisely why a token failed helps an
    attacker more than a legitimate client.
    """
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "typ", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid token.") from exc

    if payload.get("typ") != "access":
        # A refresh token presented as a bearer credential.
        raise AuthenticationError("Invalid token.")
    return payload


def generate_refresh_token() -> str:
    """A high-entropy opaque string. Not a JWT: it carries no claims, and the
    database row is the authority on whether it is still valid."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """SHA-256 of the token.

    Only this is stored (§9), so a database dump cannot mint sessions. Plain
    SHA-256 rather than Argon2 is correct here: the input is 384 bits of
    cryptographic randomness, so there is no dictionary to attack, and refresh
    happens often enough that a 100 ms hash would be a real latency cost.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
