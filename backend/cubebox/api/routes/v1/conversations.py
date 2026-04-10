"""Conversations API routes."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.agents.schemas import AgentEvent, DoneEvent, StatusEvent
from cubebox.api.exceptions import InternalError, InvalidInputError
from cubebox.db import get_session
from cubebox.db.engine import _build_database_url, async_session_maker
from cubebox.repositories import ConversationRepository
from cubebox.utils.time import utc_isoformat

router = APIRouter(prefix="/conversations", tags=["conversations"])


async def _update_conversation_timestamp(conversation_id: str) -> None:
    """Update conversation timestamp using an isolated NullPool engine.

    Uses a dedicated connection so post-stream persistence does not
    depend on the request-scoped pool state.
    """
    save_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        async with AsyncSession(save_engine, expire_on_commit=False) as save_session:
            save_conv_repo = ConversationRepository(save_session)
            await save_conv_repo.update_timestamp(conversation_id)
    finally:
        await save_engine.dispose()


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
        "created_at": utc_isoformat(conversation.created_at),
        "updated_at": utc_isoformat(conversation.updated_at),
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
        "created_at": utc_isoformat(conversation.created_at),
        "updated_at": utc_isoformat(conversation.updated_at),
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
                "created_at": utc_isoformat(c.created_at),
                "updated_at": utc_isoformat(c.updated_at),
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
        "created_at": utc_isoformat(conversation.created_at),
        "updated_at": utc_isoformat(conversation.updated_at),
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


def _dicts_to_sse_events(event_dicts: list[dict[str, Any]]) -> list[AgentEvent]:
    """Wrap raw event dicts from stream helpers into typed AgentEvent objects."""
    from cubebox.agents.schemas import (
        ArtifactEvent,
        ReasoningEvent,
        TextDeltaEvent,
        ToolCallEvent,
        ToolResultEvent,
    )

    events: list[AgentEvent] = []
    for evt_dict in event_dicts:
        evt_type = evt_dict.get("type")
        if evt_type == "reasoning":
            events.append(
                ReasoningEvent(
                    timestamp=evt_dict["timestamp"],
                    data=evt_dict["data"],
                    agent_id=evt_dict.get("agent_id"),
                )
            )
        elif evt_type == "tool_call":
            events.append(
                ToolCallEvent(
                    timestamp=evt_dict["timestamp"],
                    data=evt_dict["data"],
                    agent_id=evt_dict.get("agent_id"),
                )
            )
        elif evt_type == "tool_result":
            events.append(
                ToolResultEvent(
                    timestamp=evt_dict["timestamp"],
                    data=evt_dict["data"],
                    agent_id=evt_dict.get("agent_id"),
                )
            )
        elif evt_type == "text_delta":
            events.append(
                TextDeltaEvent(
                    timestamp=evt_dict["timestamp"],
                    data=evt_dict["data"],
                    agent_id=evt_dict.get("agent_id"),
                )
            )
        elif evt_type == "artifact":
            events.append(
                ArtifactEvent(
                    timestamp=evt_dict["timestamp"],
                    data=evt_dict["data"],
                    agent_id=evt_dict.get("agent_id"),
                )
            )

    return events


@router.post("/{conversation_id}/messages", status_code=status.HTTP_200_OK)
async def send_message(
    conversation_id: str,
    request_obj: SendMessageRequest,
    raw_request: Request,
) -> StreamingResponse:
    """Send a user message and stream the assistant response via SSE."""
    # Use a short-lived session for the pre-check only.
    # Do NOT use Depends(get_session) here — it would hold a pooled connection
    # for the entire SSE stream duration, causing connection leaks on cancellation.
    async with async_session_maker() as session:
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
        from cubebox.middleware.subagents import subagent_event_queue

        checkpointer = None
        sandbox = None
        sandbox_manager = None
        sandbox_create_task: asyncio.Task[Any] | None = None
        stream_task: asyncio.Task[None] | None = None
        request_cancelled = False

        # Unified event queue: both the main agent stream and subagent callbacks
        # push events here so the SSE generator can multiplex them.
        event_q: asyncio.Queue[tuple[str, Any, Any] | None] = asyncio.Queue()
        cv_token = subagent_event_queue.set(event_q)

        def _status(phase: str, detail: str | None = None) -> str:
            data: dict[str, str] = {"phase": phase}
            if detail:
                data["detail"] = detail
            evt = StatusEvent(
                timestamp=datetime.now(UTC).isoformat(),
                data=data,
            )
            return f"data: {evt.model_dump_json()}\n\n"

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

                        yield _status("sandbox_creating")
                        sandbox_manager = get_sandbox_manager()
                        sandbox_create_task = asyncio.create_task(
                            sandbox_manager.get_or_create(user_id)
                        )
                        # Send heartbeat comments every 10s to keep proxies alive
                        while not sandbox_create_task.done():
                            try:
                                await asyncio.wait_for(
                                    asyncio.shield(sandbox_create_task), timeout=10
                                )
                            except TimeoutError:
                                yield ": heartbeat\n\n"
                        sandbox = sandbox_create_task.result()
                        yield _status("sandbox_ready")
                    except Exception as e:
                        logger.warning("Sandbox unavailable, continuing without: {}", e)
                        yield _status("sandbox_failed", detail=str(e))

            # Create LLM
            from cubebox.llm.factory import LLMFactory

            factory_llm = LLMFactory()
            llm = factory_llm.create_default()

            # Get tools from registry
            from cubebox.tools import get_registry

            tools = get_registry().list_tools()

            # Create agent
            from cubebox.agents.graph import create_cubebox_agent

            agent = create_cubebox_agent(
                llm=llm,
                tools=tools,
                sandbox=sandbox,
                conversation_id=conversation_id,
                skills=raw_request.app.state.skills,
                checkpointer=checkpointer,
            )

            config_dict = {"configurable": {"thread_id": conversation_id}}

            async def _drain_main_stream() -> None:
                """Push main agent stream events into the unified queue.

                Uses ``stream_mode=["messages", "updates"]``:
                - ``messages``: real-time token chunks (text, reasoning)
                - ``updates``:  complete node outputs (tool_call, tool_result)

                With ``stream_subgraphs=True`` each event is wrapped as
                ``(namespace_tuple, (mode, data))``.
                """
                try:
                    human_msg = HumanMessage(
                        content=request_obj.content,
                        response_metadata={
                            "created_at": datetime.now(UTC).isoformat(),
                        },
                    )
                    async for event in agent.astream(  # type: ignore[call-overload]
                        {"messages": [human_msg]},
                        stream_mode=["messages", "updates"],
                        stream_subgraphs=True,
                        config=config_dict,
                    ):
                        ns: tuple[Any, ...] = ()
                        payload = event
                        # stream_subgraphs wraps as (ns_tuple, payload)
                        if isinstance(event, tuple) and len(event) == 2:
                            first, second = event
                            if isinstance(first, tuple):
                                ns = first
                                payload = second
                        await event_q.put(("main", ns, payload))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await event_q.put(("error", None, exc))
                finally:
                    with suppress(Exception):
                        await event_q.put(None)  # sentinel: main stream done

            from cubebox.agents.stream import (
                convert_messages_chunk,
                convert_updates_chunk,
            )

            stream_task = asyncio.create_task(_drain_main_stream())

            while True:
                try:
                    item = await asyncio.wait_for(event_q.get(), timeout=15)
                except TimeoutError:
                    # Send SSE comment as heartbeat to keep connection alive
                    # during long LLM calls
                    yield ": heartbeat\n\n"
                    continue
                if item is None:
                    break

                kind = item[0]

                if kind == "main":
                    ns, payload = item[1], item[2]
                    agent_id = _ns_to_agent_id(ns)
                    # Dual stream mode yields (mode, data) tuples
                    if isinstance(payload, tuple) and len(payload) == 2:
                        mode, data = payload
                        if mode == "messages":
                            evts = convert_messages_chunk(data, agent_id=agent_id)
                        elif mode == "updates":
                            evts = convert_updates_chunk(data, agent_id=agent_id)
                        else:
                            evts = []
                        for sse_event in _dicts_to_sse_events(evts):
                            yield f"data: {sse_event.model_dump_json()}\n\n"

                elif kind == "subagent":
                    sa_agent_id, payload = item[1], item[2]
                    # Subagent also uses dual stream mode
                    if isinstance(payload, tuple) and len(payload) == 2:
                        mode, data = payload
                        if mode == "messages":
                            evts = convert_messages_chunk(data, agent_id=sa_agent_id)
                        elif mode == "updates":
                            evts = convert_updates_chunk(data, agent_id=sa_agent_id)
                        else:
                            evts = []
                        for sse_event in _dicts_to_sse_events(evts):
                            yield f"data: {sse_event.model_dump_json()}\n\n"

                elif kind == "error":
                    raise item[2]

            await stream_task

        except asyncio.CancelledError:
            request_cancelled = True
            logger.debug("SSE stream cancelled for conversation {}", conversation_id)
            raise
        except Exception as e:
            logger.error("Streaming error: {}", str(e), exc_info=True)
            error = InternalError(
                message="An unexpected error occurred during execution",
                details=str(e),
            )
            error_event = error.to_error_event()
            yield f"data: {error_event.model_dump_json()}\n\n"

        finally:
            if stream_task is not None and not stream_task.done():
                stream_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stream_task

            if sandbox_create_task is not None and not sandbox_create_task.done():
                sandbox_create_task.cancel()
                with suppress(asyncio.CancelledError):
                    await sandbox_create_task

            try:
                subagent_event_queue.reset(cv_token)
            except ValueError:
                # Async generator cancellation may resume cleanup in a different
                # task/context; fall back to clearing the per-request queue.
                subagent_event_queue.set(None)

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

            # Only emit terminal persistence/done events for completed streams.
            if not request_cancelled:
                try:
                    await _update_conversation_timestamp(conversation_id)
                except Exception as e:
                    logger.warning("Error updating conversation timestamp: {}", e)

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
