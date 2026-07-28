"""Authentication DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr, Field, field_validator

from app.core.config import get_settings
from app.db.models.enums import Role
from app.schemas.common import Schema

settings = get_settings()


class RegisterRequest(Schema):
    email: EmailStr
    #: Length is the only rule enforced. Composition rules ("one uppercase, one
    #: symbol") measurably reduce entropy by pushing users to Password1! —
    #: NIST 800-63B recommends length plus a breach check instead.
    password: str = Field(min_length=12, max_length=200)
    full_name: str = Field(min_length=1, max_length=200)
    #: Optional: registering creates a personal organization when absent.
    organization_name: str | None = Field(default=None, max_length=200)

    @field_validator("password")
    @classmethod
    def _not_trivial(cls, v: str) -> str:
        if len(set(v)) < 5:
            raise ValueError("Password is too repetitive.")
        return v


class LoginRequest(Schema):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(Schema):
    """The access token only.

    The refresh token is never in the body — it is set as an httpOnly cookie so
    XSS cannot read it (§9). A client that could read it would inevitably store
    it somewhere a script can reach.
    """

    access_token: str
    token_type: str = "bearer"  # noqa: S105 — scheme name, not a secret
    expires_in: int = Field(description="Seconds until the access token expires.")


class MembershipSummary(Schema):
    org_id: uuid.UUID
    org_name: str
    org_slug: str
    role: Role


class UserProfile(Schema):
    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    is_superuser: bool
    created_at: datetime
    last_login_at: datetime | None = None


class MeResponse(Schema):
    user: UserProfile
    memberships: list[MembershipSummary]
