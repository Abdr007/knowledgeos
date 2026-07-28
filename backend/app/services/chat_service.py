"""Chat orchestration: retrieve → gate → prompt → stream → validate → persist (§12).

The **refusal gate** is the most important thing in this file. When nothing clears
the relevance floor, the system answers "not in this workspace" *without calling
the model at all*. A model handed zero context and a question will still answer —
from parametric memory, fluently, with no way for the user to tell. That is the
single largest source of confident fabrication in RAG systems, and no amount of
prompting reliably prevents it. Not making the call does.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.conversation import Citation, Conversation, Message
from app.db.models.enums import FinishReason, MessageRole, UsageKind
from app.providers.llm.base import ChatTurn, StreamDone, StreamUsage, TextDelta
from app.providers.llm.registry import get_llm_provider
from app.services import citation_service, prompt_builder, retrieval_service
from app.services.retrieval_service import RetrievedChunk
from app.services.usage_recorder import record_usage

logger = logging.getLogger(__name__)
settings = get_settings()

REFUSAL_TEXT = (
    "I could not find anything in this workspace that answers that question.\n\n"
    "Either the documents covering it have not been uploaded yet, or the question "
    "needs to be phrased closer to the language used in them."
)


@dataclass(slots=True)
class ChatOutcome:
    """Everything the SSE layer needs to emit and persist."""

    message_id: uuid.UUID
    text: str = ""
    sources: list[RetrievedChunk] = field(default_factory=list)
    citations: list[citation_service.ValidatedCitation] = field(default_factory=list)
    validated_markers: list[int] = field(default_factory=list)
    stripped_markers: list[int] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    ttft_ms: int | None = None
    latency_ms: int = 0
    finish_reason: FinishReason = FinishReason.STOP
    groundedness: float | None = None
    refused: bool = False
    provider: str = ""
    model: str = ""


def load_history(db: Session, *, conversation_id: uuid.UUID, limit: int = 8) -> list[ChatTurn]:
    rows = db.scalars(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.role.in_([MessageRole.USER, MessageRole.ASSISTANT]),
        )
        .order_by(Message.created_at.desc())
        .limit(limit * 2)
    ).all()
    ordered = list(reversed(rows))
    return [
        ChatTurn(role="user" if m.role is MessageRole.USER else "assistant", content=m.content)
        for m in ordered
        if m.content.strip()
    ]


async def answer(
    db: Session,
    *,
    conversation: Conversation,
    question: str,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
) -> AsyncIterator[tuple[str, object]]:
    """Yield ``(event_name, payload)`` pairs for the SSE layer.

    A generator rather than a function returning a string, because the caller
    must be able to forward tokens the moment they arrive — buffering the whole
    answer to return it would discard the entire point of streaming.
    """
    started = time.perf_counter()

    # ── retrieve ─────────────────────────────────────────────────────────
    retrieval = await retrieval_service.retrieve(
        db, workspace_id=conversation.workspace_id, query=question
    )

    provider = get_llm_provider()
    outcome = ChatOutcome(
        message_id=uuid.uuid4(),
        sources=retrieval.chunks,
        provider=provider.name,
        model=provider.model,
    )

    # ── the refusal gate ─────────────────────────────────────────────────
    # `relevance` is the raw cosine similarity of the best candidate, NOT the
    # fused score — see RetrievalResult.relevance for why the distinction is what
    # makes this gate work at all.
    if retrieval.is_empty or retrieval.relevance < settings.relevance_floor:
        outcome.refused = True
        outcome.finish_reason = FinishReason.REFUSED
        outcome.text = REFUSAL_TEXT
        outcome.sources = []
        outcome.latency_ms = int((time.perf_counter() - started) * 1000)

        logger.info(
            "refusal gate fired",
            extra={
                "event": "chat.refused",
                "relevance": retrieval.relevance,
                "floor": settings.relevance_floor,
                "retrieved": len(retrieval.chunks),
            },
        )

        yield "meta", {
            "message_id": str(outcome.message_id),
            "sources": [],
            "provider": provider.name,
            "model": provider.model,
            "retrieval_ms": retrieval.took_ms,
            "refused": True,
        }
        for piece in REFUSAL_TEXT.split(" "):
            yield "token", {"delta": piece + " "}
        yield "done", {"finish_reason": FinishReason.REFUSED.value}
        yield "__outcome__", outcome
        return

    # ── prompt ───────────────────────────────────────────────────────────
    history = load_history(db, conversation_id=conversation.id)
    # Drop the just-inserted user message; it is passed as the question.
    if history and history[-1].role == "user" and history[-1].content == question:
        history = history[:-1]

    built = prompt_builder.build_chat_prompt(
        question=question, chunks=retrieval.chunks, history=history
    )
    outcome.sources = built.sources_used

    # Sources go out BEFORE the first token, so the user can see what the answer
    # will be based on while it is being written (§12).
    yield "meta", {
        "message_id": str(outcome.message_id),
        "provider": provider.name,
        "model": provider.model,
        "retrieval_ms": retrieval.took_ms,
        "context_tokens": built.context_tokens,
        "refused": False,
        "sources": [
            {
                "marker": i,
                "chunk_id": str(c.chunk_id),
                "document_id": str(c.document_id),
                "document_title": c.document_title,
                "page_label": c.page_label,
                "section": c.section,
                "score": c.score,
                "found_by_both": c.found_by_both,
                "snippet": " ".join(c.content.split())[:280],
            }
            for i, c in enumerate(built.sources_used, start=1)
        ],
    }

    # ── stream ───────────────────────────────────────────────────────────
    buffer: list[str] = []
    first_token_at: float | None = None

    try:
        async for event in provider.stream_chat(
            system=built.system, turns=built.turns, max_tokens=1500
        ):
            if isinstance(event, TextDelta):
                if first_token_at is None:
                    first_token_at = time.perf_counter()
                    outcome.ttft_ms = int((first_token_at - started) * 1000)
                buffer.append(event.text)
                yield "token", {"delta": event.text}
            elif isinstance(event, StreamUsage):
                outcome.input_tokens = event.input_tokens
                outcome.output_tokens = event.output_tokens
            elif isinstance(event, StreamDone):
                outcome.finish_reason = (
                    FinishReason.LENGTH if event.finish_reason == "length" else FinishReason.STOP
                )
    except Exception as exc:
        # Persist whatever was generated: those tokens were billed whether or not
        # anyone reads them, and a half-answer is more useful than a blank turn.
        outcome.finish_reason = FinishReason.ERROR
        outcome.text = "".join(buffer)
        outcome.latency_ms = int((time.perf_counter() - started) * 1000)
        logger.exception("chat stream failed", extra={"event": "chat.stream_failed"})
        yield "error", {
            "error": "provider_error",
            "detail": str(exc)[:300],
            "retryable": True,
        }
        yield "__outcome__", outcome
        return

    raw_text = "".join(buffer)

    # ── validate citations ───────────────────────────────────────────────
    validated = citation_service.validate(raw_text, built.sources_used)
    outcome.text = validated.text
    outcome.citations = validated.citations
    outcome.validated_markers = validated.validated_markers
    outcome.stripped_markers = validated.stripped_markers
    outcome.groundedness = citation_service.heuristic_groundedness(
        validated.text, built.sources_used
    )
    outcome.latency_ms = int((time.perf_counter() - started) * 1000)

    yield "citations", {
        "validated": validated.validated_markers,
        "stripped": validated.stripped_markers,
        "groundedness": outcome.groundedness,
    }
    yield "usage", {
        "input_tokens": outcome.input_tokens,
        "output_tokens": outcome.output_tokens,
        "ttft_ms": outcome.ttft_ms,
        "latency_ms": outcome.latency_ms,
        "model": provider.model,
    }
    yield "done", {"finish_reason": outcome.finish_reason.value}
    yield "__outcome__", outcome


def persist(
    db: Session,
    *,
    conversation: Conversation,
    outcome: ChatOutcome,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Message:
    """Write the assistant turn, its citations and its usage record."""
    message = Message(
        id=outcome.message_id,
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content=outcome.text,
        model=outcome.model,
        prompt_tokens=outcome.input_tokens,
        completion_tokens=outcome.output_tokens,
        ttft_ms=outcome.ttft_ms,
        latency_ms=outcome.latency_ms,
        finish_reason=outcome.finish_reason,
        groundedness=outcome.groundedness,
    )
    db.add(message)
    db.flush()

    for citation in outcome.citations:
        db.add(
            Citation(
                message_id=message.id,
                chunk_id=citation.chunk.chunk_id,
                document_id=citation.chunk.document_id,
                marker=citation.marker,
                score=citation.chunk.score,
                snippet=citation.snippet,
                document_title=citation.chunk.document_title,
                page_label=citation.chunk.page_label,
            )
        )

    # A refusal costs nothing and is not a billable event, so it is not recorded
    # as one — that keeps cost-per-answer honest rather than diluted by refusals.
    if not outcome.refused and (outcome.input_tokens or outcome.output_tokens):
        record_usage(
            db,
            org_id=org_id,
            workspace_id=conversation.workspace_id,
            user_id=user_id,
            kind=UsageKind.CHAT,
            model=outcome.model,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            latency_ms=outcome.latency_ms,
        )

    conversation.last_message_at = datetime.now(UTC)
    db.flush()

    logger.info(
        "chat completed",
        extra={
            "event": "chat.completed",
            "conversation_id": str(conversation.id),
            "retrieved": len(outcome.sources),
            "cited": len(outcome.citations),
            "stripped": len(outcome.stripped_markers),
            "groundedness": outcome.groundedness,
            "ttft_ms": outcome.ttft_ms,
            "latency_ms": outcome.latency_ms,
            "refused": outcome.refused,
        },
    )
    return message
