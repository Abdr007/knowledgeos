"""Long-lived clients for Redis and Qdrant.

Created once per process and reused. Building a connection per request wastes a
handshake on every call and, under load, exhausts file descriptors before it
exhausts anything interesting.

Both are exposed through health checks so ``/readyz`` can report what is actually
reachable rather than assuming.
"""

from __future__ import annotations

import logging
from functools import lru_cache

import redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def get_redis() -> redis.Redis:
    settings = get_settings()
    return redis.Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=5,
        # Without this a dropped connection surfaces as an application error on
        # the next call rather than being transparently re-established.
        retry_on_timeout=True,
        health_check_interval=30,
    )


@lru_cache
def get_blocking_redis() -> redis.Redis:
    """Separate client for blocking commands (BRPOPLPUSH).

    The shared client sets socket_timeout=5 so a wedged server cannot hang a
    request forever. That same timeout kills a blocking pop, because the socket
    goes quiet for exactly as long as the command is designed to wait — the
    worker then dies with a TimeoutError on every idle poll.

    A blocking command therefore needs its own connection whose socket timeout is
    longer than the block it is asked to perform.
    """
    settings = get_settings()
    return redis.Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=None,  # the command's own timeout is the bound
        health_check_interval=30,
    )


@lru_cache
def get_qdrant():
    """Imported lazily: the SDK costs ~33 MB resident, and a deployment
    configured for pgvector never calls this."""
    from qdrant_client import QdrantClient

    settings = get_settings()
    return QdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key,
        timeout=30,
    )


def check_redis() -> bool:
    try:
        return bool(get_redis().ping())
    except Exception:
        logger.exception("redis health check failed")
        return False


def check_qdrant() -> bool:
    try:
        get_qdrant().get_collections()
        return True
    except Exception:
        logger.exception("qdrant health check failed")
        return False


def check_vector_store() -> bool:
    """Probe the vector backend that is actually CONFIGURED.

    Readiness must reflect the dependencies this deployment has, not a
    hard-coded list. Probing Qdrant while running on pgvector reports `degraded`
    forever — which is worse than having no probe at all, because a readiness
    endpoint that is always failing trains everyone to ignore it, and most
    orchestrators respond by pulling the instance out of the load balancer.
    """
    settings = get_settings()

    if settings.vector_backend == "qdrant":
        return check_qdrant()

    if settings.vector_backend == "pgvector":
        # Vectors live in Postgres, so the database check already covers
        # reachability. What it does not cover is whether the schema the store
        # needs is actually present, which is the failure worth catching here.
        from sqlalchemy import text

        from app.db.session import engine

        try:
            with engine.connect() as conn:
                return bool(
                    conn.execute(
                        text(
                            "SELECT 1 FROM information_schema.columns "
                            "WHERE table_name = 'chunks' AND column_name = 'embedding'"
                        )
                    ).first()
                )
        except Exception:
            logger.exception("pgvector health check failed")
            return False

    logger.error("unknown vector backend", extra={"backend": settings.vector_backend})
    return False
