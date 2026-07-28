"""Local ONNX embeddings via fastembed (D2).

The decision this implements: **Anthropic ships no embeddings API.** A deployment
holding only an Anthropic credential cannot embed through its chat provider at
all, so the retrieval half of RAG needs its own answer.

Running the model locally on CPU means no second vendor, no per-token cost, and
no document text leaving the deployment — which for an enterprise knowledge
platform is a selling point rather than a compromise. The cost is CPU time, which
is why ingestion is asynchronous (D6) and why §22 names embedding throughput as
the honest ceiling of this design.
"""

from __future__ import annotations

import hashlib
import logging
import struct
import threading
from functools import lru_cache

from app.core.clients import get_redis
from app.core.config import get_settings
from app.providers.embeddings.base import EmbedKind

logger = logging.getLogger(__name__)
settings = get_settings()

_CACHE_PREFIX = "kos:v1:embed"
_CACHE_TTL_SECONDS = 7 * 24 * 3600

# BGE models are trained asymmetrically: queries carry an instruction prefix,
# documents do not. Applying the prefix to both sides, or neither, silently costs
# recall (see base.py).
_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class LocalOnnxEmbeddings:
    """CPU inference with a Redis-backed cache keyed by model + text."""

    def __init__(self, model_name: str | None = None, dimensions: int | None = None) -> None:
        self.model_name = model_name or settings.embedding_model
        self.dimensions = dimensions or settings.embedding_dimensions
        self._model = None
        # Model load is slow and not thread-safe; guard the one-time init so a
        # burst of concurrent first-requests loads it once rather than N times.
        self._lock = threading.Lock()

    def _ensure_model(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding

                    logger.info("loading embedding model", extra={"model": self.model_name})
                    self._model = TextEmbedding(model_name=self.model_name)
                    logger.info("embedding model ready", extra={"model": self.model_name})
        return self._model

    def embed(self, texts: list[str], *, kind: EmbedKind = "document") -> list[list[float]]:
        if not texts:
            return []

        prepared = [
            (_QUERY_PREFIX + t) if kind == "query" else t for t in texts
        ]

        # Cache first. Re-ingesting a revised document re-embeds only what
        # changed, and a repeated question skips inference entirely (§14).
        cached = self._cache_get(prepared)
        missing = [i for i, v in enumerate(cached) if v is None]

        if missing:
            model = self._ensure_model()
            fresh = [list(map(float, v)) for v in model.embed([prepared[i] for i in missing])]
            for index, vector in zip(missing, fresh, strict=True):
                cached[index] = vector
            self._cache_set([prepared[i] for i in missing], fresh)

        result: list[list[float]] = []
        for index, vector in enumerate(cached):
            assert vector is not None
            if len(vector) != self.dimensions:
                # Fail loudly: a width mismatch means EMBEDDING_DIMENSIONS does
                # not match the model, and silently writing the wrong width
                # corrupts the collection (§29.3).
                raise ValueError(
                    f"Embedding width {len(vector)} does not match configured "
                    f"EMBEDDING_DIMENSIONS={self.dimensions} for model {self.model_name!r}."
                )
            result.append(vector)
        return result

    # ── cache ────────────────────────────────────────────────────────────

    def _key(self, text: str) -> str:
        digest = hashlib.sha256(f"{self.model_name}\x00{text}".encode()).hexdigest()
        return f"{_CACHE_PREFIX}:{digest}"

    def _cache_get(self, texts: list[str]) -> list[list[float] | None]:
        try:
            redis = get_redis()
            # decode_responses=True on the shared client would mangle packed
            # floats, so vectors are stored base-free as hex text instead of raw
            # bytes — slightly larger, and safe with the existing client.
            raw = redis.mget([self._key(t) for t in texts])
        except Exception:
            logger.debug("embedding cache unavailable")
            return [None] * len(texts)

        out: list[list[float] | None] = []
        for value in raw:
            if not value:
                out.append(None)
                continue
            try:
                data = bytes.fromhex(value)
                out.append(list(struct.unpack(f"{len(data) // 4}f", data)))
            except Exception:
                out.append(None)
        return out

    def _cache_set(self, texts: list[str], vectors: list[list[float]]) -> None:
        try:
            pipe = get_redis().pipeline()
            for text, vector in zip(texts, vectors, strict=True):
                packed = struct.pack(f"{len(vector)}f", *vector).hex()
                pipe.setex(self._key(text), _CACHE_TTL_SECONDS, packed)
            pipe.execute()
        except Exception:
            # A cache write failure must never fail an ingestion.
            logger.debug("failed to write embedding cache")


@lru_cache
def get_embedding_provider():
    """Resolve the configured provider once per process."""
    if settings.embedding_provider == "local":
        return LocalOnnxEmbeddings()
    raise NotImplementedError(
        f"Embedding provider {settings.embedding_provider!r} is not available in this build."
    )
