"""Qdrant vector store (D3, §13).

One collection, multi-tenant by payload filter. A collection per workspace would
mean thousands of HNSW graphs each carrying fixed overhead and none of them warm;
Qdrant's payload indexes make a filtered ANN search narrow the traversal rather
than post-filter results, so one collection is both cheaper and faster here.

**The isolation contract:** ``search`` REQUIRES ``workspace_id``. There is no
overload without it, so omitting the tenant filter is a type error rather than a
runtime data breach (§13). Callers re-verify the returned ids against Postgres —
defence in depth on the one boundary that must not fail.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass
from functools import lru_cache

from qdrant_client import QdrantClient, models

from app.core.clients import get_qdrant
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass(frozen=True, slots=True)
class VectorHit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    score: float


@dataclass(frozen=True, slots=True)
class VectorRecord:
    vector_id: uuid.UUID
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    workspace_id: uuid.UUID
    vector: list[float]
    ordinal: int
    page_from: int | None
    page_to: int | None


class QdrantVectorStore:
    def __init__(self, client: QdrantClient, collection: str, dimensions: int) -> None:
        self._client = client
        self._collection = collection
        self._dimensions = dimensions

    # ── schema ───────────────────────────────────────────────────────────

    def ensure_collection(self) -> None:
        """Create the collection and its payload indexes if absent. Idempotent."""
        existing = {c.name for c in self._client.get_collections().collections}
        if self._collection not in existing:
            logger.info(
                "creating qdrant collection",
                extra={"collection": self._collection, "dimensions": self._dimensions},
            )
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=models.VectorParams(
                    size=self._dimensions,
                    distance=models.Distance.COSINE,
                ),
                # int8 scalar quantization: ~4x less memory for ~1% recall, which
                # is the right trade long before the corpus is large (§13).
                quantization_config=models.ScalarQuantization(
                    scalar=models.ScalarQuantizationConfig(
                        type=models.ScalarType.INT8,
                        always_ram=True,
                    )
                ),
                hnsw_config=models.HnswConfigDiff(m=16, ef_construct=128),
            )
        else:
            # Guard against a model change that silently changes vector width.
            info = self._client.get_collection(self._collection)
            configured = info.config.params.vectors
            # `vectors` is VectorParams | dict[str, VectorParams] | None — only
            # the single-vector form has a width to compare.
            size = getattr(configured, "size", None)
            if size is not None and size != self._dimensions:
                raise RuntimeError(
                    f"Qdrant collection {self._collection!r} has width {size}, but "
                    f"EMBEDDING_DIMENSIONS is {self._dimensions}. Changing the embedding "
                    f"model requires the migration in TDD §29.3, not a config edit."
                )

        # Indexed payload fields. Without the index, a filtered search degrades to
        # a scan and tenant filtering becomes the slowest part of retrieval.
        for field, schema in (
            ("workspace_id", models.PayloadSchemaType.KEYWORD),
            ("document_id", models.PayloadSchemaType.KEYWORD),
        ):
            # Qdrant has no "create index if absent"; a second call raises.
            # Suppressed rather than pre-checked, because a check would race
            # against another replica starting at the same moment.
            with contextlib.suppress(Exception):
                self._client.create_payload_index(
                    collection_name=self._collection,
                    field_name=field,
                    field_schema=schema,
                )

    # ── writes ───────────────────────────────────────────────────────────

    def upsert(self, records: list[VectorRecord]) -> None:
        if not records:
            return
        self._client.upsert(
            collection_name=self._collection,
            points=[
                models.PointStruct(
                    id=str(r.vector_id),
                    vector=r.vector,
                    payload={
                        "workspace_id": str(r.workspace_id),
                        "document_id": str(r.document_id),
                        "chunk_id": str(r.chunk_id),
                        "ordinal": r.ordinal,
                        "page_from": r.page_from,
                        "page_to": r.page_to,
                    },
                )
                for r in records
            ],
            wait=True,  # a READY document must be searchable, not eventually searchable
        )

    def delete_by_document(self, document_id: uuid.UUID) -> None:
        self._client.delete(
            collection_name=self._collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document_id",
                            match=models.MatchValue(value=str(document_id)),
                        )
                    ]
                )
            ),
            wait=True,
        )

    def delete_by_workspace(self, workspace_id: uuid.UUID) -> None:
        self._client.delete(
            collection_name=self._collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="workspace_id",
                            match=models.MatchValue(value=str(workspace_id)),
                        )
                    ]
                )
            ),
            wait=True,
        )

    # ── reads ────────────────────────────────────────────────────────────

    def search(
        self,
        *,
        workspace_id: uuid.UUID,
        vector: list[float],
        limit: int,
        document_ids: list[uuid.UUID] | None = None,
    ) -> list[VectorHit]:
        """Filtered ANN search. ``workspace_id`` is mandatory, by design."""
        must: list[models.Condition] = [
            models.FieldCondition(
                key="workspace_id", match=models.MatchValue(value=str(workspace_id))
            )
        ]
        if document_ids:
            must.append(
                models.FieldCondition(
                    key="document_id",
                    match=models.MatchAny(any=[str(d) for d in document_ids]),
                )
            )

        hits = self._client.query_points(
            collection_name=self._collection,
            query=vector,
            limit=limit,
            query_filter=models.Filter(must=must),
            with_payload=True,
        ).points

        out: list[VectorHit] = []
        for hit in hits:
            payload = hit.payload or {}
            try:
                out.append(
                    VectorHit(
                        chunk_id=uuid.UUID(payload["chunk_id"]),
                        document_id=uuid.UUID(payload["document_id"]),
                        score=float(hit.score),
                    )
                )
            except (KeyError, ValueError):
                logger.warning(
                    "skipping malformed qdrant point", extra={"point_id": str(hit.id)}
                )
        return out

    def count(self, workspace_id: uuid.UUID | None = None) -> int:
        flt = None
        if workspace_id is not None:
            flt = models.Filter(
                must=[
                    models.FieldCondition(
                        key="workspace_id", match=models.MatchValue(value=str(workspace_id))
                    )
                ]
            )
        return int(self._client.count(self._collection, count_filter=flt, exact=True).count)


@lru_cache
def get_vector_store() -> QdrantVectorStore:
    return QdrantVectorStore(
        client=get_qdrant(),
        collection=settings.qdrant_collection,
        dimensions=settings.embedding_dimensions,
    )
