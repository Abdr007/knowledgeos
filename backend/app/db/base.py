"""Declarative base and shared column mixins.

Every table gets a UUIDv7 primary key and creation timestamp from the same place,
so the shape of a row is consistent across the schema and a new model cannot
accidentally invent its own convention.

Naming conventions for constraints are declared here rather than left to
SQLAlchemy's defaults. Without them Alembic generates unnamed constraints, and an
unnamed constraint cannot be dropped in a later migration without hand-written
SQL — a problem that only surfaces months later when you need to reverse one.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.ids import uuid7

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKey:
    """Time-ordered primary key. See app.core.ids for why v7 and not v4."""

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid7
    )


class TimestampMixin:
    """Server-side timestamps.

    ``server_default=func.now()`` rather than a Python default: the database
    clock is the single authority. Application clocks drift between replicas, and
    rows written by a migration or a psql session would otherwise have no
    timestamp at all.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
