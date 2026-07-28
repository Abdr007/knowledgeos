"""Vector store protocol and its shared value types (§29.1).

`VectorHit` and `VectorRecord` live HERE rather than in the Qdrant module.
They used to live there, and pgvector imported them from it — which dragged the
entire qdrant-client SDK (~33 MB resident) into every process, including
deployments configured to use pgvector and never to touch Qdrant at all. A
protocol's value types must not be owned by one of its implementations.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class VectorHit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    #: Cosine SIMILARITY, not distance. Implementations must convert, because
    #: this is the value compared against RELEVANCE_FLOOR and returning a
    #: distance would invert the refusal gate silently.
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


@runtime_checkable
class VectorStore(Protocol):
    """The tenancy contract is part of the protocol, not of one backend.

    `search` takes `workspace_id` as a REQUIRED keyword argument, so omitting the
    tenant filter is a type error rather than a data breach (D5). Every
    implementation is tested against that.
    """

    def ensure_collection(self) -> None: ...

    def upsert(self, records: list[VectorRecord]) -> None: ...

    def delete_by_document(self, document_id: uuid.UUID) -> None: ...

    def delete_by_workspace(self, workspace_id: uuid.UUID) -> None: ...

    def search(
        self,
        *,
        workspace_id: uuid.UUID,
        vector: list[float],
        limit: int,
        document_ids: list[uuid.UUID] | None = None,
    ) -> list[VectorHit]: ...

    def count(self, workspace_id: uuid.UUID | None = None) -> int: ...
