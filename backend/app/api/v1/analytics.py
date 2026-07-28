"""Analytics and admin endpoints (§8, §26).

Everything here reads from ``usage_events`` and the domain tables — nothing
maintains a parallel counter, so a number on the dashboard can always be traced
back to the rows that produced it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import Float, cast, func, select

from app.api.deps import DbSession, WsContext, require_superuser
from app.core.config import get_settings
from app.db.models.content import Document
from app.db.models.conversation import Citation, Conversation, Feedback, Message
from app.db.models.enums import DocumentStatus, FinishReason, MessageRole, Role
from app.db.models.ops import UsageEvent
from app.schemas.analytics import (
    AnalyticsOverview,
    DocumentLeaderboardEntry,
    QualityMetrics,
    SystemStatus,
    UsagePoint,
)
from app.services import queue

settings = get_settings()
router = APIRouter(tags=["analytics"])


@router.get(
    "/workspaces/{workspace_id}/analytics/overview",
    response_model=AnalyticsOverview,
    summary="Headline metrics for a workspace",
)
def overview(ctx: WsContext, db: DbSession) -> AnalyticsOverview:
    ctx.require(Role.MEMBER)
    ws = ctx.workspace.id

    docs = db.execute(
        select(
            func.count(Document.id),
            func.count(Document.id).filter(Document.status == DocumentStatus.READY),
            func.count(Document.id).filter(Document.status == DocumentStatus.FAILED),
            func.coalesce(func.sum(Document.chunk_count), 0),
        ).where(Document.workspace_id == ws)
    ).one()

    conversations = db.scalar(
        select(func.count()).select_from(Conversation).where(Conversation.workspace_id == ws)
    )

    msg = db.execute(
        select(
            func.count(Message.id),
            func.percentile_cont(0.5).within_group(cast(Message.latency_ms, Float)),
            func.percentile_cont(0.95).within_group(cast(Message.latency_ms, Float)),
            func.percentile_cont(0.5).within_group(cast(Message.ttft_ms, Float)),
            func.avg(Message.groundedness),
            func.count(Message.id).filter(Message.finish_reason == FinishReason.REFUSED),
        )
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Conversation.workspace_id == ws, Message.role == MessageRole.ASSISTANT)
    ).one()

    cost = db.execute(
        select(
            func.coalesce(func.sum(UsageEvent.cost_usd), 0),
            func.coalesce(func.sum(UsageEvent.input_tokens), 0),
            func.coalesce(func.sum(UsageEvent.output_tokens), 0),
        ).where(UsageEvent.workspace_id == ws)
    ).one()

    answered = (msg[0] or 0) - (msg[5] or 0)
    return AnalyticsOverview(
        documents=docs[0] or 0,
        documents_ready=docs[1] or 0,
        documents_failed=docs[2] or 0,
        chunks=docs[3] or 0,
        conversations=conversations or 0,
        messages=msg[0] or 0,
        answered=answered,
        refused=msg[5] or 0,
        refusal_rate=round((msg[5] or 0) / msg[0], 4) if msg[0] else 0.0,
        latency_p50_ms=int(msg[1]) if msg[1] else None,
        latency_p95_ms=int(msg[2]) if msg[2] else None,
        ttft_p50_ms=int(msg[3]) if msg[3] else None,
        avg_groundedness=round(float(msg[4]), 4) if msg[4] is not None else None,
        total_cost_usd=float(cost[0]),
        input_tokens=int(cost[1]),
        output_tokens=int(cost[2]),
        cost_per_answer_usd=round(float(cost[0]) / answered, 6) if answered else 0.0,
    )


@router.get(
    "/workspaces/{workspace_id}/analytics/usage",
    response_model=list[UsagePoint],
    summary="Token and cost time series",
)
def usage(
    ctx: WsContext,
    db: DbSession,
    days: Annotated[int, Query(ge=1, le=90)] = 14,
) -> list[UsagePoint]:
    ctx.require(Role.MEMBER)
    since = datetime.now(UTC) - timedelta(days=days)
    bucket = func.date_trunc("day", UsageEvent.created_at).label("bucket")

    rows = db.execute(
        select(
            bucket,
            func.sum(UsageEvent.input_tokens),
            func.sum(UsageEvent.output_tokens),
            func.sum(UsageEvent.cost_usd),
            func.count(UsageEvent.id),
        )
        .where(UsageEvent.workspace_id == ctx.workspace.id, UsageEvent.created_at >= since)
        .group_by(bucket)
        .order_by(bucket)
    ).all()

    return [
        UsagePoint(
            date=row[0].date(),
            input_tokens=int(row[1] or 0),
            output_tokens=int(row[2] or 0),
            cost_usd=float(row[3] or 0),
            calls=int(row[4] or 0),
        )
        for row in rows
    ]


@router.get(
    "/workspaces/{workspace_id}/analytics/quality",
    response_model=QualityMetrics,
    summary="Answer-quality signals",
)
def quality(ctx: WsContext, db: DbSession) -> QualityMetrics:
    ctx.require(Role.MEMBER)
    ws = ctx.workspace.id

    feedback = db.execute(
        select(
            func.count(Feedback.id),
            func.count(Feedback.id).filter(Feedback.rating > 0),
        )
        .join(Message, Message.id == Feedback.message_id)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Conversation.workspace_id == ws)
    ).one()

    grounded = db.execute(
        select(
            func.percentile_cont(0.1).within_group(cast(Message.groundedness, Float)),
            func.percentile_cont(0.5).within_group(cast(Message.groundedness, Float)),
        )
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Conversation.workspace_id == ws,
            Message.role == MessageRole.ASSISTANT,
            Message.groundedness.isnot(None),
        )
    ).one()

    citations = db.scalar(
        select(func.count())
        .select_from(Citation)
        .join(Message, Message.id == Citation.message_id)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Conversation.workspace_id == ws)
    )
    answers = db.scalar(
        select(func.count())
        .select_from(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Conversation.workspace_id == ws,
            Message.role == MessageRole.ASSISTANT,
            Message.finish_reason != FinishReason.REFUSED,
        )
    )

    return QualityMetrics(
        feedback_count=feedback[0] or 0,
        positive_feedback=feedback[1] or 0,
        satisfaction_rate=round((feedback[1] or 0) / feedback[0], 4) if feedback[0] else None,
        # The BOTTOM decile is where fabrication lives; the mean hides it (§26.3).
        groundedness_p10=round(float(grounded[0]), 4) if grounded[0] is not None else None,
        groundedness_p50=round(float(grounded[1]), 4) if grounded[1] is not None else None,
        total_citations=citations or 0,
        citations_per_answer=round((citations or 0) / answers, 2) if answers else 0.0,
    )


@router.get(
    "/workspaces/{workspace_id}/analytics/documents",
    response_model=list[DocumentLeaderboardEntry],
    summary="Which documents actually answer questions",
)
def document_leaderboard(
    ctx: WsContext, db: DbSession, limit: Annotated[int, Query(ge=1, le=50)] = 10
) -> list[DocumentLeaderboardEntry]:
    """Citation counts per document.

    A real table for citations (rather than JSON on the message) is what makes
    this a GROUP BY instead of a scan (§7) — and it answers a question every
    knowledge-base owner has: which of these documents is earning its place.
    """
    ctx.require(Role.MEMBER)
    rows = db.execute(
        select(
            Document.id,
            Document.title,
            Document.chunk_count,
            func.count(Citation.id).label("citations"),
        )
        .outerjoin(Citation, Citation.document_id == Document.id)
        .where(Document.workspace_id == ctx.workspace.id)
        .group_by(Document.id, Document.title, Document.chunk_count)
        .order_by(func.count(Citation.id).desc(), Document.created_at.desc())
        .limit(limit)
    ).all()
    return [
        DocumentLeaderboardEntry(
            document_id=r[0], title=r[1], chunks=r[2] or 0, citations=int(r[3] or 0)
        )
        for r in rows
    ]


@router.get("/admin/system", response_model=SystemStatus, summary="Platform status")
def system_status(
    db: DbSession,
    _superuser: Annotated[object, require_superuser],
) -> SystemStatus:
    """Superuser only. Secrets are never included (§18)."""
    return _system_status(db)


@router.get(
    "/workspaces/{workspace_id}/admin/system",
    response_model=SystemStatus,
    summary="Platform status visible to workspace admins",
)
def workspace_system_status(ctx: WsContext, db: DbSession) -> SystemStatus:
    ctx.require(Role.ADMIN)
    return _system_status(db)


def _system_status(db) -> SystemStatus:
    from app.providers.llm.registry import get_llm_provider
    from app.providers.vector.registry import get_vector_store

    provider = get_llm_provider()
    try:
        vectors = get_vector_store().count()
    except Exception:
        vectors = -1

    try:
        queue_depth = queue.depth()
    except Exception:
        queue_depth = {"pending": -1, "processing": -1}

    return SystemStatus(
        environment=settings.environment.value,
        llm_provider=provider.name,
        chat_model=provider.model,
        llm_configured=settings.llm_is_configured,
        embedding_provider=settings.embedding_provider,
        embedding_model=settings.embedding_model,
        embedding_dimensions=settings.embedding_dimensions,
        vector_count=vectors,
        queue_pending=queue_depth["pending"],
        queue_processing=queue_depth["processing"],
        relevance_floor=settings.relevance_floor,
        retrieval_top_k=settings.retrieval_top_k,
        chunk_size_chars=settings.chunk_size_chars,
        chunk_overlap_chars=settings.chunk_overlap_chars,
    )
