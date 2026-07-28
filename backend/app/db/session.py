"""Database engine and session management.

One engine per process, pooled and bounded. ``get_db`` is the FastAPI dependency
every route uses; it guarantees the session is closed even when a handler raises,
which is what stops a burst of 500s from exhausting the pool and turning an
application error into a database outage.

Transactions are committed by the caller, not here. A dependency that commits on
the way out will happily persist half of a multi-step operation whose later step
failed.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

engine = create_engine(
    settings.sqlalchemy_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    # Recycle below typical proxy and cloud idle timeouts so the pool never
    # hands out a connection the network has already dropped.
    pool_recycle=1800,
    pool_pre_ping=True,
    echo=settings.db_echo,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
)


def get_db() -> Iterator[Session]:
    """Request-scoped session. Always closed, never auto-committed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_database() -> bool:
    """Liveness probe for the readiness endpoint."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("database health check failed")
        return False
