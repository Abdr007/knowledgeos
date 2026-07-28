"""Object storage table (see providers/storage/postgres_store.py)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StoredObject(Base):
    """Original uploaded bytes.

    Keyed by an opaque application-generated string rather than a foreign key to
    documents: storage is a lower-level concern than the document aggregate, and
    the same interface has to be satisfiable by S3, where there are no foreign
    keys.
    """

    __tablename__ = "stored_objects"

    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(150))
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
