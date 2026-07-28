"""Chat endpoints, including the SSE stream (§12, §15)."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, WsContext
from app.core.config import get_settings
from app.core.errors import AuthorizationError, NotFoundError
from app.core.rate_limit import check_rate_limit
from app.db.models.conversation import Citation, Conversation, Feedback, Message
from app.db.models.enums import MessageRole, Role
from app.db.models.identity import Membership, Workspace
from app.db.session import SessionLocal
from app.schemas.chat import (
    AskRequest,
    CitationOut,
    ConversationCreate,
    ConversationOut,
    FeedbackRequest,
    MessageOut,
)
from app.schemas.common import Message as MessageDTO
from app.services import chat_service

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(tags=["chat"])

#: Idle proxies reap quiet connections; a comment frame every 15s keeps the
#: stream alive during a long first-token wait without emitting fake data.
HEARTBEAT_SECONDS = 15


def _sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _load_conversation(
    db, user, conversation_id: uuid.UUID
) -> tuple[Conversation, Workspace, Role]:
    row = db.execute(
        select(Conversation, Workspace, Membership.role)
        .join(Workspace, Workspace.id == Conversation.workspace_id)
        .join(
            Membership,
            (Membership.org_id == Workspace.org_id) & (Membership.user_id == user.id),
        )
        .where(Conversation.id == conversation_id)
    ).first()
    if row is None:
        raise NotFoundError("Conversation not found.")
    conversation, workspace, role = row
    # A conversation is private to its author; admins can read for support and
    # audit, which is why role is returned rather than a bare boolean.
    if conversation.user_id != user.id and not Role(role).satisfies(Role.ADMIN):
        raise NotFoundError("Conversation not found.")
    return conversation, workspace, Role(role)


# ── conversations ────────────────────────────────────────────────────────


@router.post(
    "/workspaces/{workspace_id}/conversations",
    response_model=ConversationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Start a conversation",
)
def create_conversation(
    payload: ConversationCreate, ctx: WsContext, db: DbSession
) -> ConversationOut:
    ctx.require(Role.MEMBER)
    conversation = Conversation(
        workspace_id=ctx.workspace.id,
        user_id=ctx.user.id,
        title=payload.title or "New conversation",
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return ConversationOut.model_validate(conversation)


@router.get(
    "/workspaces/{workspace_id}/conversations",
    response_model=list[ConversationOut],
    summary="List conversations",
)
def list_conversations(ctx: WsContext, db: DbSession) -> list[ConversationOut]:
    count = (
        select(func.count())
        .select_from(Message)
        .where(Message.conversation_id == Conversation.id)
        .scalar_subquery()
    )
    stmt = (
        select(Conversation, count)
        .where(Conversation.workspace_id == ctx.workspace.id)
        .order_by(
            Conversation.last_message_at.desc().nullslast(), Conversation.created_at.desc()
        )
        .limit(100)
    )
    if not ctx.role.satisfies(Role.ADMIN):
        stmt = stmt.where(Conversation.user_id == ctx.user.id)

    return [
        ConversationOut(
            id=c.id,
            workspace_id=c.workspace_id,
            user_id=c.user_id,
            title=c.title,
            created_at=c.created_at,
            last_message_at=c.last_message_at,
            message_count=n,
        )
        for c, n in db.execute(stmt).all()
    ]


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[MessageOut],
    summary="Conversation history with citations",
)
def get_messages(
    conversation_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> list[MessageOut]:
    conversation, _ws, _role = _load_conversation(db, user, conversation_id)
    messages = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at)
    ).all()

    citations = db.scalars(
        select(Citation).where(
            Citation.message_id.in_([m.id for m in messages] or [uuid.uuid4()])
        )
    ).all()
    by_message: dict[uuid.UUID, list[Citation]] = {}
    for citation in citations:
        by_message.setdefault(citation.message_id, []).append(citation)

    out: list[MessageOut] = []
    for message in messages:
        dto = MessageOut.model_validate(message)
        dto.citations = [
            CitationOut.model_validate(c)
            for c in sorted(by_message.get(message.id, []), key=lambda c: c.marker)
        ]
        out.append(dto)
    return out


@router.delete(
    "/conversations/{conversation_id}",
    response_model=MessageDTO,
    summary="Delete a conversation",
)
def delete_conversation(
    conversation_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> MessageDTO:
    conversation, _ws, _role = _load_conversation(db, user, conversation_id)
    db.delete(conversation)
    db.commit()
    return MessageDTO(detail="Conversation deleted.")


# ── the stream ───────────────────────────────────────────────────────────


@router.post(
    "/conversations/{conversation_id}/messages",
    summary="Ask a question (Server-Sent Events stream)",
    response_class=StreamingResponse,
)
async def ask(
    conversation_id: uuid.UUID,
    payload: AskRequest,
    request: Request,
    user: CurrentUser,
    db: DbSession,
) -> StreamingResponse:
    """POST returning text/event-stream.

    POST rather than GET-with-query-param: a question can exceed URL length
    limits and must not be written into proxy access logs (§8).
    """
    conversation, workspace, role = _load_conversation(db, user, conversation_id)
    if not role.satisfies(Role.MEMBER):
        raise AuthorizationError("Requires MEMBER role to ask questions.")
    check_rate_limit(str(user.id), action="chat", limit=settings.rate_limit_chat_per_minute)

    question = payload.content.strip()

    # Persist the user's turn before streaming, so a disconnect mid-answer still
    # leaves a coherent transcript rather than an assistant reply to nothing.
    db.add(
        Message(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content=question,
        )
    )
    if conversation.title == "New conversation":
        conversation.title = _derive_title(question)
    db.commit()

    conversation_id_value = conversation.id
    workspace_org_id = workspace.org_id
    user_id = user.id

    async def event_stream() -> AsyncIterator[str]:
        # A dedicated session: the request-scoped one is closed by its dependency
        # when the handler returns, which happens the moment the response starts
        # streaming — long before this generator is finished with the database.
        stream_db = SessionLocal()
        outcome: chat_service.ChatOutcome | None = None
        try:
            conversation_row = stream_db.get(Conversation, conversation_id_value)
            if conversation_row is None:
                yield _sse("error", {"error": "not_found", "detail": "Conversation deleted."})
                return

            last_beat = asyncio.get_event_loop().time()

            async for event_name, data in chat_service.answer(
                stream_db,
                conversation=conversation_row,
                question=question,
                org_id=workspace_org_id,
                user_id=user_id,
            ):
                if event_name == "__outcome__":
                    # The sentinel frame carries the ChatOutcome rather than JSON.
                    assert isinstance(data, chat_service.ChatOutcome)
                    outcome = data
                    continue

                if await request.is_disconnected():
                    # Cooperative cancellation: closing the generator closes the
                    # provider stream. Without this an abandoned generation keeps
                    # billing tokens to completion (§15).
                    logger.info(
                        "client disconnected mid-stream",
                        extra={"event": "chat.client_abort"},
                    )
                    break

                yield _sse(event_name, data)

                now = asyncio.get_event_loop().time()
                if now - last_beat > HEARTBEAT_SECONDS:
                    yield ": heartbeat\n\n"
                    last_beat = now

            if outcome is not None:
                if await request.is_disconnected():
                    from app.db.models.enums import FinishReason

                    outcome.finish_reason = FinishReason.CLIENT_ABORT
                chat_service.persist(
                    stream_db,
                    conversation=conversation_row,
                    outcome=outcome,
                    org_id=workspace_org_id,
                    user_id=user_id,
                )
                stream_db.commit()
        except Exception as exc:
            logger.exception("sse stream failed")
            stream_db.rollback()
            yield _sse("error", {"error": "internal_error", "detail": str(exc)[:200]})
        finally:
            stream_db.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            # Disable buffering end to end. A compressing or buffering proxy will
            # hold the entire "stream" and deliver it at once, which is
            # indistinguishable from a slow backend and is the classic SSE
            # deployment bug (§15).
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post(
    "/messages/{message_id}/feedback",
    response_model=MessageDTO,
    summary="Rate an answer",
)
def submit_feedback(
    message_id: uuid.UUID, payload: FeedbackRequest, user: CurrentUser, db: DbSession
) -> MessageDTO:
    message = db.get(Message, message_id)
    if message is None:
        raise NotFoundError("Message not found.")
    _conversation, _ws, _role = _load_conversation(db, user, message.conversation_id)

    existing = db.scalar(
        select(Feedback).where(Feedback.message_id == message_id, Feedback.user_id == user.id)
    )
    if existing is not None:
        existing.rating = payload.rating
        existing.comment = payload.comment
    else:
        db.add(
            Feedback(
                message_id=message_id,
                user_id=user.id,
                rating=payload.rating,
                comment=payload.comment,
            )
        )
    db.commit()
    return MessageDTO(detail="Thanks for the feedback.")


def _derive_title(question: str) -> str:
    words = question.split()
    title = " ".join(words[:8])
    return (title[:120] + "…") if len(title) > 120 else (title or "New conversation")
