"""Conversations API routes."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.agents.executor import DeepAgentExecutor
from cubebox.agents.schemas import DoneEvent
from cubebox.api.exceptions import ExecutionError, InternalError, InvalidInputError
from cubebox.db import get_session
from cubebox.db.engine import _build_database_url
from cubebox.repositories import ConversationRepository, MessageRepository

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    title: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """Create a new conversation."""
    repo = ConversationRepository(session)
    conversation = await repo.create(title=title)
    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
    }


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """Get a conversation by ID."""
    repo = ConversationRepository(session)
    conversation = await repo.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
    }


@router.get("")
async def list_conversations(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = 20,
    offset: int = 0,
) -> dict[str, object]:
    """List conversations with pagination."""
    repo = ConversationRepository(session)
    conversations, total = await repo.list_all(limit=limit, offset=offset)
    return {
        "conversations": [
            {
                "id": c.id,
                "title": c.title,
                "created_at": c.created_at.isoformat(),
                "updated_at": c.updated_at.isoformat(),
            }
            for c in conversations
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.patch("/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    title: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """Update conversation title."""
    repo = ConversationRepository(session)
    conversation = await repo.update_title(conversation_id, title)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
    }


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Delete a conversation."""
    repo = ConversationRepository(session)
    deleted = await repo.delete(conversation_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )


class SendMessageRequest(BaseModel):
    """Request body for sending a message."""

    content: str
    sandbox_domain: str | None = None
    sandbox_image: str | None = None


@router.post("/{conversation_id}/messages", status_code=status.HTTP_200_OK)
async def send_message(
    conversation_id: str,
    request: SendMessageRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    """Send a user message to a conversation and stream assistant response via SSE."""
    conv_repo = ConversationRepository(session)
    conversation = await conv_repo.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    if not request.content or not request.content.strip():
        raise InvalidInputError(
            message="Content field cannot be empty",
            details="Please provide a non-empty content string",
        )

    # Save user message first
    msg_repo = MessageRepository(session)
    await msg_repo.create(
        conversation_id=conversation_id,
        role="user",
        content=request.content,
    )

    async def event_generator() -> AsyncIterator[str]:
        collected_events: list[dict[str, object]] = []
        checkpointer = None

        try:
            from cubebox.agents.checkpointer import create_checkpointer

            checkpointer = await create_checkpointer()
            executor = DeepAgentExecutor(
                sandbox_domain=request.sandbox_domain,
                sandbox_image=request.sandbox_image,
                checkpointer=checkpointer,
            )

            async for event in executor.stream(request.content, thread_id=conversation_id):
                event_dict = event.model_dump()
                collected_events.append(event_dict)
                yield f"data: {json.dumps(event_dict)}\n\n"

        except InvalidInputError as e:
            logger.error("Invalid input error: {}", str(e))
            error_event = e.to_error_event()
            event_dict = error_event.model_dump()
            collected_events.append(event_dict)
            yield f"data: {json.dumps(event_dict)}\n\n"
            done = DoneEvent(timestamp=datetime.now(UTC).isoformat())
            yield f"data: {done.model_dump_json()}\n\n"

        except ExecutionError as e:
            logger.error("Execution error: {}", str(e))
            error_event = e.to_error_event()
            event_dict = error_event.model_dump()
            collected_events.append(event_dict)
            yield f"data: {json.dumps(event_dict)}\n\n"
            done = DoneEvent(timestamp=datetime.now(UTC).isoformat())
            yield f"data: {done.model_dump_json()}\n\n"

        except Exception as e:  # noqa: BLE001
            logger.error("Unexpected error: {}", str(e), exc_info=True)
            error = InternalError(
                message="An unexpected error occurred during execution",
                details=str(e),
            )
            error_event = error.to_error_event()
            event_dict = error_event.model_dump()
            collected_events.append(event_dict)
            yield f"data: {json.dumps(event_dict)}\n\n"
            done = DoneEvent(timestamp=datetime.now(UTC).isoformat())
            yield f"data: {done.model_dump_json()}\n\n"

        finally:
            # Close checkpointer connection if created
            if checkpointer is not None:
                try:
                    # Get the underlying connection and close it
                    if hasattr(checkpointer, "conn"):
                        checkpointer.conn.close()
                        logger.debug("Closed checkpointer connection")
                except Exception as e:
                    logger.warning("Error closing checkpointer connection: {}", str(e))

            # Create a new engine bound to the current event loop to avoid cross-loop issues
            save_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
            try:
                async with AsyncSession(save_engine, expire_on_commit=False) as save_session:
                    save_msg_repo = MessageRepository(save_session)
                    await save_msg_repo.create(
                        conversation_id=conversation_id,
                        role="assistant",
                        events=collected_events,
                    )
                    save_conv_repo = ConversationRepository(save_session)
                    await save_conv_repo.update_timestamp(conversation_id)
            finally:
                await save_engine.dispose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = 100,
    offset: int = 0,
) -> dict[str, object]:
    """List messages in a conversation with pagination."""
    conv_repo = ConversationRepository(session)
    conversation = await conv_repo.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    msg_repo = MessageRepository(session)
    messages, total = await msg_repo.list_by_conversation(
        conversation_id, limit=limit, offset=offset
    )
    return {
        "messages": [
            {
                "id": m.id,
                "conversation_id": m.conversation_id,
                "role": m.role,
                "content": m.content,
                "events": m.events,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
