"""Hybrid search endpoint — retrieval without generation (§8)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import DbSession, WsContext
from app.schemas.search import SearchHit, SearchRequest, SearchResponse
from app.services import retrieval_service

router = APIRouter(tags=["search"])


@router.post(
    "/workspaces/{workspace_id}/search",
    response_model=SearchResponse,
    summary="Hybrid search (dense + sparse, RRF-fused)",
)
async def search(payload: SearchRequest, ctx: WsContext, db: DbSession) -> SearchResponse:
    """Retrieval with no LLM in the loop.

    This is the debug surface for the whole RAG system: when an answer is wrong,
    the first question is what the model was given, and this endpoint answers it
    directly — with per-retriever ranks, so you can see whether the dense or the
    sparse half found each chunk.
    """
    result = await retrieval_service.retrieve(
        db,
        workspace_id=ctx.workspace.id,
        query=payload.query,
        top_k=payload.top_k,
        document_ids=payload.document_ids,
    )
    return SearchResponse(
        query=payload.query,
        hits=[
            SearchHit(
                chunk_id=c.chunk_id,
                document_id=c.document_id,
                document_title=c.document_title,
                content=c.content,
                page_label=c.page_label,
                section=c.section,
                score=c.score,
                dense_rank=c.dense_rank,
                sparse_rank=c.sparse_rank,
                found_by_both=c.found_by_both,
            )
            for c in result.chunks
        ],
        dense_candidates=result.dense_count,
        sparse_candidates=result.sparse_count,
        fused_candidates=result.fused_count,
        took_ms=result.took_ms,
    )
