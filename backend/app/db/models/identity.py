"""Identity aggregate: users, organizations, membership, workspaces, refresh tokens.

Grouped into one module rather than five because these entities form a single
relationship cluster. Splitting a mutually-referencing cluster across modules in
SQLAlchemy produces import cycles that get papered over with string-literal
relationship targets and deferred imports — the same coupling, made invisible.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey
from app.db.models.enums import Role


def _enum(python_enum: type, name: str) -> Enum:
    return Enum(
        python_enum,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        native_enum=True,
    )


class User(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "users"

    # citext would be ideal; a lowercase-normalising service plus a unique index
    # achieves the same without requiring the extension at every deploy target.
    # Normalisation happens in exactly one place: auth_service.normalize_email.
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: Platform operator, not a tenant role. Grants /admin/system only.
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Organization(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    plan: Mapped[str] = mapped_column(String(40), default="free", nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    workspaces: Mapped[list[Workspace]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class Membership(UUIDPrimaryKey, TimestampMixin, Base):
    """A user's role within an organization. The unit of authorization."""

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "org_id", name="uq_memberships_user_org"),
        Index("ix_memberships_org_role", "org_id", "role"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[Role] = mapped_column(_enum(Role, "role"), nullable=False)

    user: Mapped[User] = relationship(back_populates="memberships")
    organization: Mapped[Organization] = relationship(back_populates="memberships")


class Workspace(UUIDPrimaryKey, TimestampMixin, Base):
    """The tenancy boundary. Every document, chunk and conversation belongs to one."""

    __tablename__ = "workspaces"
    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_workspaces_org_slug"),)

    org_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    organization: Mapped[Organization] = relationship(back_populates="workspaces")


class RefreshToken(UUIDPrimaryKey, TimestampMixin, Base):
    """One issued refresh token.

    Only the SHA-256 of the token is stored, so a database dump cannot be used to
    mint sessions. ``family_id`` is shared by a token and every successor it is
    rotated into; replaying a spent token revokes the whole family at once, which
    is what turns a stolen refresh token from persistent access into a single-use
    theft that trips an alarm (§9).
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_family", "family_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    family_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Set when the token is spent (rotated) or revoked. Non-null means unusable.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(400))

    user: Mapped[User] = relationship(back_populates="refresh_tokens")
