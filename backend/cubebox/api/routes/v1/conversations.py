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
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.db import get_session
from cubebox.db.engine import _build_database_url, async_session_maker
from cubebox.repositories import ConversationRepository
from cubebox.utils.time import utc_isoformat

router = APIRouter(prefix="/ws/{workspace_id}/conversations", tags=["conversations"])


async def _update_conversation_timestamp(
    conversation_id: str,
    *,
    org_id: str,
    workspace_id: str,
    user_id: str,
) -> None:
    """Update conversation timestamp using an isolated NullPool engine.

    Uses a dedicated connection so post-stream persistence does not
    depend on the request-scoped pool state.
    """
    save_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        async with AsyncSession(save_engine, expire_on_commit=False) as save_session:
            save_conv_repo = ConversationRepository(
                save_session,
                org_id=org_id,
                workspace_id=workspace_id,
                user_id=user_id,
            )
            await save_conv_repo.update_timestamp(conversation_id)
    finally:
        await save_engine.dispose()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    title: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    """Create a new conversation."""
    repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
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
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    """Get a conversation by ID."""
    repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
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
    ctx: Annotated[RequestContext, Depends(require_member)],
    limit: int = 20,
    offset: int = 0,
) -> dict[str, object]:
    """List conversations with pagination."""
    repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
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
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    """Update conversation title."""
    repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
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
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> None:
    """Delete a conversation."""
    repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
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


def _backfill_tool_call_delta_identity(
    evt_dict: dict[str, Any],
    delta_context: dict[tuple[str | None, int], dict[str, Any]],
) -> dict[str, Any]:
    """Fill missing tool_call_delta identity fields from prior chunks in the same stream."""
    if evt_dict.get("type") != "tool_call_delta":
        return evt_dict

    data = evt_dict.get("data")
    if not isinstance(data, dict):
        return evt_dict

    index = data.get("index")
    if not isinstance(index, int):
        return evt_dict

    key = (evt_dict.get("agent_id"), index)
    cached = delta_context.get(key, {})
    normalized_data = dict(data)

    if normalized_data.get("tool_call_id") is None and cached.get("tool_call_id") is not None:
        normalized_data["tool_call_id"] = cached["tool_call_id"]
    if normalized_data.get("name") is None and cached.get("name") is not None:
        normalized_data["name"] = cached["name"]

    delta_context[key] = {
        "tool_call_id": normalized_data.get("tool_call_id"),
        "name": normalized_data.get("name"),
    }
    return {**evt_dict, "data": normalized_data}


def _dicts_to_sse_events(
    event_dicts: list[dict[str, Any]],
    delta_context: dict[tuple[str | None, int], dict[str, Any]] | None = None,
) -> list[AgentEvent]:
    """Wrap raw event dicts from stream helpers into typed AgentEvent objects."""
    from cubebox.agents.schemas import (
        ArtifactEvent,
        ReasoningEvent,
        TextDeltaEvent,
        ToolCallDeltaEvent,
        ToolCallEvent,
        ToolResultEvent,
    )

    events: list[AgentEvent] = []
    for evt_dict in event_dicts:
        if delta_context is not None:
            evt_dict = _backfill_tool_call_delta_identity(evt_dict, delta_context)
        evt_type = evt_dict.get("type")
        if evt_type == "reasoning":
            events.append(
                ReasoningEvent(
                    timestamp=evt_dict["timestamp"],
                    data=evt_dict["data"],
                    agent_id=evt_dict.get("agent_id"),
                    agent_name=evt_dict.get("agent_name"),
                )
            )
        elif evt_type == "tool_call":
            events.append(
                ToolCallEvent(
                    timestamp=evt_dict["timestamp"],
                    data=evt_dict["data"],
                    agent_id=evt_dict.get("agent_id"),
                    agent_name=evt_dict.get("agent_name"),
                )
            )
        elif evt_type == "tool_result":
            events.append(
                ToolResultEvent(
                    timestamp=evt_dict["timestamp"],
                    data=evt_dict["data"],
                    agent_id=evt_dict.get("agent_id"),
                    agent_name=evt_dict.get("agent_name"),
                )
            )
        elif evt_type == "text_delta":
            events.append(
                TextDeltaEvent(
                    timestamp=evt_dict["timestamp"],
                    data=evt_dict["data"],
                    agent_id=evt_dict.get("agent_id"),
                    agent_name=evt_dict.get("agent_name"),
                )
            )
        elif evt_type == "tool_call_delta":
            events.append(
                ToolCallDeltaEvent(
                    timestamp=evt_dict["timestamp"],
                    data=evt_dict["data"],
                    agent_id=evt_dict.get("agent_id"),
                    agent_name=evt_dict.get("agent_name"),
                )
            )
        elif evt_type == "artifact":
            events.append(
                ArtifactEvent(
                    timestamp=evt_dict["timestamp"],
                    data=evt_dict["data"],
                    agent_id=evt_dict.get("agent_id"),
                    agent_name=evt_dict.get("agent_name"),
                )
            )

    return events


@router.post("/{conversation_id}/messages", status_code=status.HTTP_200_OK)
async def send_message(
    conversation_id: str,
    request_obj: SendMessageRequest,
    raw_request: Request,
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> StreamingResponse:
    """Send a user message and stream the assistant response via SSE."""
    # Use a short-lived session for the pre-check only.
    # Do NOT use Depends(get_session) here — it would hold a pooled connection
    # for the entire SSE stream duration, causing connection leaks on cancellation.
    async with async_session_maker() as session:
        conv_repo = ConversationRepository(
            session,
            org_id=ctx.org_id,
            workspace_id=ctx.workspace_id,
            user_id=ctx.user.id,
        )
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

    user_id: str = ctx.user.id
    org_id: str = ctx.org_id
    workspace_id: str = ctx.workspace_id

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

        from cubebox.middleware.citations.counter import (
            CitationCounter,
            citation_counter_var,
            citation_event_queue,
        )

        citation_counter = CitationCounter(start=1)
        cc_token = citation_counter_var.set(citation_counter)
        ce_token = citation_event_queue.set(event_q)

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

            # Get sandbox — DI or production (lazy: created on first tool use)
            sandbox_factory = getattr(raw_request.app.state, "sandbox_factory", None)
            if sandbox_factory:
                sandbox = sandbox_factory()
            else:
                from cubebox.config import config

                sandbox_enabled = config.get("sandbox.enabled", False)
                if sandbox_enabled:
                    try:
                        from cubebox.sandbox.lazy import LazySandbox
                        from cubebox.sandbox.manager import get_sandbox_manager

                        sandbox_manager = get_sandbox_manager()
                        sandbox = LazySandbox(
                            manager=sandbox_manager,
                            user_id=user_id,
                            org_id=org_id,
                            workspace_id=workspace_id,
                            workdir=config.get("sandbox.workdir", "/workspace"),
                        )
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

            # Load citation configs from MCP tool definitions
            from cubebox.middleware.citations import CitationConfig, load_citation_configs

            all_citation_configs: dict[str, CitationConfig] = {}
            try:
                from cubebox.config import config as app_config

                mcp_servers = app_config.get("mcp.servers", {})
                for _server_name, server_cfg in (mcp_servers or {}).items():
                    tool_defs = server_cfg.get("tools", [])
                    if tool_defs:
                        all_citation_configs.update(load_citation_configs(tool_defs))
            except Exception as e:
                logger.debug("Failed to load citation configs: {}", e)

            # Create agent
            from cubebox.agents.graph import create_cubebox_agent

            agent = create_cubebox_agent(
                llm=llm,
                tools=tools,
                sandbox=sandbox,
                conversation_id=conversation_id,
                org_id=org_id,
                workspace_id=workspace_id,
                skills=raw_request.app.state.skills,
                checkpointer=checkpointer,
                citation_configs=all_citation_configs,
                event_queue=event_q,
            )

            config_dict = {"configurable": {"thread_id": conversation_id}}

            # Recover citation counter from conversation history
            import re as _re

            try:
                from langchain_core.runnables import RunnableConfig

                state = await agent.aget_state(
                    RunnableConfig(configurable={"thread_id": conversation_id})
                )
                if state and state.values:
                    history_messages = state.values.get("messages", [])
                    max_citation_id = 0
                    _citation_pattern = _re.compile(r"【(\d+)-\d+】")
                    for msg in history_messages:
                        msg_content = getattr(msg, "content", "") or ""
                        if isinstance(msg_content, str):
                            for match in _citation_pattern.finditer(msg_content):
                                cid = int(match.group(1))
                                if cid > max_citation_id:
                                    max_citation_id = cid
                    if max_citation_id > 0:
                        citation_counter._next = max_citation_id + 1
                        logger.debug(
                            "Recovered citation counter: next_id={}",
                            max_citation_id + 1,
                        )
            except Exception as e:
                logger.debug("Could not recover citation counter: {}", e)

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
            tool_delta_context: dict[tuple[str | None, int], dict[str, Any]] = {}

            _citation_buffers: dict[str | None, str] = {}

            def _process_text_delta(
                sse_event: Any,
                agent_key: str | None,
            ) -> list[str]:
                results: list[str] = []
                content = sse_event.data.get("content", "")
                buf = _citation_buffers.get(agent_key, "")
                content = buf + content
                _citation_buffers[agent_key] = ""
                last_open = content.rfind("【")
                if last_open != -1 and "】" not in content[last_open:]:
                    _citation_buffers[agent_key] = content[last_open:]
                    content = content[:last_open]
                if content:
                    sse_event.data["content"] = content
                    results.append(f"data: {sse_event.model_dump_json()}\n\n")
                return results

            def _flush_citation_buffer(
                agent_key: str | None,
                fallback_agent_id: str | None,
            ) -> list[str]:
                results: list[str] = []
                buf = _citation_buffers.get(agent_key, "")
                if buf:
                    from cubebox.agents.schemas import TextDeltaEvent

                    flush_evt = TextDeltaEvent(
                        timestamp=datetime.now(UTC).isoformat(),
                        data={
                            "content": buf,
                            "usage": {
                                "input_tokens": 0,
                                "output_tokens": 0,
                            },
                        },
                        agent_id=fallback_agent_id,
                    )
                    _citation_buffers[agent_key] = ""
                    results.append(f"data: {flush_evt.model_dump_json()}\n\n")
                return results

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
                        for sse_event in _dicts_to_sse_events(evts, tool_delta_context):
                            if sse_event.type == "text_delta":
                                for chunk in _process_text_delta(
                                    sse_event,
                                    agent_id,
                                ):
                                    yield chunk
                            else:
                                for chunk in _flush_citation_buffer(
                                    agent_id,
                                    sse_event.agent_id,
                                ):
                                    yield chunk
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
                        for sse_event in _dicts_to_sse_events(evts, tool_delta_context):
                            if sse_event.type == "text_delta":
                                for chunk in _process_text_delta(
                                    sse_event,
                                    sa_agent_id,
                                ):
                                    yield chunk
                            else:
                                for chunk in _flush_citation_buffer(
                                    sa_agent_id,
                                    sse_event.agent_id,
                                ):
                                    yield chunk
                                yield f"data: {sse_event.model_dump_json()}\n\n"

                elif kind == "citation":
                    from cubebox.agents.schemas import CitationEvent

                    citation_data = item[2]
                    citation_agent_id = item[1]
                    citation_event = CitationEvent(
                        timestamp=datetime.now(UTC).isoformat(),
                        data=citation_data,
                        agent_id=citation_agent_id,
                    )
                    yield f"data: {citation_event.model_dump_json()}\n\n"

                elif kind == "error":
                    raise item[2]

            for agent_key in list(_citation_buffers):
                for chunk in _flush_citation_buffer(agent_key, agent_key):
                    yield chunk

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
            try:
                citation_counter_var.reset(cc_token)
            except ValueError:
                citation_counter_var.set(None)
            try:
                citation_event_queue.reset(ce_token)
            except ValueError:
                citation_event_queue.set(None)

            if sandbox:
                from cubebox.sandbox.lazy import LazySandbox

                if isinstance(sandbox, LazySandbox) and sandbox.initialized:
                    try:
                        await sandbox._manager.release(
                            sandbox.id, org_id=org_id, workspace_id=workspace_id
                        )
                    except Exception as e:
                        logger.warning("Error releasing sandbox: {}", e)
                elif sandbox_manager and not isinstance(sandbox, LazySandbox):
                    try:
                        await sandbox_manager.release(
                            sandbox.id, org_id=org_id, workspace_id=workspace_id
                        )
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
                    await _update_conversation_timestamp(
                        conversation_id,
                        org_id=org_id,
                        workspace_id=workspace_id,
                        user_id=user_id,
                    )
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
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    """List messages in a conversation, read from LangGraph thread state."""
    conv_repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
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
