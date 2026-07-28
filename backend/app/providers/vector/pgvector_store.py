"""pgvector implementation of the VectorStore protocol.

**Why this exists.** TDD §29.1 lists pgvector as "the obvious second
implementation" of `VectorStore`. This is that implementation, written because
the deployment target offers managed Postgres but no way to run Qdrant without a
third vendor — and adding a vendor to a demo is worse than using the database
already present.

It is also the honest test of the protocol claim: swapping the vector store
touches this file, a migration and one line of configuration. Nothing in
`retrieval_service`, the ingestion pipeline, the API or the frontend changes.

**Trade-offs, stated rather than glossed.** Qdrant remains the better choice at
scale — payload-index filtering, quantization, and keeping a memory-hungry ANN
workload off the OLTP database (D3). pgvector puts that workload on the same
instance serving transactions, and its HNSW index is less tunable. What it buys
is one fewer service to run, back up and reconcile, and transactional
consistency between a chunk and its vector: they are the same row, so they
cannot drift, which removes the reconciliation problem §13 exists to manage.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import text

from app.db.session import SessionLocal
from app.providers.vector.base import VectorHit, VectorRecord

logger = logging.getLogger(__name__)


class PgVectorStore:
    """Vectors as a column on `chunks`, searched with the `<=>` cosine operator."""

    def __init__(self, dimensions: int) -> None:
        self._dimensions = dimensions

    # ── schema ───────────────────────────────────────────────────────────

    def ensure_collection(self) -> None:
        """Verify the extension and column exist. Idempotent.

        The schema itself is owned by Alembic — creating tables from application
        code at start-up is how N replicas race each other (§19). This only
        checks, and fails with an actionable message rather than a cryptic
        "operator does not exist" on the first query.
        """
        with SessionLocal() as session:
            has_extension = session.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            ).first()
            if not has_extension:
                raise RuntimeError(
                    "The pgvector extension is not installed. Run `alembic upgrade head` "
                    "against a Postgres build that provides it."
                )

            width = session.execute(
                text(
                    """
                    SELECT atttypmod
                    FROM pg_attribute
                    WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'
                    """
                )
            ).scalar()
            # pgvector stores the declared dimension in atttypmod.
            if width is not None and width > 0 and width != self._dimensions:
                raise RuntimeError(
                    f"chunks.embedding has width {width}, but EMBEDDING_DIMENSIONS is "
                    f"{self._dimensions}. Changing the embedding model requires the "
                    f"migration in TDD §29.3, not a configuration edit."
                )

    # ── writes ───────────────────────────────────────────────────────────

    def upsert(self, records: list[VectorRecord]) -> None:
        """Write vectors onto their own chunk rows.

        An UPDATE rather than an INSERT: the chunk already exists, and the vector
        is one of its columns. That is precisely why this backend cannot drift
        from Postgres the way an external index can.
        """
        if not records:
            return
        with SessionLocal() as session:
            session.execute(
                text(
                    "UPDATE chunks SET embedding = CAST(:embedding AS vector) "
                    "WHERE id = :chunk_id"
                ),
                [{"chunk_id": r.chunk_id, "embedding": _to_literal(r.vector)} for r in records],
            )
            session.commit()

    def delete_by_document(self, document_id: uuid.UUID) -> None:
        # The rows are removed by the SQL cascade; clearing the vector first
        # keeps the ordering guarantee of §13 (never retrievable mid-delete).
        with SessionLocal() as session:
            session.execute(
                text("UPDATE chunks SET embedding = NULL WHERE document_id = :document_id"),
                {"document_id": document_id},
            )
            session.commit()

    def delete_by_workspace(self, workspace_id: uuid.UUID) -> None:
        with SessionLocal() as session:
            session.execute(
                text("UPDATE chunks SET embedding = NULL WHERE workspace_id = :workspace_id"),
                {"workspace_id": workspace_id},
            )
            session.commit()

    # ── reads ────────────────────────────────────────────────────────────

    def search(
        self,
        *,
        workspace_id: uuid.UUID,
        vector: list[float],
        limit: int,
        document_ids: list[uuid.UUID] | None = None,
    ) -> list[VectorHit]:
        """Filtered nearest-neighbour search.

        ``workspace_id`` is a required keyword argument here exactly as it is on
        the Qdrant store — the isolation contract is a property of the protocol,
        not of one implementation (D5).

        `1 - (a <=> b)` converts pgvector's cosine *distance* to the cosine
        *similarity* the rest of the system compares against RELEVANCE_FLOOR.
        Returning distance here would invert the refusal gate silently.
        """
        clauses = [
            "c.workspace_id = :workspace_id",
            "c.embedding IS NOT NULL",
            "d.status = 'READY'",
        ]
        params: dict[str, object] = {
            "workspace_id": workspace_id,
            "embedding": _to_literal(vector),
            "limit": limit,
        }
        if document_ids:
            clauses.append("c.document_id = ANY(:document_ids)")
            params["document_ids"] = document_ids

        # The WHERE clause is assembled from `clauses`, a closed set of string
        # literals defined immediately above. Nothing user-supplied is
        # interpolated: the query vector, workspace id, document ids and limit
        # are all bound parameters. See the ruff per-file exemption in
        # pyproject.toml, which records why S608 is suppressed for this module.
        sql = text(
            f"""
            SELECT c.id, c.document_id,
                   1 - (c.embedding <=> CAST(:embedding AS vector)) AS similarity
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE {" AND ".join(clauses)}
            ORDER BY c.embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
            """
        )

        with SessionLocal() as session:
            rows = session.execute(sql, params).all()

        return [
            VectorHit(chunk_id=row[0], document_id=row[1], score=float(row[2])) for row in rows
        ]

    def count(self, workspace_id: uuid.UUID | None = None) -> int:
        sql = "SELECT count(*) FROM chunks WHERE embedding IS NOT NULL"
        params: dict[str, object] = {}
        if workspace_id is not None:
            sql += " AND workspace_id = :workspace_id"
            params["workspace_id"] = workspace_id
        with SessionLocal() as session:
            return int(session.execute(text(sql), params).scalar() or 0)


def _to_literal(vector: list[float]) -> str:
    """pgvector's text input form: '[0.1,0.2,...]'.

    Passed as a bound parameter and cast in SQL, never interpolated — the values
    are floats, but building SQL by concatenation is a habit worth not having.
    """
    return "[" + ",".join(f"{value:.7g}" for value in vector) + "]"
