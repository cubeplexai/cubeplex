"""Conversations API routes."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.agents.schemas import AgentEvent, DoneEvent
from cubebox.api.exceptions import InternalError, InvalidInputError
from cubebox.db import get_session
from cubebox.db.engine import _build_database_url
from cubebox.repositories import ConversationRepository

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


def _ns_to_agent_id(ns: tuple[Any, ...]) -> str | None:
    """Convert LangGraph namespace tuple to agent_id string."""
    if not ns:
        return None
    return ":".join(str(part) for part in ns)


def _convert_stream_chunk(chunk: Any, ns: tuple[Any, ...] = ()) -> list[AgentEvent]:
    """Convert a LangGraph stream chunk to SSE events."""
    from cubebox.agents.schemas import (
        ReasoningEvent,
        TextDeltaEvent,
        ToolCallEvent,
        ToolResultEvent,
    )

    timestamp = datetime.now(UTC).isoformat()
    agent_id = _ns_to_agent_id(ns)
    events: list[AgentEvent] = []

    if not isinstance(chunk, tuple) or len(chunk) < 2:
        return events

    msg, metadata = chunk

    # Handle both dict and message object
    if isinstance(msg, dict):
        content = msg.get("content", "")
        additional_kwargs = msg.get("additional_kwargs", {})
        tool_calls = msg.get("tool_calls", [])
        usage_metadata = msg.get("usage_metadata", {})
        tool_name = msg.get("name")
    else:
        content = getattr(msg, "content", "") or ""
        additional_kwargs = getattr(msg, "additional_kwargs", {}) or {}
        tool_calls = getattr(msg, "tool_calls", []) or []
        usage_metadata = getattr(msg, "usage_metadata", {}) or {}
        tool_name = getattr(msg, "name", None)

    # Reasoning content
    reasoning_content = (additional_kwargs or {}).get("reasoning_content", "")
    if reasoning_content:
        events.append(
            ReasoningEvent(
                timestamp=timestamp,
                data={"content": reasoning_content},
                agent_id=agent_id,
            )
        )

    # Tool calls
    if tool_calls:
        for tc in tool_calls:
            tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
            tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
            tc_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
            if not tc_name:
                continue
            events.append(
                ToolCallEvent(
                    timestamp=timestamp,
                    data={"tool_call_id": tc_id, "name": tc_name, "arguments": tc_args},
                    agent_id=agent_id,
                )
            )

    # Tool result (ToolMessage: has name and content)
    if tool_name and content:
        events.append(
            ToolResultEvent(
                timestamp=timestamp,
                data={
                    "tool_name": tool_name,
                    "content": content if isinstance(content, str) else str(content),
                },
                agent_id=agent_id,
            )
        )
        return events

    # Text content
    if content:
        events.append(
            TextDeltaEvent(
                timestamp=timestamp,
                data={
                    "content": content,
                    "usage": {
                        "input_tokens": (usage_metadata or {}).get("input_tokens", 0),
                        "output_tokens": (usage_metadata or {}).get("output_tokens", 0),
                    },
                },
                agent_id=agent_id,
            )
        )

    return events


@router.post("/{conversation_id}/messages", status_code=status.HTTP_200_OK)
async def send_message(
    conversation_id: str,
    request_obj: SendMessageRequest,
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    """Send a user message and stream the assistant response via SSE."""
    conv_repo = ConversationRepository(session)
    conversation = await conv_repo.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    if not request_obj.content or not request_obj.content.strip():
        raise InvalidInputError(
            message="Content field cannot be empty",
            details="Please provide a non-empty content string",
        )

    user_id: str = getattr(raw_request.state, "user_id", "anonymous")

    async def event_generator() -> AsyncIterator[str]:
        checkpointer = None
        sandbox = None
        sandbox_manager = None

        try:
            # Get checkpointer — DI or production
            factory = getattr(raw_request.app.state, "checkpointer_factory", None)
            if factory:
                checkpointer = factory()
            else:
                from cubebox.agents.checkpointer import create_checkpointer

                checkpointer = await create_checkpointer()

            # Get sandbox — DI or production
            sandbox_factory = getattr(raw_request.app.state, "sandbox_factory", None)
            if sandbox_factory:
                sandbox = sandbox_factory()
            else:
                from cubebox.config import config

                sandbox_enabled = config.get("sandbox.enabled", False)
                if sandbox_enabled:
                    try:
                        from cubebox.sandbox.manager import get_sandbox_manager

                        sandbox_manager = get_sandbox_manager()
                        sandbox = await sandbox_manager.get_or_create(user_id)
                    except Exception as e:
                        logger.warning("Sandbox unavailable, continuing without: {}", e)

            # Create LLM
            from cubebox.llm.factory import LLMFactory

            factory_llm = LLMFactory()
            providers = factory_llm.list_providers()
            llm = factory_llm.create(
                model_id=factory_llm.list_models(providers[0])[0],
                provider_name=providers[0],
            )

            # Get tools from registry
            from cubebox.tools import get_registry

            tools = get_registry().list_tools()

            # Create agent
            from cubebox.agents.graph import create_cubebox_agent

            agent = create_cubebox_agent(
                llm=llm,
                tools=tools,
                sandbox=sandbox,
                checkpointer=checkpointer,
            )

            config_dict = {"configurable": {"thread_id": conversation_id}}

            async for event in agent.astream(  # type: ignore[call-arg]
                {"messages": [HumanMessage(content=request_obj.content)]},
                stream_mode="messages",
                stream_subgraphs=True,
                config=config_dict,  # type: ignore[arg-type]
            ):
                ns: tuple[Any, ...] = ()
                chunk = event
                if isinstance(event, tuple) and len(event) == 2:
                    first, second = event
                    if isinstance(first, tuple):
                        ns = first
                        chunk = second

                for sse_event in _convert_stream_chunk(chunk, ns=ns):
                    yield f"data: {sse_event.model_dump_json()}\n\n"

        except Exception as e:
            logger.error("Streaming error: {}", str(e), exc_info=True)
            error = InternalError(
                message="An unexpected error occurred during execution",
                details=str(e),
            )
            error_event = error.to_error_event()
            yield f"data: {error_event.model_dump_json()}\n\n"

        finally:
            if sandbox_manager and sandbox:
                try:
                    await sandbox_manager.release(sandbox.id)
                except Exception as e:
                    logger.warning("Error releasing sandbox: {}", e)

            if checkpointer is not None and hasattr(checkpointer, "conn"):
                try:
                    checkpointer.conn.close()
                except Exception as e:
                    logger.warning("Error closing checkpointer: {}", e)

            # Update conversation timestamp
            save_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
            try:
                async with AsyncSession(save_engine, expire_on_commit=False) as save_session:
                    save_conv_repo = ConversationRepository(save_session)
                    await save_conv_repo.update_timestamp(conversation_id)
            finally:
                await save_engine.dispose()

            done = DoneEvent(timestamp=datetime.now(UTC).isoformat())
            yield f"data: {done.model_dump_json()}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """List messages in a conversation, read from LangGraph thread state."""
    conv_repo = ConversationRepository(session)
    conversation = await conv_repo.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    # Get checkpointer — DI or production
    factory = getattr(raw_request.app.state, "checkpointer_factory", None)
    if factory:
        checkpointer = factory()
    else:
        from cubebox.agents.checkpointer import create_checkpointer

        checkpointer = await create_checkpointer()

    if checkpointer is None:
        return {"messages": [], "total": 0}

    try:
        config = {"configurable": {"thread_id": conversation_id}}
        checkpoint = await checkpointer.aget(config)
        if not checkpoint:
            return {"messages": [], "total": 0}

        from cubebox.agents.convert import convert_to_api_messages

        lc_messages = checkpoint["channel_values"].get("messages", [])
        messages = convert_to_api_messages(lc_messages)
        return {"messages": messages, "total": len(messages)}
    finally:
        if hasattr(checkpointer, "conn"):
            try:
                checkpointer.conn.close()
            except Exception as e:
                logger.warning("Error closing checkpointer: {}", e)
