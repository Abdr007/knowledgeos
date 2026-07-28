"""Vector store selection (D3, §29.1).

Two implementations of one protocol, chosen by configuration:

- **qdrant** — the default and the better choice at scale (payload-index
  filtering, quantization, ANN load kept off the OLTP database).
- **pgvector** — one fewer service to run, and transactional consistency between
  a chunk and its vector because they are the same row.

Callers import `get_vector_store` from here and never name an implementation, so
switching is this file plus one environment variable.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@lru_cache
def get_vector_store():
    backend = settings.vector_backend

    if backend == "qdrant":
        from app.core.clients import get_qdrant
        from app.providers.vector.qdrant_store import QdrantVectorStore

        return QdrantVectorStore(
            client=get_qdrant(),
            collection=settings.qdrant_collection,
            dimensions=settings.embedding_dimensions,
        )

    if backend == "pgvector":
        from app.providers.vector.pgvector_store import PgVectorStore

        return PgVectorStore(dimensions=settings.embedding_dimensions)

    raise ValueError(f"Unknown VECTOR_BACKEND: {backend!r}")
