"""pgvector embedding column

Adds the extension, an `embedding` column on chunks, and an HNSW index, so the
pgvector VectorStore implementation has a schema to work against (§29.1).

DELIBERATELY UNCONDITIONAL. An earlier version skipped when the extension was
unavailable — but a migration that no-ops still records itself as applied, so a
server that later gains pgvector would never get the column and the failure
would surface as an empty search rather than an error. A migration that
sometimes does nothing is a trap.

The column is harmless when the Qdrant backend is in use: it stays NULL and
costs nothing. pgvector ships with every mainstream managed Postgres and with
the compose image, so requiring it is cheaper than the ambiguity.

Revision ID: c1f9b7a2d3e4
Revises: 8a4743600bbe
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c1f9b7a2d3e4"
down_revision: str | None = "8a4743600bbe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DIMENSIONS = 384


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(f"ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding vector({DIMENSIONS})")
    # HNSW with cosine ops, matching the distance the application compares
    # against RELEVANCE_FLOOR. A mismatched opclass silently returns the wrong
    # neighbours rather than erroring.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw "
        "ON chunks USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 128)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS embedding")
    # The extension is deliberately NOT dropped: other schemas may use it.
