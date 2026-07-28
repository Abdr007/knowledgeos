"""End-to-end: a document ingested must actually be retrievable.

**Why this test exists.** A bug shipped to production that no unit test could
have caught: the pgvector backend runs in its own database session, and the
pipeline had only *flushed* the chunk rows before indexing them. Its UPDATE
matched nothing — and an UPDATE that matches nothing is not an error. Ingestion
reported success, the document reached READY, and every subsequent query
returned zero dense candidates, so the refusal gate declined questions the corpus
plainly answered.

Everything looked healthy: the document was READY, chunks existed, health checks
were green. The only observable symptom was worse answers.

So this asserts the property that actually matters — **after ingestion, the dense
retriever returns the chunk** — and it runs against BOTH vector backends, because
the failure was specific to one of them and invisible in the other.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.content import Document
from app.db.models.enums import DocumentStatus, SourceType
from app.db.models.identity import Organization, User, Workspace
from app.providers.storage.local_disk import get_storage
from app.providers.vector.registry import get_vector_store
from app.services import retrieval_service
from app.services.ingestion_pipeline import process_document

CORPUS = b"""# Retrieval design notes

Reciprocal Rank Fusion was chosen instead of a weighted score blend because
cosine similarity and Postgres ts_rank live on incomparable scales, so blending
them requires per-corpus normalisation that drifts as documents are added.

Tenant isolation is enforced twice: once as a SQL predicate and once as a filter
on the vector store, with post-fetch re-verification against Postgres.

The refusal gate fires when the best raw cosine similarity falls below the
configured floor, and the system then answers without calling the model at all.
"""


@pytest.fixture
def workspace(db: Session) -> Iterator[Workspace]:
    """A throwaway tenant, removed afterwards so runs do not accumulate."""
    suffix = uuid.uuid4().hex[:10]
    user = User(
        email=f"ingest-{suffix}@knowledgeos.ai",
        password_hash="x" * 32,
        full_name="Ingest Fixture",
    )
    db.add(user)
    db.flush()

    org = Organization(name=f"Org {suffix}", slug=f"org-{suffix}", created_by=user.id)
    db.add(org)
    db.flush()

    ws = Workspace(org_id=org.id, name="General", slug="general", created_by=user.id)
    db.add(ws)
    db.commit()

    yield ws

    # Explicit order with a flush between each: organizations.created_by
    # references users, so the user must go last or the FK rejects the delete.
    get_vector_store().delete_by_workspace(ws.id)
    db.delete(ws)
    db.flush()
    db.delete(org)
    db.flush()
    db.delete(user)
    db.commit()


@pytest.mark.parametrize("backend", ["qdrant", "pgvector"])
def test_ingested_document_is_retrievable(
    db: Session, workspace: Workspace, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "vector_backend", backend, raising=False)
    # The factory is process-cached; clear it so the override takes effect.
    get_vector_store.cache_clear()

    try:
        store = get_vector_store()
        store.ensure_collection()
    except Exception as exc:  # pragma: no cover — backend unavailable in this env
        pytest.skip(f"{backend} unavailable: {exc}")

    document = Document(
        workspace_id=workspace.id,
        uploaded_by=workspace.created_by,
        title="Retrieval design notes.md",
        source_type=SourceType.MARKDOWN,
        checksum_sha256=uuid.uuid4().hex * 2,
        status=DocumentStatus.PENDING,
    )
    db.add(document)
    db.flush()
    document.storage_key = f"test/{document.id}.bin"
    get_storage().put(document.storage_key, CORPUS)
    db.commit()

    try:
        result = process_document(db, document_id=document.id)
        assert result.chunks > 0, "ingestion produced no chunks"

        db.expire_all()
        assert db.get(Document, document.id).status is DocumentStatus.READY

        # THE ASSERTION THAT MATTERS. A READY document whose vectors were never
        # written still looks healthy everywhere else; only this fails.
        retrieval = asyncio.run(
            retrieval_service.retrieve(
                db,
                workspace_id=workspace.id,
                query="Why was Reciprocal Rank Fusion chosen over a weighted blend?",
                top_k=5,
            )
        )

        assert retrieval.dense_count > 0, (
            f"{backend}: ingestion succeeded but the DENSE retriever returned nothing. "
            "The vectors were not written, or were written where the search cannot see "
            "them. Retrieval silently degrades to keyword-only when this happens."
        )
        assert retrieval.chunks, f"{backend}: no chunks survived fusion"
        assert retrieval.relevance >= settings.relevance_floor, (
            f"{backend}: an on-topic question scored {retrieval.relevance:.3f}, below the "
            f"{settings.relevance_floor} floor — the refusal gate would decline a question "
            "this corpus answers."
        )
    finally:
        get_vector_store().delete_by_document(document.id)
        get_storage().delete(document.storage_key or "")
        db.delete(db.get(Document, document.id))
        db.commit()
        get_vector_store.cache_clear()


@pytest.mark.parametrize("backend", ["qdrant", "pgvector"])
def test_vector_store_refuses_to_search_without_a_workspace(backend: str) -> None:
    """The isolation contract is a property of the PROTOCOL, not one backend.

    `search` takes workspace_id as a required keyword argument, so omitting the
    tenant filter is a TypeError rather than a data breach (D5). Asserted for
    every implementation so a new backend cannot quietly relax it.
    """
    settings = get_settings()
    original = settings.vector_backend
    object.__setattr__(settings, "vector_backend", backend)
    get_vector_store.cache_clear()
    try:
        store = get_vector_store()
        with pytest.raises(TypeError):
            store.search(vector=[0.0] * settings.embedding_dimensions, limit=1)  # type: ignore[call-arg]
    finally:
        object.__setattr__(settings, "vector_backend", original)
        get_vector_store.cache_clear()
