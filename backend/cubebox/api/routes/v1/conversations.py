"""Conversations API routes."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.agents.schemas import AgentEvent
from cubebox.api.exceptions import InvalidInputError
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.cache import RedisHandle, redis_dep
from cubebox.db import get_session
from cubebox.db.engine import _build_database_url, async_session_maker
from cubebox.models import Conversation
from cubebox.repositories import ConversationRepository
from cubebox.streams.run_events import (
    get_active_run,
    get_latest_event_id,
    get_run_meta,
    is_stale_meta,
    iter_run_events,
    mark_run_stale,
    read_run_events_after,
)
from cubebox.streams.run_manager import RunContext
from cubebox.utils.time import utc_isoformat

router = APIRouter(prefix="/ws/{workspace_id}/conversations", tags=["conversations"])


def _serialize_conversation(c: Conversation) -> dict[str, object]:
    return {
        "id": c.id,
        "title": c.title,
        "is_pinned": c.is_pinned,
        "created_at": utc_isoformat(c.created_at),
        "updated_at": utc_isoformat(c.updated_at),
    }


async def _update_conversation_timestamp(
    conversation_id: str,
    *,
    org_id: str,
    workspace_id: str,
    user_id: str,
) -> None:
    """Mark conversation as active and refresh its timestamp.

    Uses a dedicated NullPool connection so post-stream persistence does not
    depend on the request-scoped pool state. Always sets has_messages=True
    and bumps updated_at, so the conversation is visible in ``list_all`` and
    its position in the recency-ordered list reflects the latest activity.
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
            await save_conv_repo.mark_active(conversation_id)
    finally:
        await save_engine.dispose()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    title: str = "",
    draft: bool = False,
) -> dict[str, object]:
    """Create a new conversation.

    When ``draft=true`` the conversation is created hidden — it stays out of
    the list endpoint until the user actually sends a message. The home page
    uses this for the eager-create-on-file-pick flow so abandoned drafts
    don't clutter the sidebar.
    """
    repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    conversation = await repo.create(title=title, draft=draft)
    return _serialize_conversation(conversation)


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
    return _serialize_conversation(conversation)


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
        "conversations": [_serialize_conversation(c) for c in conversations],
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
    return _serialize_conversation(conversation)


class PinRequest(BaseModel):
    """Request body for pin endpoint."""

    is_pinned: bool


@router.patch("/{conversation_id}/pin")
async def set_pin(
    conversation_id: str,
    body: PinRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    """Set the pinned state of a conversation (idempotent)."""
    repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    conversation = await repo.set_pin(conversation_id, body.is_pinned)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    return _serialize_conversation(conversation)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> None:
    """Soft-delete a conversation.

    Stamps ``deleted_at`` and hides the row from subsequent reads. Child
    rows (billing events for cost audit, artifacts, attachments) are kept
    so their FK targets stay valid and cost reports survive. A separate
    GC job is the right place to permanently purge old soft-deleted rows.
    """
    repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    deleted = await repo.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )


class GenerateTitleRequest(BaseModel):
    """Request body for generate-title endpoint."""

    content: str


@router.post("/{conversation_id}/generate-title")
async def generate_title(
    conversation_id: str,
    body: GenerateTitleRequest,
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    """Generate a short title from the user's first message.

    Thin wrapper — gating, LLM invocation, and atomic compare-and-set live
    in :mod:`cubebox.services.conversation_title`.
    """
    from cubebox.services.conversation_title import generate_and_apply_title

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

    backend = getattr(raw_request.app.state, "encryption_backend", None)
    conversation = await generate_and_apply_title(
        repo=repo,
        session=session,
        org_id=ctx.org_id,
        encryption_backend=backend,
        conversation=conversation,
        content=body.content,
    )
    return _serialize_conversation(conversation)


class SendMessageRequest(BaseModel):
    """Request body for sending a message."""

    content: str = ""
    attachments: list[str] = []


class SendMessageResponse(BaseModel):
    """Response body for starting a new run."""

    run_id: str


def _build_run_streaming_response(
    *,
    raw_request: Request,
    conversation_id: str,
    run_id: str,
    redis_handle: RedisHandle,
) -> StreamingResponse:
    """Build an SSE response that replays and tails a run event stream."""
    redis = redis_handle.client
    prefix = redis_handle.key_prefix

    from cubebox.config import config

    # Clamp BLOCK to stay strictly under the Redis socket_timeout. redis-py
    # applies socket_timeout to blocking commands, so a BLOCK >= socket_timeout
    # causes XREAD to raise TimeoutError mid-read even though Redis is healthy.
    # Use 80% of the socket timeout so the invariant holds for any socket
    # timeout (a fixed subtraction would underflow for low timeouts and a
    # 1000ms floor would equal a 1s socket_timeout — both regress to TimeoutError).
    socket_timeout_s = config.get("redis.socket_timeout_seconds", 10)
    configured_block_ms = config.get("streaming.run_stream_block_ms", 5000)
    safe_block_ms = max(100, int(socket_timeout_s * 1000 * 0.8))
    block_ms = min(configured_block_ms, safe_block_ms)
    last_event_id = raw_request.headers.get("last-event-id")

    async def event_generator() -> AsyncIterator[str]:
        target_event_id = await get_latest_event_id(redis, prefix=prefix, run_id=run_id)
        replay_start = f"({last_event_id}" if last_event_id else None
        replay_cursor = last_event_id

        if target_event_id is not None:
            replay_events = await iter_run_events(
                redis,
                prefix=prefix,
                run_id=run_id,
                start=replay_start,
                stop=target_event_id,
            )
            for event in replay_events:
                replay_cursor = event.event_id
                yield _format_sse_event(event.event_id, event.payload)
                if event.payload.get("type") in {"done", "error"}:
                    return

        live_cursor = replay_cursor or target_event_id or "$"
        while True:
            events = await read_run_events_after(
                redis,
                prefix=prefix,
                run_id=run_id,
                last_event_id=live_cursor,
                block_ms=block_ms,
            )
            if not events:
                active_run = await get_active_run(
                    redis,
                    prefix=prefix,
                    conversation_id=conversation_id,
                )
                latest_event_id = await get_latest_event_id(redis, prefix=prefix, run_id=run_id)
                if active_run is None and latest_event_id == live_cursor:
                    return
                yield ": heartbeat\n\n"
                continue

            for event in events:
                live_cursor = event.event_id
                yield _format_sse_event(event.event_id, event.payload)
                if event.payload.get("type") in {"done", "error"}:
                    return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _ns_to_agent_id(ns: tuple[Any, ...]) -> str | None:
    """Convert cubepi namespace tuple to agent_id string."""
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


@router.post(
    "/{conversation_id}/messages",
    status_code=status.HTTP_200_OK,
    response_model=None,
)
async def send_message(
    conversation_id: str,
    request_obj: SendMessageRequest,
    raw_request: Request,
    ctx: Annotated[RequestContext, Depends(require_member)],
    rds: Annotated[RedisHandle, Depends(redis_dep)],
) -> SendMessageResponse | StreamingResponse:
    """Send a user message and start a background run."""
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

    if not (request_obj.content and request_obj.content.strip()) and not request_obj.attachments:
        raise InvalidInputError(
            message="Message must include content or attachments",
            details="Provide content text and/or one or more file attachments",
        )

    from cubebox.api.exceptions import (
        AttachmentReferenceInvalidError,
        AttachmentTooManyError,
    )
    from cubebox.config import config as _cfg

    max_per_msg = int(_cfg.get("attachments.max_per_message", 10))
    if len(request_obj.attachments) > max_per_msg:
        raise AttachmentTooManyError(
            count=len(request_obj.attachments),
            limit=max_per_msg,
        )

    if request_obj.attachments:
        from cubebox.repositories import AttachmentRepository

        async with async_session_maker() as att_session:
            att_repo = AttachmentRepository(
                att_session,
                org_id=ctx.org_id,
                workspace_id=ctx.workspace_id,
            )
            for fid in request_obj.attachments:
                row = await att_repo.get_in_conversation(
                    conversation_id=conversation_id,
                    attachment_id=fid,
                )
                if row is None or row.status not in {"pending", "attached"}:
                    raise AttachmentReferenceInvalidError(fid)

            # Flip pending → attached synchronously, before the run starts.
            # The background _execute_run path also calls this (idempotent),
            # but doing it here closes a race where the client navigates to
            # the conversation page and rehydrates `pending` attachments
            # back into the InputBar staging area.
            await att_repo.mark_attached_bulk(
                conversation_id=conversation_id,
                attachment_ids=list(request_obj.attachments),
            )

    # Mark the conversation active synchronously, before the run starts.
    # This ensures the conversation becomes visible in list_all even if the
    # stream errors before the post-stream persistence runs, and bumps
    # updated_at on every send so recency ordering tracks activity.
    await _update_conversation_timestamp(
        conversation_id,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )

    run_manager = raw_request.app.state.run_manager
    run_ctx = RunContext(
        user_id=ctx.user.id,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
    )

    try:
        run_id = await run_manager.start_run(
            conversation_id=conversation_id,
            content=request_obj.content,
            attachments=list(request_obj.attachments),
            ctx=run_ctx,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    accept = raw_request.headers.get("accept", "")
    if "text/event-stream" in accept:
        return _build_run_streaming_response(
            raw_request=raw_request,
            conversation_id=conversation_id,
            run_id=run_id,
            redis_handle=rds,
        )

    return SendMessageResponse(run_id=run_id)


async def _get_history_messages(raw_request: Request, conversation_id: str) -> dict[str, object]:
    """Read cubepi-runtime conversation history.

    Messages are returned in cubepi's native shape (UserMessage / AssistantMessage /
    ToolResultMessage as pydantic dumps). The frontend consumes this shape directly;
    no cubebox-specific wire conversion layer.
    """
    from cubebox.agents.checkpointer import init_checkpointer

    del raw_request  # checkpointer factory override hook (unused; preserved for future use)
    async with init_checkpointer() as cp:
        data = await cp.load(conversation_id)
    if data is None:
        return {"messages": [], "total": 0}
    messages = [m.model_dump(mode="json") for m in data.messages]
    return {"messages": messages, "total": len(messages)}


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    """List messages in a conversation, read from cubepi PostgresCheckpointer state."""
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

    return await _get_history_messages(raw_request, conversation_id)


@router.get("/{conversation_id}/bootstrap")
async def get_conversation_bootstrap(
    conversation_id: str,
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    rds: Annotated[RedisHandle, Depends(redis_dep)],
) -> dict[str, object]:
    """Return history baseline plus active run metadata."""
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

    from cubebox.config import config as _cfg

    history = await _get_history_messages(raw_request, conversation_id)
    active_run = await get_active_run(
        rds.client, prefix=rds.key_prefix, conversation_id=conversation_id
    )

    last_run_status: str | None = None
    if active_run is not None:
        threshold = int(_cfg.get("lifecycle.stale_run_threshold_seconds", 120))
        if is_stale_meta(active_run, threshold_seconds=threshold):
            await mark_run_stale(
                rds.client,
                prefix=rds.key_prefix,
                run_id=active_run.run_id,
                conversation_id=conversation_id,
            )
            active_run = None
            last_run_status = "stale"

    active_run_payload: dict[str, Any] | None = None
    if active_run is not None:
        active_run_payload = {
            "run_id": active_run.run_id,
            "status": active_run.status,
            "user_message": active_run.user_message,
            "last_event_id": active_run.last_event_id,
            # Disambiguates the active run's user message from a prior turn
            # with identical content: any history user message older than
            # this timestamp belongs to a completed turn, not this run.
            "started_at": active_run.started_at,
        }

    # --- Token usage for the usage panel ---
    msgs: list[dict[str, Any]] = history["messages"]  # type: ignore[assignment]
    last_user_ts: str | None = None
    for msg in reversed(msgs):
        if isinstance(msg, dict) and msg.get("role") == "user":
            ts = msg.get("timestamp")
            if isinstance(ts, (int, float)):
                last_user_ts = datetime.fromtimestamp(ts, tz=UTC).isoformat()
            break

    from cubebox.services.usage import build_usage_summary

    usage_summary = await build_usage_summary(
        session,
        conversation_id,
        org_id=ctx.org_id,
        last_user_message_ts=last_user_ts,
    )

    return {
        "messages": history["messages"],
        "total": history["total"],
        "active_run": active_run_payload,
        "last_run_status": last_run_status,
        "usage_summary": usage_summary,
    }


def _format_sse_event(event_id: str, payload: dict[str, Any]) -> str:
    event_payload = {**payload, "event_id": event_id}
    return f"id: {event_id}\ndata: {json.dumps(event_payload, ensure_ascii=False)}\n\n"


@router.get("/{conversation_id}/runs/{run_id}/stream")
async def stream_run(
    conversation_id: str,
    run_id: str,
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    rds: Annotated[RedisHandle, Depends(redis_dep)],
) -> StreamingResponse:
    """Replay a run's event log, then continue with live blocking reads."""
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

    run_meta = await get_run_meta(rds.client, prefix=rds.key_prefix, run_id=run_id)
    if run_meta is None or run_meta.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found",
        )

    from cubebox.config import config as _cfg

    threshold = int(_cfg.get("lifecycle.stale_run_threshold_seconds", 120))
    if is_stale_meta(run_meta, threshold_seconds=threshold):
        await mark_run_stale(
            rds.client,
            prefix=rds.key_prefix,
            run_id=run_id,
            conversation_id=conversation_id,
        )

        async def _stale_stream() -> AsyncIterator[bytes]:
            from datetime import UTC, datetime

            payload = {
                "type": "error",
                "timestamp": datetime.now(UTC).isoformat(),
                "data": {
                    "error_code": "run_stale",
                    "message": "This run died before finishing.",
                },
            }
            yield _format_sse_event("0-0", payload).encode()

        return StreamingResponse(_stale_stream(), media_type="text/event-stream")

    return _build_run_streaming_response(
        raw_request=raw_request,
        conversation_id=conversation_id,
        run_id=run_id,
        redis_handle=rds,
    )


@router.post("/{conversation_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_active_run(
    conversation_id: str,
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    rds: Annotated[RedisHandle, Depends(redis_dep)],
) -> dict[str, object]:
    """Cancel the conversation's active run, if any."""
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

    active_run = await get_active_run(
        rds.client,
        prefix=rds.key_prefix,
        conversation_id=conversation_id,
    )
    if active_run is None or active_run.status != "running":
        return {"cancelled": False, "run_id": None}

    run_manager = raw_request.app.state.run_manager
    cancelled = await run_manager.cancel_run(active_run.run_id)
    return {"cancelled": cancelled, "run_id": active_run.run_id}
