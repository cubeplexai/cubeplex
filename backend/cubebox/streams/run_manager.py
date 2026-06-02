"""Background run orchestration decoupled from HTTP connections."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cubepi.providers.images import create_images_provider
from fastapi import FastAPI
from loguru import logger
from redis.asyncio import Redis
from uuid_utils import uuid7

from cubebox.agents.schemas import AgentEvent, DoneEvent, ErrorEvent, StatusEvent
from cubebox.streams.run_events import (
    append_run_event,
    clear_active_run,
    create_run,
    expire_run_data,
    get_active_run,
    get_run_meta,
    update_run_meta,
)
from cubebox.utils.time import utc_isoformat


@dataclass(slots=True)
class RunContext:
    """Scoped context required to execute a run."""

    user_id: str
    org_id: str
    workspace_id: str
    trigger: str = "interactive"


def _ns_to_agent_id(ns: tuple[Any, ...]) -> str | None:
    if not ns:
        return None
    return ":".join(str(part) for part in ns)


def _subagent_shared_tools(tools: list[Any]) -> list[Any]:
    """Tools shared into subagents. show_widget is top-level only (v1).

    Annotated ``list[Any]`` to match this module's convention (it builds tools
    via lazy imports inside functions and does not import ``AgentTool`` at module
    level). The helper only reads ``t.name``.
    """
    return [t for t in tools if t.name != "show_widget"]


def _backfill_tool_call_delta_identity(
    evt_dict: dict[str, Any],
    delta_context: dict[tuple[str | None, int], dict[str, Any]],
) -> dict[str, Any]:
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
    from cubebox.agents.schemas import (
        ArtifactEvent,
        CitationEvent,
        ReasoningEvent,
        TextDeltaEvent,
        ToolCallDeltaEvent,
        ToolCallEvent,
        ToolResultEvent,
        UsageEvent,
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
        elif evt_type == "citation":
            events.append(
                CitationEvent(
                    timestamp=evt_dict["timestamp"],
                    data=evt_dict["data"],
                    agent_id=evt_dict.get("agent_id"),
                    agent_name=evt_dict.get("agent_name"),
                )
            )
        elif evt_type == "usage":
            events.append(
                UsageEvent(
                    timestamp=evt_dict["timestamp"],
                    data=evt_dict["data"],
                    agent_id=evt_dict.get("agent_id"),
                    agent_name=evt_dict.get("agent_name"),
                )
            )
    return events


async def _drain_cubepi_sse_queue(
    queue: asyncio.Queue[dict[str, Any] | None],
    publish: Any,
) -> None:
    """Drain SSE dicts from a queue and forward them as typed AgentEvents.

    Each event is published with a fresh ``datetime.now(UTC)`` timestamp so the
    SSE consumer sees the time the event was actually streamed, not a fixed
    value computed once at run start.  Exits when it pops a sentinel ``None``.
    """
    while True:
        d = await queue.get()
        if d is None:
            return
        sse_event = cubepi_dict_to_agent_event(d, datetime.now(UTC).isoformat())
        if sse_event is None:
            continue
        await publish(sse_event, None)


async def _drain_subagent_citation_queue(
    queue: asyncio.Queue[tuple[str, Any, Any] | None],
    publish: Any,
) -> None:
    """Drain (kind, agent_id, payload) tuples and publish typed AgentEvents.

    Counterpart to :func:`_drain_cubepi_sse_queue` for the shared queue that
    ``SubAgentMiddleware`` and ``CitationMiddleware`` push onto:

    - ``("subagent", agent_id, sse_dict)`` — already-translated cubepi SSE
      dict produced by ``convert_agent_event_to_sse``. We retranslate via
      :func:`cubepi_dict_to_agent_event` and stamp the originating subagent
      ``agent_id`` so the frontend's per-agent stream buckets work.
    - ``("citation", agent_id, citation_payload)`` — wrapped into a
      :class:`CitationEvent`.

    Unknown kinds and unmappable subagent dicts are silently dropped.
    Exits on the ``None`` sentinel.

    Background: when the langgraph dispatch branch was removed in M6.6,
    the only consumer of this queue went with it; subagent live events
    accumulated and were discarded at run end. This drainer restores live
    delivery so the frontend doesn't have to wait for a page reload to
    see what a subagent did.
    """
    while True:
        item = await queue.get()
        if item is None:
            return
        try:
            kind, agent_id, payload = item
        except (TypeError, ValueError):
            logger.warning("subagent/citation queue produced malformed item: {!r}", item)
            continue

        timestamp = datetime.now(UTC).isoformat()
        sse_event: AgentEvent | None = None
        if kind == "subagent":
            sse_event = cubepi_dict_to_agent_event(payload, timestamp)
            if sse_event is not None and agent_id is not None:
                sse_event.agent_id = agent_id
        elif kind == "citation":
            from cubebox.agents.schemas import CitationEvent

            sse_event = CitationEvent(timestamp=timestamp, data=payload, agent_id=agent_id)
        else:
            continue

        if sse_event is None:
            continue
        await publish(sse_event, agent_id)


def cubepi_dict_to_agent_event(d: dict[str, Any], timestamp: str) -> AgentEvent | None:
    """Translate a single SSE dict produced by ``convert_agent_event_to_sse``
    into a typed cubebox ``AgentEvent``.

    Returns ``None`` for dicts that should be silently dropped at this layer
    (done — the caller emits done with usage data; unknown types).
    """
    from cubebox.agents.schemas import (
        ArtifactEvent,
        AskUserRequestEvent,
        AskUserResolvedEvent,
        ErrorEvent,
        InjectedMessageEvent,
        ReasoningEvent,
        SandboxConfirmRequestEvent,
        SandboxConfirmResolvedEvent,
        TextDeltaEvent,
        ToolCallDeltaEvent,
        ToolCallEvent,
        ToolResultEvent,
        UsageEvent,
    )

    t = d.get("type")
    if t == "injected_message":
        return InjectedMessageEvent(
            timestamp=timestamp,
            data={"content": d.get("content", ""), "steer_id": d.get("steer_id", "")},
        )
    if t == "text_delta":
        return TextDeltaEvent(
            timestamp=timestamp,
            data={"content": d.get("delta", ""), "usage": {}},
        )
    if t == "reasoning":
        return ReasoningEvent(
            timestamp=timestamp,
            data={"content": d.get("delta", "")},
        )
    if t == "tool_call_delta":
        return ToolCallDeltaEvent(
            timestamp=timestamp,
            data={
                "tool_call_id": d.get("id"),
                "name": d.get("name"),
                "args_delta": d.get("delta", ""),
                "index": d.get("index"),
            },
        )
    if t == "tool_call":
        return ToolCallEvent(
            timestamp=timestamp,
            data={
                "tool_call_id": d.get("id", ""),
                "name": d.get("name", ""),
                "arguments": d.get("arguments", ""),
            },
        )
    if t == "tool_result":
        # ``convert_agent_event_to_sse`` extracts a string from cubepi's
        # ``AgentToolResult`` before the dict reaches this translator; the
        # ``str()`` here is defensive against unexpected producers and is
        # a no-op for the expected string case. ``details`` carries
        # middleware-attached payloads such as ``subagent_events`` so the
        # live SSE shape matches the post-reload one.
        return ToolResultEvent(
            timestamp=timestamp,
            data={
                "tool_call_id": d.get("tool_call_id", ""),
                "name": d.get("name", ""),
                "content": str(d.get("result", "")),
                "is_error": d.get("is_error", False),
                "details": d.get("details"),
            },
        )
    if t == "artifact":
        return ArtifactEvent(
            timestamp=timestamp,
            data={
                "action": d.get("action", "created"),
                "artifact": d.get("artifact", {}),
            },
        )
    if t == "sandbox_confirm_request":
        args = d.get("args") or {}
        details = d.get("details") or {}
        return SandboxConfirmRequestEvent(
            timestamp=timestamp,
            data={
                "question_id": d.get("question_id", ""),
                "tool_call_id": d.get("tool_call_id", ""),
                "command": args.get("command", ""),
                "matched_pattern": details.get("matched_pattern"),
                "timeout_seconds": d.get("timeout_seconds"),
            },
        )
    if t == "sandbox_confirm_resolved":
        return SandboxConfirmResolvedEvent(
            timestamp=timestamp,
            data={
                "question_id": d.get("question_id", ""),
                "decision": d.get("decision"),
                "cancelled": d.get("cancelled", False),
                "timed_out": d.get("timed_out", False),
                "reason": d.get("reason"),
            },
        )
    if t == "ask_user_request":
        return AskUserRequestEvent(
            timestamp=timestamp,
            data={
                "question_id": d.get("question_id", ""),
                "questions": d.get("questions", []),
                "timeout_seconds": d.get("timeout_seconds"),
            },
        )
    if t == "ask_user_resolved":
        return AskUserResolvedEvent(
            timestamp=timestamp,
            data={
                "question_id": d.get("question_id", ""),
                "answers": d.get("answers"),
                "cancelled": d.get("cancelled", False),
                "timed_out": d.get("timed_out", False),
            },
        )
    if t == "usage":
        return UsageEvent(
            timestamp=timestamp,
            data={
                "input_tokens": d.get("input_tokens", 0),
                "output_tokens": d.get("output_tokens", 0),
                "cache_read_tokens": d.get("cache_read_tokens", 0),
                "cache_write_tokens": d.get("cache_write_tokens", 0),
            },
        )
    if t == "error":
        err_msg = d.get("error") or "unknown agent error"
        return ErrorEvent(
            timestamp=timestamp,
            data={
                "error_code": "run_error",
                "message": err_msg,
                "details": err_msg,
            },
        )
    return None


async def _build_attachment_content_blocks(
    *,
    org_id: str,
    workspace_id: str,
    conversation_id: str,
    attachment_ids: list[str],
) -> list[dict[str, Any]]:
    """Return file_attachment content blocks for the given file_ids.

    Reads metadata via a short-lived session. Rows are expected to exist
    (validated at the API layer); missing rows are silently skipped here
    since hydration would have already failed for them.
    """
    if not attachment_ids:
        return []

    from cubebox.db.engine import async_session_maker
    from cubebox.repositories import AttachmentRepository

    async with async_session_maker() as session:
        repo = AttachmentRepository(
            session,
            org_id=org_id,
            workspace_id=workspace_id,
        )
        blocks: list[dict[str, Any]] = []
        for fid in attachment_ids:
            row = await repo.get_in_conversation(
                conversation_id=conversation_id,
                attachment_id=fid,
            )
            if row is None:
                continue
            blocks.append(
                {
                    "type": "file_attachment",
                    "file_id": row.id,
                    "kind": row.kind,
                    "filename": row.filename,
                    "sandbox_path": row.sandbox_path,
                    "size_bytes": row.size_bytes,
                    "width": row.width,
                    "height": row.height,
                }
            )
        return blocks


async def _repair_dangling_tool_calls(conversation_id: str) -> None:
    """Backfill synthetic tool_results for tool_calls a cancel left unanswered.

    Mirrors cubepi's own cancel cleanup as a fallback. Loads the checkpointed
    thread, finds tool_calls in the last assistant message that have no
    ToolResultMessage, and appends a synthetic error result for each so the
    next provider call sees a structurally valid history.
    """
    from cubepi.providers.base import (
        AssistantMessage,
        TextContent,
        ToolCall,
        ToolResultMessage,
    )

    from cubebox.agents.checkpointer import init_checkpointer

    async with init_checkpointer() as cp:
        data = await cp.load(conversation_id)
        if data is None or not data.messages:
            return

        last_idx = -1
        for i in range(len(data.messages) - 1, -1, -1):
            if isinstance(data.messages[i], AssistantMessage):
                last_idx = i
                break
        if last_idx == -1:
            return
        last_assistant = data.messages[last_idx]
        assert isinstance(last_assistant, AssistantMessage)

        # Only this turn's results count as answered — tool_call ids are not
        # globally unique, so scanning all history could treat a reused id
        # from an earlier turn as answered and skip the needed backfill.
        answered = {
            m.tool_call_id
            for m in data.messages[last_idx + 1 :]
            if isinstance(m, ToolResultMessage)
        }
        synthetic: list[Any] = [
            ToolResultMessage(
                tool_call_id=block.id,
                tool_name=block.name,
                content=[TextContent(text="[Tool execution cancelled by user]")],
                is_error=True,
                timestamp=datetime.now(UTC).timestamp(),
            )
            for block in last_assistant.content
            if isinstance(block, ToolCall) and block.id not in answered
        ]
        if synthetic:
            await cp.append(conversation_id, synthetic)


async def _emit_synthetic_resolved(
    publish_stream_event: Any,
    pending: Any,
    answered_question_id: str,
) -> None:
    """Emit a typed *_resolved event for a pending that was cleared by
    the dangling-cleanup branch (org policy changed between pause and
    respond, so middleware short-circuited the resumed tool call).

    Uses the SAME typed events + publish_stream_event(event, agent_key)
    signature the live HITL resolve path uses — so the frontend sees an
    identical event shape and the same applyStreamEvent branch fires.

    See spec §6 "Dangling pending cleanup".
    """
    from cubebox.agents.schemas import (
        AskUserResolvedEvent,
        SandboxConfirmResolvedEvent,
    )

    event: AgentEvent
    timestamp = datetime.now(UTC).isoformat()
    kind = pending.payload.kind  # "approve" | "ask" | "confirm"
    if kind == "approve":
        event = SandboxConfirmResolvedEvent(
            timestamp=timestamp,
            data={
                "question_id": answered_question_id,
                "tool_call_id": pending.payload.tool_call_id,
                "decision": "policy_overridden",
                "cancelled": False,
                "timed_out": False,
                "reason": "org sandbox policy changed during pause",
            },
        )
    elif kind == "ask":
        # AskUserResolvedEvent.data is {question_id, answers, cancelled,
        # timed_out} — no 'outcome' field. Encode policy-override as
        # cancelled=True + reason='policy_overridden' so the existing
        # frontend applyStreamEvent ask_user_resolved branch fires and
        # the card is removed.
        event = AskUserResolvedEvent(
            timestamp=timestamp,
            data={
                "question_id": answered_question_id,
                "answers": None,
                "cancelled": True,
                "timed_out": False,
                "reason": "policy_overridden",
            },
        )
    else:
        # ConfirmRequest (kind='confirm') is unused by cubebox today. If a
        # future caller introduces it, fail loud rather than silently leave
        # the frontend with a stuck card.
        raise ValueError(f"unhandled HITL kind in dangling cleanup: {kind!r}")

    await publish_stream_event(event, None)  # second arg = agent_key


class _AutoDetachListener:
    """Schedules ``agent.detach()`` exactly once on ``HitlRequestEvent``.

    Exposes ``.detached`` so the terminal block in ``_run_cubepi_path`` can
    read whether this turn entered HITL — distinguishing a real new pending
    request from a stale pending leftover from a prior session.
    """

    def __init__(self, agent: Any) -> None:
        self._agent = agent
        self.detached: bool = False

    def __call__(self, evt: Any, _signal: Any = None) -> None:
        from cubepi.agent.types import HitlRequestEvent

        if self.detached:
            return
        if isinstance(evt, HitlRequestEvent):
            self.detached = True
            asyncio.create_task(self._agent.detach())


def _build_auto_detach_listener(agent: Any) -> _AutoDetachListener:
    return _AutoDetachListener(agent)


class ResumeNoPending(LookupError):
    """No DB pending exists for this conversation."""


class ResumeStaleAnswer(Exception):
    """The submitted answer's question_id doesn't match the pending."""


class ResumeInFlight(Exception):
    """Another resume / cancel is already in flight for this run."""


class ResumeConflict(Exception):
    """The conversation has moved on; the active run_id has changed."""


class RunManager:
    """Owns background run execution and Redis persistence."""

    def __init__(
        self,
        *,
        app: FastAPI,
        redis: Redis,
        key_prefix: str,
        run_event_ttl_seconds: int,
        run_stream_max_events: int = 1000000,
    ) -> None:
        self._app = app
        self._redis = redis
        self._key_prefix = key_prefix
        self._run_event_ttl_seconds = run_event_ttl_seconds
        self._run_stream_max_events = run_stream_max_events
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._agents: dict[str, Any] = {}
        self._hitl_channels: dict[str, Any] = {}
        self._consolidation_tasks: set[asyncio.Task[None]] = set()
        self._reflection_tasks: set[asyncio.Task[None]] = set()
        self._ack_waiters: dict[str, list[asyncio.Future[bool]]] = {}
        self._control_channel = f"{key_prefix}:control"
        self._ack_channel = f"{key_prefix}:control:ack"
        self._control_stopping = False
        self._control_tasks: list[asyncio.Task[None]] = []
        self._tasks_empty: asyncio.Event = asyncio.Event()
        self._tasks_empty.set()

    def _on_task_done(self, run_id: str) -> None:
        """Done-callback that removes the run task and signals drain when empty."""
        self._tasks.pop(run_id, None)
        if not self._tasks:
            self._tasks_empty.set()

    async def start_run(
        self,
        *,
        conversation_id: str,
        content: str,
        attachments: list[str] | None = None,
        ctx: RunContext,
        run_id: str | None = None,
    ) -> str:
        """Create and start a new background run.

        ``run_id`` is generated unless the caller supplies one. The scheduled-
        task poller pre-generates it and stamps the occurrence row so the
        completion hook can find the row by ``run_id`` even if ``_execute_run``
        finishes faster than the poller's post-dispatch UPDATE.
        """
        if run_id is None:
            run_id = str(uuid7())
        started_at = utc_isoformat(datetime.now(UTC))

        # DB is the authoritative source for "is this conversation paused
        # on a pending HITL". Check BEFORE create_run so a TTL-expired
        # Redis lock can't let a new turn slip past — the Redis active-run
        # key normally blocks via paused_hitl status, but if a long pause
        # exceeds run_event_ttl_seconds the key disappears and create_run
        # below would otherwise succeed for a brand-new run_id, racing
        # the in-flight resume path and orphaning the pending answer.
        from cubebox.agents.checkpointer import init_checkpointer

        async with init_checkpointer() as _cp:
            _db_pending = await _cp.load_pending_request(conversation_id)
        if _db_pending is not None:
            raise RuntimeError(
                f"Conversation {conversation_id} has a pending HITL request "
                f"(question_id={_db_pending.question_id}); "
                f"answer or cancel before starting a new turn"
            )

        created_run = await create_run(
            self._redis,
            prefix=self._key_prefix,
            run_id=run_id,
            conversation_id=conversation_id,
            status="running",
            started_at=started_at,
            user_message=content,
            ttl_seconds=self._run_event_ttl_seconds,
        )
        if created_run is None:
            existing = await get_active_run(
                self._redis,
                prefix=self._key_prefix,
                conversation_id=conversation_id,
            )
            if existing and existing.status in ("running", "paused_hitl"):
                raise RuntimeError(f"Conversation {conversation_id} already has an active run")
            raise RuntimeError(f"Conversation {conversation_id} could not claim an active run")

        task = asyncio.create_task(
            self._execute_run(
                run_id=run_id,
                conversation_id=conversation_id,
                content=content,
                attachments=list(attachments or []),
                ctx=ctx,
            ),
            name=f"run:{run_id}",
        )
        self._tasks_empty.clear()
        self._tasks[run_id] = task
        task.add_done_callback(lambda _: self._on_task_done(run_id))
        return run_id

    async def cancel_all(self) -> None:
        """Cancel every in-flight run task. Forced shutdown path."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task

    async def cancel_run(self, run_id: str) -> bool:
        """Cancel a single in-flight run and wait for cleanup to finish.

        Awaiting the task keeps the caller blocked until ``_execute_run``'s
        finally block has cleared the Redis active-run key. Without this, a
        client that clicks Stop and immediately re-sends would race against
        cleanup and get a 409 from ``start_run``.
        """
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        return True

    async def steer_run(self, run_id: str, content: str) -> bool:
        """Inject a steering message into a live run's agent.

        Returns False when the run has no live agent in this process (already
        finished, or running in a different worker — the same single-process
        limitation as cancel_run). The agent's loop drains the message at its
        next safe point; we do not block on delivery.
        """
        agent = self._agents.get(run_id)
        if agent is None:
            return False

        from cubepi.providers.base import TextContent, UserMessage

        agent.steer(UserMessage(content=[TextContent(text=content)]))
        return True

    async def _publish_control(
        self,
        run_id: str,
        type_: str,
        content: str | None = None,
        steer_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        import json

        payload: dict[str, Any] = {"run_id": run_id, "type": type_}
        if content is not None:
            payload["content"] = content
        if steer_id is not None:
            payload["steer_id"] = steer_id
        if extra:
            payload.update(extra)
        await self._redis.publish(self._control_channel, json.dumps(payload))

    async def _publish_ack(self, run_id: str) -> None:
        import json

        await self._redis.publish(self._ack_channel, json.dumps({"run_id": run_id}))

    async def dispatch_steer(self, run_id: str, content: str, steer_id: str) -> str:
        agent = self._agents.get(run_id)
        if agent is not None:
            from cubepi.providers.base import TextContent, UserMessage

            agent.steer(
                UserMessage(
                    content=[TextContent(text=content)],
                    metadata={"steer_id": steer_id},
                )
            )
            return "steered"
        await self._publish_control(run_id, "steer", content, steer_id=steer_id)
        return "published"

    async def resume_run_with_answer(
        self,
        *,
        conversation_id: str,
        run_id: str,
        question_id: str,
        answer: Any,
        ctx: RunContext,
    ) -> str:
        """Resume a paused HITL conversation. Reuses the original run_id;
        events stream into the same Redis stream key. See spec §5.

        Returns the run_id (echoed back so the route response carries it).
        """
        from cubebox.agents.checkpointer import init_checkpointer
        from cubebox.streams.hitl_resume import ClaimResumeOutcome, claim_resume

        # 1. Authoritative: DB pending. load_pending_request shape unchanged
        #    per cubepi v3 prereq — returns HitlRequest | None.
        async with init_checkpointer() as cp:
            pending = await cp.load_pending_request(conversation_id)
        if pending is None:
            raise ResumeNoPending(f"no pending for {conversation_id}")
        if pending.question_id != question_id:
            raise ResumeStaleAnswer(f"answer for {question_id}; pending is {pending.question_id}")
        started_at_iso = datetime.fromtimestamp(pending.created_at, UTC).isoformat()

        # 2. Single-flight claim — pass started_at so the long-pause rebuild
        #    branch in claim_resume's Lua can repopulate the meta hash.
        claim = await claim_resume(
            self._redis,
            prefix=self._key_prefix,
            conversation_id=conversation_id,
            expected_run_id=run_id,
            started_at=started_at_iso,
            ttl_seconds=self._run_event_ttl_seconds,
        )
        if claim.outcome == ClaimResumeOutcome.ALREADY_RUNNING:
            raise ResumeInFlight("another resume/cancel is in flight")
        if claim.outcome == ClaimResumeOutcome.CONFLICT:
            raise ResumeConflict("conversation has moved on")
        assert claim.claim_token is not None  # OK outcome guarantees a token

        # 3. Spawn the respond task. Reuse the original run_id.
        task = asyncio.create_task(
            self._execute_respond_run(
                run_id=run_id,
                conversation_id=conversation_id,
                question_id=question_id,
                answer=answer,
                claim_token=claim.claim_token,
                ctx=ctx,
            ),
            name=f"respond:{run_id}",
        )
        self._tasks_empty.clear()
        self._tasks[run_id] = task
        task.add_done_callback(lambda _: self._on_task_done(run_id))
        return run_id

    async def cancel_paused_run(
        self,
        *,
        conversation_id: str,
        run_id: str,
        reason: str = "cancelled by user",
        ctx: RunContext,
    ) -> str:
        """Cancel a conversation parked in ``paused_hitl``. See spec §4.

        A normal :meth:`cancel_run` ``task.cancel()`` is a no-op here because
        the worker that started the run released its ``asyncio.Task`` when
        ``auto_detach`` fired. We instead build a transient agent via the T7
        factory, wire it to a real SSE pipeline, and call
        ``agent.abort_pending(reason)`` so cubepi emits an
        ``AgentAbortedEvent`` that the frontend picks up. Without the SSE
        wiring the event has zero subscribers and the frontend's pending
        card never resolves.
        """
        from cubebox.agents.checkpointer import init_checkpointer
        from cubebox.agents.stream import convert_agent_event_to_sse
        from cubebox.streams.hitl_resume import (
            ClaimResumeOutcome,
            claim_resume,
            finalize_run_meta_if_claim_matches,
        )

        # 1. DB pending recovers started_at — needed for claim_resume's
        #    rebuild branch (long-pause case where Redis meta has aged out).
        async with init_checkpointer() as cp:
            pending = await cp.load_pending_request(conversation_id)
        if pending is None:
            raise ResumeNoPending(f"no pending for {conversation_id}")
        started_at_iso = datetime.fromtimestamp(pending.created_at, UTC).isoformat()

        # 2. Single-flight CAS — only one cancel/resume may own the slot.
        claim = await claim_resume(
            self._redis,
            prefix=self._key_prefix,
            conversation_id=conversation_id,
            expected_run_id=run_id,
            started_at=started_at_iso,
            ttl_seconds=self._run_event_ttl_seconds,
        )
        if claim.outcome == ClaimResumeOutcome.ALREADY_RUNNING:
            raise ResumeInFlight("cancel raced another resume/cancel in flight")
        if claim.outcome == ClaimResumeOutcome.CONFLICT:
            raise ResumeConflict("conversation has moved on")
        assert claim.claim_token is not None  # OK outcome guarantees a token

        # 3. Minimal SSE pipeline. abort_pending only emits AgentAbortedEvent
        #    so the citation/turn_usage gymnastics in the prompt path's
        #    publish_stream_event are dead weight here — a plain
        #    _append_event publisher is sufficient.
        sse_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def publish_stream_event(sse_event: AgentEvent, agent_key: str | None) -> None:
            await self._append_event(run_id, conversation_id, sse_event)

        drainer = asyncio.create_task(
            _drain_cubepi_sse_queue(sse_queue, publish_stream_event),
            name=f"cancel_drain:{run_id}",
        )

        try:
            async with init_checkpointer() as cp:
                agent, _all_tools, _ch = await self._build_agent_for_conversation(
                    ctx=ctx,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    cp=cp,
                    sandbox=None,
                    skill_catalog=None,
                    catalog_session=None,
                    effective_system_prompt="",
                    extra_ref_holder={"extra": None},
                    sse_queue=sse_queue,
                    publish_stream_event=publish_stream_event,
                )

                def _on_event(evt: Any, _signal: Any = None) -> None:
                    for d in convert_agent_event_to_sse(evt):
                        sse_queue.put_nowait(d)

                agent.subscribe(_on_event)

                from cubepi.tracing import trace, tracing_context

                tracer = getattr(self._app.state, "tracer", None)
                _trace_meta = {
                    k: str(v)
                    for k, v in (
                        ("run_id", run_id),
                        ("conversation_id", conversation_id),
                        ("user_id", ctx.user_id),
                        ("org_id", ctx.org_id),
                        ("workspace_id", ctx.workspace_id),
                        ("turn_kind", "abort"),
                    )
                    if v is not None
                }
                with tracing_context(metadata=_trace_meta):
                    async with trace(tracer, agent):
                        await agent.abort_pending(reason)
        finally:
            await sse_queue.put(None)
            await drainer

        # CAS-guarded terminal write — a racing flow that took over the
        # slot wins the row; our finalize is a no-op.
        wrote_terminal = await finalize_run_meta_if_claim_matches(
            self._redis,
            prefix=self._key_prefix,
            run_id=run_id,
            claim_token=claim.claim_token,
            status="cancelled",
        )
        # Release the active-run lock + age out the meta TTL when WE own
        # the row. Without this, bootstrap on a refresh still sees an
        # active_run row (status=cancelled) and the frontend enters
        # streaming mode tailing a terminal stream — heartbeats until
        # Redis TTL clears the row. Matches _execute_run's cleanup on
        # the completed / errored paths. Skip when our claim token lost
        # the CAS (some other flow owns the slot now).
        if wrote_terminal:
            await clear_active_run(
                self._redis,
                prefix=self._key_prefix,
                conversation_id=conversation_id,
                run_id=run_id,
            )
            await expire_run_data(
                self._redis,
                prefix=self._key_prefix,
                run_id=run_id,
                ttl_seconds=self._run_event_ttl_seconds,
            )
        return run_id

    async def dispatch_cancel_steer(self, run_id: str, steer_id: str) -> str:
        agent = self._agents.get(run_id)
        if agent is not None:
            removed = agent.cancel_steer(steer_id)
            return "cancelled" if removed else "not_found"
        await self._publish_control(run_id, "cancel_steer", steer_id=steer_id)
        return "published"

    async def dispatch_cancel(self, run_id: str, ack_timeout: float = 3.0) -> str:
        if run_id in self._tasks:
            await self.cancel_run(run_id)
            return "cancelled"

        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._ack_waiters.setdefault(run_id, []).append(fut)
        try:
            await self._publish_control(run_id, "cancel")
            await asyncio.wait_for(fut, timeout=ack_timeout)
            return "cancelled"
        except TimeoutError:
            return "published"
        finally:
            waiters = self._ack_waiters.get(run_id)
            if waiters and fut in waiters:
                waiters.remove(fut)
                if not waiters:
                    self._ack_waiters.pop(run_id, None)

    async def _handle_control(self, data: dict[str, Any]) -> None:
        run_id = data.get("run_id")
        type_ = data.get("type")
        if not isinstance(run_id, str):
            return
        if type_ == "cancel":
            if run_id in self._tasks:
                await self.cancel_run(run_id)
                await self._publish_ack(run_id)
        elif type_ == "steer":
            agent = self._agents.get(run_id)
            if agent is not None:
                from cubepi.providers.base import TextContent, UserMessage

                agent.steer(
                    UserMessage(
                        content=[TextContent(text=data.get("content") or "")],
                        metadata={"steer_id": data.get("steer_id") or ""},
                    )
                )
        elif type_ == "cancel_steer":
            agent = self._agents.get(run_id)
            if agent is not None:
                agent.cancel_steer(data.get("steer_id") or "")

    async def _handle_ack(self, data: dict[str, Any]) -> None:
        run_id = data.get("run_id")
        if not isinstance(run_id, str):
            return
        for fut in self._ack_waiters.get(run_id, []):
            if not fut.done():
                fut.set_result(True)

    async def _subscribe_loop(self, channel: str, handler: Any, ready: asyncio.Event) -> None:
        import json

        backoff = 0.5
        while not self._control_stopping:
            pubsub = self._redis.pubsub()
            try:
                await pubsub.subscribe(channel)
                ready.set()
                backoff = 0.5
                async for msg in pubsub.listen():
                    if self._control_stopping:
                        break
                    if msg.get("type") != "message":
                        continue
                    try:
                        await handler(json.loads(msg["data"]))
                    except Exception:
                        logger.warning("control handler error on {}", channel, exc_info=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("control listener {} dropped; reconnecting", channel, exc_info=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 5.0)
            finally:
                with suppress(Exception):
                    await pubsub.aclose()  # type: ignore[no-untyped-call]

    async def start_control_listeners(self, ready_timeout: float = 5.0) -> None:
        self._control_stopping = False
        ctrl_ready = asyncio.Event()
        ack_ready = asyncio.Event()
        self._control_tasks = [
            asyncio.create_task(
                self._subscribe_loop(self._control_channel, self._handle_control, ctrl_ready),
                name="run-control-listener",
            ),
            asyncio.create_task(
                self._subscribe_loop(self._ack_channel, self._handle_ack, ack_ready),
                name="run-control-ack-listener",
            ),
        ]
        try:
            await asyncio.wait_for(
                asyncio.gather(ctrl_ready.wait(), ack_ready.wait()), timeout=ready_timeout
            )
        except TimeoutError:
            # Don't fail startup: single-instance deployments don't need pub/sub
            # at all (control runs via the local fast-path), and a transient Redis
            # blip shouldn't block boot. But don't fail *silently* either — the
            # listeners keep retrying in the background (_subscribe_loop), so a
            # persistent failure here (e.g. Redis ACL/connectivity) means
            # cross-instance cancel/steer is degraded until they connect.
            logger.warning(
                "Run-control pub/sub listeners not subscribed within {}s; "
                "cross-instance cancel/steer degraded until they connect "
                "(listeners keep retrying in the background)",
                ready_timeout,
            )

    async def stop_control_listeners(self) -> None:
        self._control_stopping = True
        for t in self._control_tasks:
            t.cancel()
        for t in self._control_tasks:
            with suppress(asyncio.CancelledError):
                await t
        self._control_tasks = []

    async def drain(self, timeout_seconds: float) -> None:
        """Wait for in-flight runs to finish, then return.

        On timeout, cancels residual tasks via ``cancel_all`` (which lets
        the per-task cancel path mark status=cancelled and write an
        ``error`` event before the lock is released).

        Logs a status line on entry when there's anything to wait for, plus
        a progress line every 30 seconds while waiting.
        """
        # Best-effort: stop background consolidation and reflection tasks first,
        # regardless of whether any run tasks remain (drain returns early below
        # when _tasks is empty).
        for t in list(self._consolidation_tasks) + list(self._reflection_tasks):
            t.cancel()
        for t in list(self._consolidation_tasks) + list(self._reflection_tasks):
            with suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(t, timeout=2.0)

        if self._tasks_empty.is_set():
            return

        logger.info(
            "Draining {} in-flight run(s) (timeout {}s)",
            len(self._tasks),
            timeout_seconds,
        )
        progress_task = asyncio.create_task(self._log_drain_progress())
        try:
            await asyncio.wait_for(self._tasks_empty.wait(), timeout=timeout_seconds)
        except TimeoutError:
            logger.warning(
                "Drain timeout after {}s, cancelling {} residual run(s)",
                timeout_seconds,
                len(self._tasks),
            )
            await self.cancel_all()
        finally:
            progress_task.cancel()
            with suppress(asyncio.CancelledError):
                await progress_task

    async def _log_drain_progress(self) -> None:
        try:
            while True:
                await asyncio.sleep(30)
                if self._tasks:
                    logger.info("Still draining: {} run(s) remaining", len(self._tasks))
        except asyncio.CancelledError:
            return

    async def _append_event(self, run_id: str, conversation_id: str, event: AgentEvent) -> str:
        payload = event.model_dump()
        return await append_run_event(
            self._redis,
            prefix=self._key_prefix,
            run_id=run_id,
            conversation_id=conversation_id,
            payload=payload,
            ttl_seconds=self._run_event_ttl_seconds,
            maxlen=self._run_stream_max_events,
        )

    async def _append_error(
        self,
        run_id: str,
        conversation_id: str,
        message: str,
        details: str | None = None,
    ) -> None:
        error_event = ErrorEvent(
            timestamp=datetime.now(UTC).isoformat(),
            data={
                "error_code": "run_error",
                "message": message,
                "details": details or message,
            },
        )
        await self._append_event(run_id, conversation_id, error_event)

    async def _run_cubepi_path(
        self,
        *,
        ctx: RunContext,
        run_id: str,
        conversation_id: str,
        content: str,
        attachments: list[str],
        effective_system_prompt: str,
        publish_stream_event: Any,
        flush_citation_buffer: Any,
        citation_buffers: dict[str | None, str],
        sandbox: Any | None = None,
        skill_catalog: Any | None = None,
        catalog_session: Any | None = None,
        trigger: str = "interactive",
    ) -> str:
        """Execute a single user turn through the cubepi runtime.

        Builds a cubepi.Provider + cubepi.Agent, subscribes an event listener, then
        awaits agent.prompt(). Each AgentEvent is translated into a cubebox AgentEvent
        schema object and forwarded to ``publish_stream_event``; the rest of
        _execute_run (DoneEvent, update_run_meta, etc.) consumes the resulting
        turn_usage and citation buffers.

        Tools wired (M2.5):
          - no-DI builtin tools (calculator, datetime)
          - view_images (per-request DI: org_id, workspace_id, objectstore, capabilities)
          - memory CRUD tools (service factory per-request)
          - load_skill (catalog + workspace/org)
          - MCP tools (workspace-enabled HTTP MCP servers)
        """
        from cubebox.agents.checkpointer import init_checkpointer
        from cubebox.agents.stream import convert_agent_event_to_sse
        from cubebox.middleware.citations.counter import citation_counter_var

        # extra_ref late-binding: compaction, skills, and todo all need access
        # to agent._extra, which is only available after the agent is built.
        # Pass the holder dict into the factory so middleware closures and the
        # caller share the same dict; the caller populates it post-build.
        extra_ref_holder: dict[str, Any] = {"extra": None}

        # Bridge the synchronous cubepi listener to the async world via a queue.
        # agent.prompt() is async and invokes synchronous listeners on each
        # AgentEvent as they arrive.  Previously we buffered translated dicts
        # and flushed them after prompt() returned, which made long responses
        # appear as a single batch dump.  Instead, push each translated dict
        # onto an asyncio.Queue and have a parallel drain task forward them
        # through publish_stream_event in real time.  The sentinel ``None``
        # signals the drainer to exit so we can finish citation flushing.
        sse_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async with init_checkpointer() as cp:
            # Seed the citation counter past any 【N-M】 markers already
            # persisted in this conversation's tool-result history so
            # cross-turn ids don't collide in the frontend store (which is
            # keyed by citation_id alone). No-op on the first turn.
            try:
                _hist = await cp.load(conversation_id)
            except Exception as _seed_exc:
                logger.warning("Citation seed: failed to load history: {}", _seed_exc)
                _hist = None
            if _hist is not None and _hist.messages:
                _counter = citation_counter_var.get()
                if _counter is not None:
                    await _counter.seed_from_messages(_hist.messages)

            # all_tools is part of the factory contract for future callers
            # (e.g. T8/T10) but the prompt path doesn't need it directly —
            # the agent already has the tools bound at construction time.
            agent, _all_tools, sandbox_hitl_channel = await self._build_agent_for_conversation(
                ctx=ctx,
                conversation_id=conversation_id,
                run_id=run_id,
                cp=cp,
                sandbox=sandbox,
                skill_catalog=skill_catalog,
                catalog_session=catalog_session,
                effective_system_prompt=effective_system_prompt,
                extra_ref_holder=extra_ref_holder,
                sse_queue=sse_queue,
                publish_stream_event=publish_stream_event,
                trigger=trigger,
            )
            # Late-bind extra_ref to the live agent._extra dict so compaction /
            # skills / todo middleware can read and write persistent state.
            # The factory already populated the closure via extra_ref_holder;
            # this is the post-build assignment those closures resolve to.
            extra_ref_holder["extra"] = agent._extra

            from cubepi.agent.types import MessageEndEvent as _MsgEndEvent
            from cubepi.providers.base import UserMessage as _UserMsg

            _user_msg_seen = 0
            auto_detach = _build_auto_detach_listener(agent)

            def _on_event(evt: Any, _signal: Any = None) -> None:
                # Runs on the same event loop as _run_cubepi_path, so
                # put_nowait is safe.  If we ever invoke the agent from a
                # background thread, swap to loop.call_soon_threadsafe.
                # auto_detach must run FIRST so HitlRequestEvent triggers
                # detach before the SSE conversion below; T6 reads
                # `auto_detach.detached` in the terminal block.
                auto_detach(evt, _signal)
                nonlocal _user_msg_seen
                if isinstance(evt, _MsgEndEvent) and isinstance(evt.message, _UserMsg):
                    _user_msg_seen += 1
                    if _user_msg_seen == 1:
                        return  # seed prompt — already shown optimistically
                for d in convert_agent_event_to_sse(evt):
                    sse_queue.put_nowait(d)

            agent.subscribe(_on_event)
            self._agents[run_id] = agent
            if sandbox_hitl_channel is not None:
                self._hitl_channels[run_id] = sandbox_hitl_channel
            drainer = asyncio.create_task(_drain_cubepi_sse_queue(sse_queue, publish_stream_event))

            # Compute relevance-memory snapshot before the agent loop starts
            # and bake it into the UserMessage metadata so MemoryMiddleware
            # can prepend the rendered snapshot text during transform_context.
            # Baking the snapshot at append-time (rather than re-deriving it
            # on replay) is what keeps the historical prefix byte-stable for
            # prompt caching — see backend/docs/prompt-cache-discipline.md.
            import time as _time

            from cubepi.providers.base import TextContent as _TextContent
            from cubepi.providers.base import UserMessage as _UserMessage

            from cubebox.middleware.memory import compute_relevance_snapshot as _compute_snap

            _user_msg_metadata: dict[str, Any] = {}
            try:
                _mem_repo_factory = extra_ref_holder["mem_repo_factory"]
                async with _mem_repo_factory() as _snap_repo:
                    _snapshot = await _compute_snap(_snap_repo)
                if _snapshot is not None:
                    _user_msg_metadata["memory_snapshot"] = _snapshot
            except Exception as _snap_exc:
                logger.warning("Failed to compute relevance snapshot: {}", _snap_exc)

            # Build attachment metadata blocks and inject into user message so
            # AttachmentHintMiddleware can render the [Attachments] hint.
            if attachments:
                try:
                    _att_blocks = await _build_attachment_content_blocks(
                        org_id=ctx.org_id,
                        workspace_id=ctx.workspace_id,
                        conversation_id=conversation_id,
                        attachment_ids=attachments,
                    )
                    if _att_blocks:
                        _user_msg_metadata["attachments"] = _att_blocks
                except Exception as _att_exc:
                    logger.warning("Failed to build attachment blocks for cubepi run: {}", _att_exc)

            _user_msg = _UserMessage(
                content=[_TextContent(text=content)],
                timestamp=_time.time(),
                metadata=_user_msg_metadata,
            )
            # Attach the process-level Tracer to this run via cubepi's
            # best-effort scope: it swallows every tracing fault (attach,
            # detach, flush) so tracing can never break the run, and is a
            # no-op when tracing is disabled (tracer is None).
            from cubepi.tracing import trace, tracing_context

            from cubebox.llm.runtime_writeback import (
                schedule_runtime_status_writeback as _schedule_writeback,
            )

            tracer = getattr(self._app.state, "tracer", None)
            # Provider/model identity were resolved inside the factory; the
            # writeback below uses them so the per-run liveness flips still
            # target the same provider+model the agent actually called.
            provider_name: str = extra_ref_holder["provider_name"]
            model_id: str = extra_ref_holder["model_id"]
            # Stamp the run's identity onto the trace spans (recorder writes
            # these as cubepi.metadata.* on the invoke_agent span). Skip None
            # and stringify so OTel attribute typing is always satisfied.
            _trace_meta = {
                k: str(v)
                for k, v in (
                    ("conversation_id", conversation_id),
                    ("user_id", ctx.user_id),
                    ("org_id", ctx.org_id),
                    ("workspace_id", ctx.workspace_id),
                )
                if v is not None
            }
            final_status: str = "completed"
            try:
                with tracing_context(metadata=_trace_meta):
                    async with trace(tracer, agent):
                        await agent.prompt(_user_msg)
            except BaseException as _run_exc:
                # Out-of-band, best-effort: a 401/403 flips provider liveness to
                # "fail"; a model_not_found flips this model to "unavailable".
                # Never blocks or alters the live request — we re-raise as-is.
                _schedule_writeback(
                    org_id=ctx.org_id,
                    provider_slug=provider_name,
                    model_id=model_id,
                    exc=_run_exc,
                )
                raise
            else:
                # Success clears a stale liveness "fail" via a guarded UPDATE.
                _schedule_writeback(
                    org_id=ctx.org_id,
                    provider_slug=provider_name,
                    model_id=model_id,
                    exc=None,
                )

                # --- Out-of-band reflection trigger ---
                # Spawn a detached task that runs a small reflection agent
                # to decide whether memory_save / memory_update calls are
                # warranted for this turn. Fire-and-forget — never blocks
                # or raises into the main run path. Provider / factory /
                # memory_service_factory are stashed by the agent factory
                # via extra_ref_holder (T7); reuse them rather than
                # re-resolving.
                def _last_assistant_text(messages: list[Any]) -> str | None:
                    from cubepi.providers.base import AssistantMessage, TextContent

                    for msg in reversed(messages):
                        if isinstance(msg, AssistantMessage):
                            parts: list[str] = []
                            for c in msg.content:
                                if isinstance(c, TextContent):
                                    parts.append(c.text)
                            return "\n".join(parts).strip() or None
                    return None

                def _stringify_user_msg(msg: Any) -> str:
                    if isinstance(msg, str):
                        return msg
                    from cubepi.providers.base import TextContent

                    content = getattr(msg, "content", None)
                    if isinstance(content, str):
                        return content
                    if isinstance(content, list):
                        parts_s = [c.text for c in content if isinstance(c, TextContent)]
                        return "\n".join(parts_s).strip()
                    return ""

                try:
                    from cubebox.db.engine import (
                        async_session_maker as _ue_session_maker,
                    )
                    from cubebox.repositories.user_event import UserEventRepository
                    from cubebox.services.reflection_runner import (
                        ReflectionInput,
                        ReflectionRunner,
                        ReflectionTurn,
                    )
                    from cubebox.services.user_event import UserEventService
                    from cubebox.tools.builtin.memory import create_memory_tools

                    _bus = getattr(self._app.state, "user_event_bus", None)
                    _memory_service_factory = extra_ref_holder.get("memory_service_factory")
                    _provider_ref = extra_ref_holder.get("provider")
                    _factory_ref = extra_ref_holder.get("llm_factory")
                    if (
                        _memory_service_factory is None
                        or _provider_ref is None
                        or _factory_ref is None
                    ):
                        logger.debug(
                            "skipping reflection for run_id={}: memory tools not available",
                            run_id,
                        )
                    elif _bus is not None:

                        def _make_reflection_agent(_inp: ReflectionInput) -> Any:
                            from cubepi import Agent, Model

                            from cubebox.llm.config import ModelCost
                            from cubebox.middleware.cost import (
                                CostMiddleware as _ReflCostMw,
                            )
                            from cubebox.prompts.reflection_system import (
                                REFLECTION_SYSTEM_PROMPT,
                            )

                            _mem_tools = create_memory_tools(
                                service_factory=_memory_service_factory,
                                conversation_id=_inp.conversation_id,
                                run_id=_inp.run_id,
                            )

                            def _refl_price_lookup(
                                provider_name_: str, model_id_: str
                            ) -> ModelCost | None:
                                assert _factory_ref is not None  # narrowed above
                                pcfg = _factory_ref.llm_config.providers.get(provider_name_)
                                if pcfg is None:
                                    return None
                                for m in pcfg.models:
                                    if m.id == model_id_:
                                        cost: ModelCost | None = m.cost
                                        return cost
                                return None

                            _refl_mw: list[Any] = [
                                _ReflCostMw(
                                    org_id=ctx.org_id,
                                    workspace_id=ctx.workspace_id,
                                    user_id=ctx.user_id,
                                    conversation_id=_inp.conversation_id,
                                    price_lookup=_refl_price_lookup,
                                )
                            ]

                            return Agent(
                                provider=_provider_ref,
                                model=Model(id=model_id, provider=provider_name),
                                system_prompt=REFLECTION_SYSTEM_PROMPT,
                                tools=_mem_tools,
                                middleware=_refl_mw,
                            )

                        async def _run_reflection(
                            agent_ref: Any = agent,
                            user_msg_ref: Any = _user_msg,
                            bus: Any = _bus,
                            agent_factory: Any = _make_reflection_agent,
                        ) -> None:
                            try:
                                await asyncio.wait_for(agent_ref.wait_for_idle(), timeout=10.0)
                            except (TimeoutError, Exception):
                                logger.warning(
                                    "reflection: agent not idle within 10s, skipping run_id={}",
                                    run_id,
                                )
                                return
                            last_assistant = _last_assistant_text(agent_ref.state.messages)
                            if not last_assistant:
                                return
                            user_msg_text = _stringify_user_msg(user_msg_ref)
                            inp = ReflectionInput(
                                conversation_id=conversation_id,
                                run_id=run_id,
                                user_id=ctx.user_id,
                                workspace_id=ctx.workspace_id,
                                turn=ReflectionTurn(
                                    user_message=user_msg_text,
                                    assistant_message=last_assistant,
                                    tool_summaries=[],
                                ),
                            )
                            async with _ue_session_maker() as _session:
                                _repo = UserEventRepository(_session)
                                _svc = UserEventService(repo=_repo, bus=bus)
                                _runner = ReflectionRunner(
                                    user_event_service=_svc,
                                    agent_factory=agent_factory,
                                )
                                await _runner.reflect(inp)

                        _refl_task = asyncio.create_task(
                            _run_reflection(), name=f"reflection:{run_id}"
                        )
                        self._reflection_tasks.add(_refl_task)
                        _refl_task.add_done_callback(self._reflection_tasks.discard)
                except Exception:
                    logger.warning(
                        "failed to schedule reflection for run_id={}", run_id, exc_info=True
                    )

                # T6: classify the terminal state. Three success outcomes:
                #   - no DB pending → completed
                #   - DB pending but no HitlRequestEvent this turn → stale
                #     leftover; clear it and treat as completed
                #   - DB pending and HitlRequestEvent fired → genuine new
                #     pause (auto-detach hook already detached the agent)
                from cubebox.streams.hitl_resume import classify_terminal_status

                final_pending = await agent.load_pending_hitl_request()
                classification = classify_terminal_status(
                    final_pending=final_pending,
                    answered_question_id=None,  # prompt path
                    saw_hitl_request_event=auto_detach.detached,
                )
                if classification.clear_pending:
                    await cp.save_pending_request(conversation_id, None)
                final_status = classification.status
            finally:
                # Stop accepting steers for this run before tearing down.
                self._agents.pop(run_id, None)
                self._hitl_channels.pop(run_id, None)
                # Signal drainer and wait for it to flush remaining events so
                # all SSE dicts are published before citation buffers flush.
                await sse_queue.put(None)
                await drainer

        for agent_key in list(citation_buffers):
            await flush_citation_buffer(agent_key, agent_key)
        return final_status

    async def _run_cubepi_respond_path(
        self,
        *,
        ctx: RunContext,
        run_id: str,
        conversation_id: str,
        question_id: str,
        answer: Any,
        claim_token: str,
        effective_system_prompt: str,
        publish_stream_event: Any,
        flush_citation_buffer: Any,
        citation_buffers: dict[str | None, str],
        sandbox: Any | None = None,
        skill_catalog: Any | None = None,
        catalog_session: Any | None = None,
    ) -> str:
        """Resume a paused HITL conversation by delivering ``answer`` to a
        cubepi agent via ``agent.respond``.

        Mirrors :meth:`_run_cubepi_path` but:

        * calls :meth:`cubepi.Agent.respond` instead of ``agent.prompt`` — no
          new user message, no memory snapshot, no attachments;
        * reuses the existing ``run_id`` so events stream into the same Redis
          key the SSE consumer is still tailing;
        * classifies the terminal state with ``answered_question_id`` set so
          a stale dangling pending matching the answer we just delivered is
          treated as ``completed`` rather than ``paused_hitl``;
        * writes the terminal status via
          :func:`finalize_run_meta_if_claim_matches` so we only clobber the
          meta row while our claim still owns it.
        """
        from cubebox.agents.checkpointer import init_checkpointer
        from cubebox.agents.stream import convert_agent_event_to_sse
        from cubebox.middleware.citations.counter import citation_counter_var
        from cubebox.streams.hitl_resume import (
            classify_terminal_status,
            finalize_run_meta_if_claim_matches,
        )

        # Late-binding holder for middleware closures (provider_name,
        # model_id, mem_repo_factory, extra). Same ferry pattern as the
        # prompt path — we read provider_name/model_id from it for the
        # liveness writeback below.
        extra_ref_holder: dict[str, Any] = {"extra": None}

        sse_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async with init_checkpointer() as cp:
            # Seed the citation counter past markers already persisted in
            # this conversation. The respond turn appends new tool results,
            # so without seeding we'd collide with citations the original
            # prompt turn already emitted.
            try:
                _hist = await cp.load(conversation_id)
            except Exception as _seed_exc:
                logger.warning("Citation seed (respond): failed to load history: {}", _seed_exc)
                _hist = None
            if _hist is not None and _hist.messages:
                _counter = citation_counter_var.get()
                if _counter is not None:
                    await _counter.seed_from_messages(_hist.messages)

            agent, _all_tools, sandbox_hitl_channel = await self._build_agent_for_conversation(
                ctx=ctx,
                conversation_id=conversation_id,
                run_id=run_id,
                cp=cp,
                sandbox=sandbox,
                skill_catalog=skill_catalog,
                catalog_session=catalog_session,
                effective_system_prompt=effective_system_prompt,
                extra_ref_holder=extra_ref_holder,
                sse_queue=sse_queue,
                publish_stream_event=publish_stream_event,
            )
            extra_ref_holder["extra"] = agent._extra

            auto_detach = _build_auto_detach_listener(agent)

            def _on_event(evt: Any, _signal: Any = None) -> None:
                # auto_detach runs first so a follow-up HitlRequestEvent
                # detaches the agent before SSE conversion; T6 reads
                # `auto_detach.detached` in the terminal block below.
                auto_detach(evt, _signal)
                for d in convert_agent_event_to_sse(evt):
                    sse_queue.put_nowait(d)

            agent.subscribe(_on_event)
            self._agents[run_id] = agent
            if sandbox_hitl_channel is not None:
                self._hitl_channels[run_id] = sandbox_hitl_channel
            drainer = asyncio.create_task(_drain_cubepi_sse_queue(sse_queue, publish_stream_event))

            from cubepi.tracing import trace, tracing_context

            from cubebox.llm.runtime_writeback import (
                schedule_runtime_status_writeback as _schedule_writeback,
            )

            tracer = getattr(self._app.state, "tracer", None)
            provider_name: str = extra_ref_holder["provider_name"]
            model_id: str = extra_ref_holder["model_id"]
            # turn_kind=respond distinguishes resume spans from the original
            # prompt span when grouped by conversation_id in the trace store.
            _trace_meta = {
                k: str(v)
                for k, v in (
                    ("run_id", run_id),
                    ("conversation_id", conversation_id),
                    ("user_id", ctx.user_id),
                    ("org_id", ctx.org_id),
                    ("workspace_id", ctx.workspace_id),
                    ("turn_kind", "respond"),
                )
                if v is not None
            }
            # Default to "errored" so an exception path (provider failure,
            # tool body raising, DB error mid-respond, etc.) doesn't write
            # "completed" via the CAS-guarded finalize. The DB pending row
            # is intentionally NOT cleared on exception — the user retries;
            # status=errored leaves the meta in a state claim_resume rejects
            # cleanly (we'd need a separate recovery path to retry), which
            # matches the existing crash story.
            final_status: str = "errored"
            try:
                try:
                    with tracing_context(metadata=_trace_meta):
                        async with trace(tracer, agent):
                            await agent.respond(question_id=question_id, answer=answer)
                except BaseException as _run_exc:
                    _schedule_writeback(
                        org_id=ctx.org_id,
                        provider_slug=provider_name,
                        model_id=model_id,
                        exc=_run_exc,
                    )
                    raise
                else:
                    _schedule_writeback(
                        org_id=ctx.org_id,
                        provider_slug=provider_name,
                        model_id=model_id,
                        exc=None,
                    )
                    final_pending = await agent.load_pending_hitl_request()
                    classification = classify_terminal_status(
                        final_pending=final_pending,
                        answered_question_id=question_id,
                        saw_hitl_request_event=auto_detach.detached,
                    )
                    if classification.clear_pending:
                        await cp.save_pending_request(conversation_id, None)
                        # T12: emit a synthetic *_resolved so the frontend
                        # can drop the stale "pending" UI.
                        await _emit_synthetic_resolved(
                            publish_stream_event, final_pending, question_id
                        )
                    final_status = classification.status
            finally:
                # CAS guard: only write the terminal status if our claim
                # token still owns the meta row. A racing flow that took
                # over the slot wins the row; our finalize is a no-op.
                await finalize_run_meta_if_claim_matches(
                    self._redis,
                    prefix=self._key_prefix,
                    run_id=run_id,
                    claim_token=claim_token,
                    status=final_status,
                )
                self._agents.pop(run_id, None)
                self._hitl_channels.pop(run_id, None)
                await sse_queue.put(None)
                await drainer

        for agent_key in list(citation_buffers):
            await flush_citation_buffer(agent_key, agent_key)
        return final_status

    async def _build_agent_for_conversation(
        self,
        *,
        ctx: RunContext,
        conversation_id: str,
        run_id: str,
        cp: Any,
        sandbox: Any | None,
        skill_catalog: Any | None,
        catalog_session: Any | None,
        effective_system_prompt: str,
        extra_ref_holder: dict[str, Any],
        sse_queue: asyncio.Queue[dict[str, Any] | None],
        publish_stream_event: Any,
        trigger: str = "interactive",
    ) -> tuple[Any, list[Any], Any]:
        """Build provider + middleware + tools + channel + agent for a conversation.

        Shared by the prompt path (:meth:`_run_cubepi_path`), the future respond
        path (T8), and the cancel-paused-run path (T10). Returns
        ``(agent, all_tools, sandbox_hitl_channel)``.

        The HITL channel is a :class:`cubepi.hitl.CheckpointedChannel` wired
        with ``run_id`` so every pause writes ``pending_request`` and
        ``pending_run_id`` to the cubepi_threads row in a single atomic
        statement — which is what lets a different worker pick up the answer
        and resume without losing the request identity.

        ``cp`` (the checkpointer) is owned by the CALLER so the same
        checkpointer instance drives both the channel and the agent. Pass
        ``sandbox=None`` / ``skill_catalog=None`` / ``catalog_session=None``
        when building from a context that has none (e.g. the cancel path
        opens the agent only to drive a final SSE emission).

        ``extra_ref_holder`` is the late-binding dict that middleware closures
        read at request time. The caller MUST populate
        ``extra_ref_holder["extra"] = agent._extra`` after this factory
        returns, before the first prompt invocation.
        """
        from collections.abc import AsyncIterator as _AsyncIterator
        from contextlib import asynccontextmanager as _asynccontextmanager

        from cubebox.agents.graph import create_cubebox_agent
        from cubebox.db.engine import async_session_maker
        from cubebox.llm.cache_markers import CubeboxCacheMarkerPolicy
        from cubebox.llm.factory import LLMFactory

        try:
            async with async_session_maker() as llm_session:
                factory = LLMFactory(
                    session=llm_session,
                    org_id=ctx.org_id,
                    encryption_backend=self._app.state.encryption_backend,
                )
                (
                    provider_name,
                    model_id,
                    provider_config,
                ) = await factory.resolve_default_provider_and_config()
                await llm_session.commit()
        except Exception:
            logger.warning("LLMFactory DB load failed for cubepi path, falling back to config-only")
            factory = LLMFactory()
            (
                provider_name,
                model_id,
                provider_config,
            ) = await factory.resolve_default_provider_and_config()

        # Resolve model config to extract max_tokens forwarded to the provider.
        try:
            _model_config = factory.get_model_config(provider_name, model_id)
            _model_max_tokens: int = _model_config.max_tokens or 32000
            _model_temperature: float = 0.7  # ModelConfig has no temperature field
        except Exception:
            _model_max_tokens = 32000
            _model_temperature = 0.7

        # TODO(PR #84 review - fallback chains TBD): no fallback chain
        # implementation yet. cubepi v0.3.0 has no equivalent of
        # with_fallbacks(), so resolved ModelConfig.fallback_models are
        # currently ignored. Tracked as a follow-up once either cubepi
        # upstream supports fallback chains or we wrap the provider on
        # cubebox's side.
        provider = factory.build_cubepi_provider(
            provider_config, cache_policy=CubeboxCacheMarkerPolicy()
        )

        # --- Compose tool list ---
        # Tool registration order is deliberately stable — changes invalidate
        # the prompt cache prefix. The intended order is:
        #   sandbox(execute/write_file/edit_file/file_read)
        #   → save_artifact
        #   → write_todos
        #   → subagent
        #   → calculator/datetime
        #   → memory_*
        #   → load_skill
        #   → find_skills
        #   → view_images
        #   → generate_image  (sandbox-gated)
        #   → mcp_tools
        #
        # Middleware that contributes tools writes to _sandbox_tools,
        # _artifact_tools, _todo_tools, _subagent_tools rather than all_tools.
        # All other tools accumulate in _builtin_tools.  At the end we merge
        # them in the correct order.

        from cubebox.tools.registry import list_builtin_tools

        _sandbox_tools: list[Any] = []
        _artifact_tools: list[Any] = []
        _todo_tools: list[Any] = []
        _subagent_tools: list[Any] = []
        _builtin_tools: list[Any] = list(list_builtin_tools())

        # Memory tools — service factory opened per tool call.
        # Placed before view_images and load_skill to keep the cache-prefix
        # tool order: calculator → datetime → memory_save → memory_search
        # → memory_update → load_skill → view_images → mcp_tools
        _memory_service_factory: Any = None
        try:
            from cubebox.db.engine import async_session_maker as _mem_session_maker
            from cubebox.repositories.memory import MemoryRepository as _MemoryRepository
            from cubebox.services.memory import MemoryService as _MemoryService
            from cubebox.tools.builtin.memory import create_memory_tools

            @_asynccontextmanager
            async def _memory_service_factory() -> _AsyncIterator[_MemoryService]:
                async with _mem_session_maker() as _session:
                    _repo = _MemoryRepository(
                        _session,
                        user_id=ctx.user_id,
                        org_id=ctx.org_id,
                        workspace_id=ctx.workspace_id,
                    )
                    yield _MemoryService(
                        _repo,
                        user_id=ctx.user_id,
                        org_id=ctx.org_id,
                        workspace_id=ctx.workspace_id,
                    )

            _builtin_tools.extend(
                create_memory_tools(
                    service_factory=_memory_service_factory,
                    conversation_id=conversation_id,
                    run_id=run_id,
                )
            )
        except Exception as _exc:
            logger.warning("memory tools unavailable for cubepi run: {}", _exc)

        # load_skill — requires a non-None catalog (may be absent if DB is down)
        if skill_catalog is not None:
            try:
                from cubebox.tools.builtin.load_skill import create_load_skill_tool

                _builtin_tools.append(
                    create_load_skill_tool(
                        catalog=skill_catalog,
                        workspace_id=ctx.workspace_id,
                        org_id=ctx.org_id,
                    )
                )
            except Exception as _exc:
                logger.warning("load_skill unavailable for cubepi run: {}", _exc)

        # view_images — per-request DI: objectstore + LLM capabilities.
        # Must come after memory tools and load_skill to preserve the
        # cache-prefix tool order.
        try:
            from cubebox.llm.capabilities import LLMCapabilities
            from cubebox.objectstore import get_objectstore_client
            from cubebox.tools.builtin.view_images import make_view_images_tool

            _builtin_tools.append(
                make_view_images_tool(
                    org_id=ctx.org_id,
                    workspace_id=ctx.workspace_id,
                    objectstore=get_objectstore_client(),
                    capabilities=LLMCapabilities(factory.llm_config),
                )
            )
        except Exception as _exc:
            logger.warning("view_images unavailable for cubepi run: {}", _exc)

        # show_widget — UI-only tool; no DI. Fixed position in the builtin tool
        # order to keep the prompt-cache prefix stable.
        try:
            from cubebox.tools.builtin.show_widget import make_show_widget_tool

            _builtin_tools.append(make_show_widget_tool())
        except Exception as _exc:
            logger.warning("show_widget unavailable for cubepi run: {}", _exc)

        # generate_image — sandbox-gated; enabled only when image_generation config is active.
        # Builds a per-run provider instance via create_images_provider — never the global registry.
        if sandbox is not None:
            try:
                from cubepi.providers.images.types import ImagesModel as _ImagesModel

                from cubebox.llm.config import get_image_generation_config
                from cubebox.tools.builtin.generate_image import make_generate_image_tool

                _img_cfg = get_image_generation_config()
                if not _img_cfg.enabled or not _img_cfg.api_key:
                    logger.info(
                        "generate_image unavailable: image_generation not enabled or api_key absent"
                    )
                else:
                    _images_provider = create_images_provider(
                        _img_cfg.api,
                        api_key=_img_cfg.api_key,
                        base_url=_img_cfg.base_url or None,
                    )
                    _images_model = _ImagesModel(
                        id=_img_cfg.model,
                        provider="image-gen",
                        api=_img_cfg.api,
                    )
                    _builtin_tools.append(
                        make_generate_image_tool(
                            org_id=ctx.org_id,
                            workspace_id=ctx.workspace_id,
                            conversation_id=conversation_id,
                            sandbox=sandbox,
                            images_provider=_images_provider,
                            images_model=_images_model,
                        )
                    )
            except Exception as _exc:
                logger.warning("generate_image unavailable for cubepi run: {}", _exc)

        # MCP tools — per-workspace enabled HTTP MCP connectors. Reads from
        # the four-layer ``mcp_connector_installs`` / ``mcp_workspace_connector_states`` /
        # ``mcp_credential_grants`` tables via :class:`MCPEffectiveConnectorService`.
        mcp_citation_configs: dict[str, Any] = {}

        try:
            from cubebox.credentials.dependencies import build_credential_service
            from cubebox.mcp.cubepi_runtime import load_workspace_mcp_tools_for_cubepi
            from cubebox.mcp.effective import MCPEffectiveConnectorService
            from cubebox.mcp.oauth.metadata import OAuthMetadataDiscovery
            from cubebox.mcp.oauth.token_manager import OAuthTokenManager
            from cubebox.repositories.credential import CredentialRepository
            from cubebox.repositories.mcp import (
                MCPConnectorInstallRepository,
                MCPConnectorTemplateRepository,
                MCPCredentialGrantRepository,
                MCPWorkspaceConnectorStateRepository,
            )

            async with async_session_maker() as effective_session:
                effective_cred_service = build_credential_service(
                    effective_session,
                    self._app.state.encryption_backend,
                    org_id=ctx.org_id,
                    actor_user_id=ctx.user_id,
                )
                # Reuse the shared OAuth metadata cache + httpx client if the
                # app already has them (the request-scoped DI providers stash
                # one on app.state). When absent (first ever MCP run) build a
                # short-lived pair — the next request-scoped consumer will
                # promote them onto app.state.
                _http_client = getattr(self._app.state, "_mcp_oauth_http_client", None)
                if _http_client is None:
                    import httpx as _httpx

                    _http_client = _httpx.AsyncClient(timeout=30.0)
                    self._app.state._mcp_oauth_http_client = _http_client
                _metadata = getattr(self._app.state, "_mcp_oauth_metadata_discovery", None)
                if _metadata is None:
                    _metadata = OAuthMetadataDiscovery(_http_client)
                    self._app.state._mcp_oauth_metadata_discovery = _metadata
                _token_manager = OAuthTokenManager(
                    http_client=_http_client,
                    redis=self._redis,
                    encryption_backend=self._app.state.encryption_backend,
                    credential_repo=CredentialRepository(effective_session, org_id=ctx.org_id),
                    metadata=_metadata,
                )
                _grant_repo = MCPCredentialGrantRepository(effective_session, org_id=ctx.org_id)
                _effective_service = MCPEffectiveConnectorService(
                    template_repo=MCPConnectorTemplateRepository(effective_session),
                    install_repo=MCPConnectorInstallRepository(
                        effective_session, org_id=ctx.org_id
                    ),
                    state_repo=MCPWorkspaceConnectorStateRepository(
                        effective_session, org_id=ctx.org_id
                    ),
                    grant_repo=_grant_repo,
                    org_id=ctx.org_id,
                    token_manager=_token_manager,
                )
                (
                    _new_tools,
                    _new_citations,
                ) = await load_workspace_mcp_tools_for_cubepi(
                    effective_service=_effective_service,
                    token_manager=_token_manager,
                    workspace_id=ctx.workspace_id,
                    org_id=ctx.org_id,
                    user_id=ctx.user_id,
                    cred_service=effective_cred_service,
                    signer=self._app.state.mcp_user_token_signer,
                    grant_repo=_grant_repo,
                )
                _builtin_tools.extend(_new_tools)
                mcp_citation_configs.update(_new_citations)
        except Exception as _exc:
            logger.warning("MCP tools unavailable for cubepi run: {}", _exc)

        # Platform action tools (scheduled_tasks, skills, etc.) — via the
        # capability registry. Automated runs get read-only tools (mutation gate).
        try:
            from cubebox.agents.actions.capabilities.skills import SkillDeps
            from cubebox.agents.actions.registry import (
                tools_for_run as _action_tools_for_run,
            )
            from cubebox.repositories.membership import MembershipRepository
            from cubebox.repositories.organization import OrganizationRepository
            from cubebox.skills.sources.registry import SkillsAdapterManager

            async with async_session_maker() as _action_session:
                _role = await MembershipRepository(_action_session).get_role(
                    user_id=ctx.user_id,
                    workspace_id=ctx.workspace_id,
                )

            if _role is not None:
                from collections.abc import (
                    AsyncIterator as _ActionsAsyncIterator,
                )
                from contextlib import (
                    asynccontextmanager as _actions_acm,
                )

                from cubebox.agents.actions.context import (
                    ScopeContext as _ScopeContext,
                )

                @_actions_acm
                async def _action_ctx_factory() -> _ActionsAsyncIterator[tuple[_ScopeContext, Any]]:
                    async with async_session_maker() as _sess:
                        yield (
                            _ScopeContext(
                                org_id=ctx.org_id,
                                workspace_id=ctx.workspace_id,
                                user_id=ctx.user_id,
                                role=_role,
                                conversation_id=conversation_id,
                            ),
                            _sess,
                        )

                # Construct SkillDeps only when the skill catalog session is
                # available. Mirrors today's guard: if the catalog DB is
                # unreachable, the skills capability is silently skipped
                # (same as load_skill). The inner try isolates skill-deps
                # setup failures so the rest of the action tools
                # (e.g. scheduled_tasks) still register.
                _skill_deps: SkillDeps | None = None
                if skill_catalog is not None and catalog_session is not None:
                    try:
                        _org = await OrganizationRepository(catalog_session).get(ctx.org_id)
                        if _org is not None:
                            _registry = await SkillsAdapterManager.build(
                                session=catalog_session,
                                catalog=skill_catalog,
                                org_id=ctx.org_id,
                                org_slug=_org.slug,
                                workspace_id=ctx.workspace_id,
                            )
                            _skill_deps = SkillDeps(
                                catalog=skill_catalog,
                                catalog_session=catalog_session,
                                registry=_registry,
                                org_id=ctx.org_id,
                                org_slug=_org.slug,
                                workspace_id=ctx.workspace_id,
                            )
                    except Exception as _skill_exc:  # noqa: BLE001
                        logger.warning(
                            "skills capability unavailable for cubepi run: {}",
                            _skill_exc,
                        )

                _builtin_tools.extend(
                    _action_tools_for_run(
                        _action_ctx_factory,
                        allow_mutations=(trigger == "interactive"),
                        skill_deps=_skill_deps,
                    )
                )
        except Exception as _exc:
            logger.warning(
                "platform action tools unavailable for cubepi run: {}",
                _exc,
            )

        # --- Build the 11 cubepi middleware (M3.f) ---
        # The caller owns ``extra_ref_holder`` and populates ``["extra"]`` from
        # ``agent._extra`` after this factory returns; this closure reads the
        # holder at request time, well after the agent build.
        def _extra_ref() -> dict[str, Any]:
            ref: dict[str, Any] | None = extra_ref_holder["extra"]
            if ref is None:
                return {}
            return ref

        cubepi_middleware: list[Any] = []

        # 1. AttachmentHintMiddleware — no deps
        try:
            from cubebox.middleware.attachments import AttachmentHintMiddleware

            cubepi_middleware.append(AttachmentHintMiddleware())
        except Exception as _exc:
            logger.warning("AttachmentHintMiddleware unavailable: {}", _exc)

        # 2. ArtifactMiddleware — needs sandbox
        if sandbox is not None:
            try:
                from cubebox.middleware.artifacts import ArtifactMiddleware

                artifact_mw = ArtifactMiddleware(
                    sandbox=sandbox,
                    conversation_id=conversation_id,
                    org_id=ctx.org_id,
                    workspace_id=ctx.workspace_id,
                )
                cubepi_middleware.append(artifact_mw)
                # Middleware tools (save_artifact) collected for ordered merge below
                _artifact_tools.extend(artifact_mw.tools)
            except Exception as _exc:
                logger.warning("ArtifactMiddleware unavailable: {}", _exc)

        # 3. CitationMiddleware — needs citation_configs (empty dict = pass-through)
        try:
            from cubebox.middleware.citation import CitationMiddleware
            from cubebox.middleware.citations.counter import citation_event_queue

            cubepi_middleware.append(
                CitationMiddleware(
                    citation_configs=mcp_citation_configs,
                    event_queue=citation_event_queue.get(None),
                )
            )
        except Exception as _exc:
            logger.warning("CitationMiddleware unavailable: {}", _exc)

        # 4. MemoryMiddleware — needs repo_factory.
        # The factory is defined unconditionally (outside the try) so the
        # caller can reuse it for the per-turn relevance-snapshot pass even
        # if the MemoryMiddleware build below fails. It is also stashed on
        # ``extra_ref_holder`` for the caller to pick up after the factory
        # returns — the agent doesn't expose it.
        from collections.abc import AsyncIterator as _AsyncIterator2
        from contextlib import asynccontextmanager as _asynccontextmanager2

        from cubebox.db.engine import async_session_maker as _mem2_session_maker
        from cubebox.repositories.memory import MemoryRepository as _MemRepo2

        @_asynccontextmanager2
        async def _mem_repo_factory() -> _AsyncIterator2[_MemRepo2]:
            async with _mem2_session_maker() as _s:
                yield _MemRepo2(
                    _s,
                    user_id=ctx.user_id,
                    org_id=ctx.org_id,
                    workspace_id=ctx.workspace_id,
                )

        try:
            from cubebox.middleware.memory import MemoryMiddleware

            cubepi_middleware.append(MemoryMiddleware(repo_factory=_mem_repo_factory))
        except Exception as _exc:
            logger.warning("MemoryMiddleware unavailable: {}", _exc)

        # 5. CompactionMiddleware — needs extra_ref + summary_llm + config
        try:
            from cubepi import Model as _CompModel

            from cubebox.config import config as _comp_cfg
            from cubebox.llm.factory import LLMFactory as _CompLLMFactory
            from cubebox.llm.oneshot import OneShotLLM as _CompOneShot
            from cubebox.middleware.compaction import CompactionMiddleware

            if _comp_cfg.get("compaction.enabled", False):
                _summary_provider = _comp_cfg.get("compaction.summary_provider")
                _summary_model_id = _comp_cfg.get("compaction.summary_model")
                _comp_factory = _CompLLMFactory()
                _summary_provider_config = _comp_factory.llm_config.providers[_summary_provider]
                _summary_provider_inst = _comp_factory.build_cubepi_provider(
                    _summary_provider_config, cache_policy=None
                )
                _summary_llm = _CompOneShot(
                    _summary_provider_inst,
                    _CompModel(id=_summary_model_id, provider=_summary_provider),
                )
                _ctx_window: int = int(_comp_cfg.get("compaction.fallback_context_window", 64000))
                _ratio = float(_comp_cfg.get("compaction.threshold_ratio", 0.7))
                cubepi_middleware.append(
                    CompactionMiddleware(
                        extra_ref=_extra_ref,
                        summary_llm=_summary_llm,
                        max_tokens_before_compact=int(_ctx_window * _ratio),
                        keep_recent_messages=int(
                            _comp_cfg.get("compaction.keep_recent_messages", 8)
                        ),
                        max_summary_tokens=int(
                            _comp_cfg.get("compaction.max_summary_tokens", 1024)
                        ),
                        min_compact_messages=int(
                            _comp_cfg.get("compaction.min_compact_messages", 4)
                        ),
                    )
                )
                logger.info(
                    "CompactionMiddleware enabled (threshold={} tokens)",
                    int(_ctx_window * _ratio),
                )
        except Exception as _exc:
            logger.warning("CompactionMiddleware not loaded: {}", _exc)

        # 6. SandboxMiddleware — needs sandbox. The HITL channel is built here
        # so that the SandboxMiddleware's confirm-gate and the agent share the
        # same CheckpointedChannel instance (which writes pending_request +
        # pending_run_id atomically via ``cp``).
        sandbox_hitl_channel: Any = None
        if sandbox is not None:
            try:
                from cubebox.middleware.sandbox import SandboxMiddleware
                from cubebox.sandbox.manager import get_sandbox_manager

                # Resolve the org's command_rules via the manager so DB access
                # stays behind the manager and the middleware only sees its
                # slice of policy.
                #
                # Fail-CLOSED on resolution failure: if we can't read the org's
                # rules (DB transient, malformed persisted policy, …) we MUST
                # NOT pass an empty list — empty means allow-all, which would
                # silently bypass any deny/confirm rules the admin configured.
                # Install a single deny-all rule instead so every execute call
                # blocks with the standard "blocked by org policy" message
                # until the next request resolves cleanly.
                _command_rules: list[dict[str, Any]]
                try:
                    _command_rules = await get_sandbox_manager().resolve_command_rules(ctx.org_id)
                except Exception as _exc:
                    logger.error(
                        "Failed to resolve sandbox command_rules for org {}; "
                        "failing CLOSED with deny-all until next request "
                        "resolves: {}",
                        ctx.org_id,
                        _exc,
                    )
                    _command_rules = [
                        {"action": "deny", "pattern": "*"},
                    ]

                from cubepi.hitl import CheckpointedChannel

                sandbox_hitl_channel = CheckpointedChannel(
                    checkpointer=cp,
                    thread_id=conversation_id,
                    run_id=run_id,
                    default_timeout=None,
                )
                sandbox_mw = SandboxMiddleware(
                    sandbox=sandbox,
                    conversation_id=conversation_id,
                    workspace_id=ctx.workspace_id,
                    command_rules=_command_rules,
                    channel=sandbox_hitl_channel,
                )
                cubepi_middleware.append(sandbox_mw)
                # Middleware tools (execute, write_file, edit_file, file_read) collected for
                # ordered merge below
                _sandbox_tools.extend(sandbox_mw.tools)
                # ask_user built-in tool shares the same HITL channel so the agent
                # can ask structured questions that pause execution just like a confirm rule.
                from cubepi.hitl import ask_user_tool

                _builtin_tools.append(ask_user_tool(sandbox_hitl_channel))
            except Exception as _exc:
                logger.warning("SandboxMiddleware unavailable: {}", _exc)

        # 7. SkillsMiddleware — needs extra_ref
        try:
            from cubebox.middleware.skills import SkillsMiddleware

            cubepi_middleware.append(SkillsMiddleware(extra_ref=_extra_ref))
        except Exception as _exc:
            logger.warning("SkillsMiddleware unavailable: {}", _exc)

        # 8. SubAgentMiddleware — needs provider + model info + shared tools
        try:
            from cubebox.middleware.subagents import SubAgentMiddleware

            # Cost middleware (if present) is passed as inherited_middleware for depth attribution.
            # Build the cost instance separately so SubAgent can clone it.
            _cost_mw_for_inherit: list[Any] = []
            try:
                from cubebox.middleware.cost import CostMiddleware as _CostMwPi

                _cost_mw_for_inherit = [
                    _CostMwPi(
                        org_id=ctx.org_id,
                        workspace_id=ctx.workspace_id,
                        user_id=ctx.user_id,
                        conversation_id=conversation_id,
                    )
                ]
            except Exception:
                pass

            subagent_mw = SubAgentMiddleware(
                subagent_map={},
                default_provider=provider,
                default_model_id=model_id,
                default_provider_name=provider_name,
                # Pass all tools (sandbox + artifact + builtin) collected so far
                # as shared tools for subagent spawning, minus show_widget
                # (top-level only in v1).
                shared_tools=_subagent_shared_tools(
                    _sandbox_tools + _artifact_tools + _builtin_tools
                ),
                inherited_middleware=_cost_mw_for_inherit,
                tracer=getattr(self._app.state, "tracer", None),
            )
            cubepi_middleware.append(subagent_mw)
            _subagent_tools.extend(subagent_mw.tools)
        except Exception as _exc:
            logger.warning("SubAgentMiddleware unavailable: {}", _exc)

        # 9. CostMiddleware — needs org/workspace/user/conversation IDs
        try:
            from cubebox.llm.config import ModelCost
            from cubebox.middleware.cost import CostMiddleware

            # Resolve ModelCost for any (provider, model_id) the agent reports.
            # The factory's llm_config holds the merged YAML + DB provider config
            # so this lookup honors per-org overrides.
            def _price_lookup(provider: str, model_id: str) -> ModelCost | None:
                pcfg = factory.llm_config.providers.get(provider)
                if pcfg is None:
                    return None
                for m in pcfg.models:
                    if m.id == model_id:
                        return m.cost
                return None

            cubepi_middleware.append(
                CostMiddleware(
                    org_id=ctx.org_id,
                    workspace_id=ctx.workspace_id,
                    user_id=ctx.user_id,
                    conversation_id=conversation_id,
                    price_lookup=_price_lookup,
                )
            )
        except Exception as _exc:
            logger.warning("CostMiddleware unavailable: {}", _exc)

        # 10. TimestampMiddleware — no deps
        try:
            from cubebox.middleware.timestamps import TimestampMiddleware

            cubepi_middleware.append(TimestampMiddleware())
        except Exception as _exc:
            logger.warning("TimestampMiddleware unavailable: {}", _exc)

        # 11. TodoListMiddleware — needs extra_ref
        try:
            from cubebox.middleware.todo import TodoListMiddleware

            todo_mw = TodoListMiddleware(extra_ref=_extra_ref)
            cubepi_middleware.append(todo_mw)
            _todo_tools.extend(todo_mw.tools)
        except Exception as _exc:
            logger.warning("TodoListMiddleware unavailable: {}", _exc)

        # --- Final tool merge ---
        # Stable composition order — changes invalidate the prompt cache prefix:
        #   sandbox tools → artifact tools → todo tools → subagent tools
        #   → builtin tools (calculator/datetime/view_images/memory/load_skill/mcp)
        all_tools: list[Any] = (
            _sandbox_tools + _artifact_tools + _todo_tools + _subagent_tools + _builtin_tools
        )

        logger.info(
            "cubepi middleware stack: {} layers, {} total tools",
            len(cubepi_middleware),
            len(all_tools),
        )

        agent = create_cubebox_agent(
            provider=provider,
            model_id=model_id,
            provider_name=provider_name,
            system_prompt=effective_system_prompt,
            tools=all_tools,
            checkpointer=cp,
            thread_id=conversation_id,
            middleware=cubepi_middleware,
            max_tokens=_model_max_tokens,
            temperature=_model_temperature,
            # Reasoning-capable models think by default ("medium"); a
            # per-conversation toggle (UI) can override this later.
            reasoning=_model_config.reasoning,
            thinking="medium" if _model_config.reasoning else "off",
            channel=sandbox_hitl_channel,
        )

        # Stash provider_name / model_id / memory-repo factory on the bridge
        # dict so the caller doesn't have to re-resolve them (a second
        # ``factory.resolve_default_provider_and_config()`` would double the
        # DB load). The caller's runtime-status writeback and per-turn
        # relevance-snapshot pass both read from here.
        extra_ref_holder["provider_name"] = provider_name
        extra_ref_holder["model_id"] = model_id
        extra_ref_holder["mem_repo_factory"] = _mem_repo_factory
        # Reflection trigger in _run_cubepi_path needs these to build its
        # own short-lived agent for end-of-turn memory self-review.
        extra_ref_holder["memory_service_factory"] = _memory_service_factory
        extra_ref_holder["provider"] = provider
        extra_ref_holder["llm_factory"] = factory

        return agent, all_tools, sandbox_hitl_channel

    async def _maybe_consolidate_memory(self, *, conversation_id: str, ctx: RunContext) -> None:
        """Cheap per-run gate; spawn a tracked background consolidation task when
        due. Never raises into the run path."""
        try:
            from cubebox.config import config as _cfg
            from cubebox.services import memory_consolidation as mc

            if not _cfg.get("memory.consolidation.enabled", True):
                return
            await mc.note_run(self._redis, self._key_prefix, conversation_id)
            min_hours = float(_cfg.get("memory.consolidation.min_hours", mc.DEFAULT_MIN_HOURS))
            min_runs = int(_cfg.get("memory.consolidation.min_runs", mc.DEFAULT_MIN_RUNS))
            if not await mc.should_consolidate(
                self._redis,
                self._key_prefix,
                conversation_id,
                min_hours=min_hours,
                min_runs=min_runs,
            ):
                return

            from cubepi import Model

            from cubebox.db.engine import async_session_maker
            from cubebox.llm.factory import LLMFactory
            from cubebox.llm.oneshot import OneShotLLM

            async with async_session_maker() as _llm_session:
                factory = LLMFactory(
                    session=_llm_session,
                    org_id=ctx.org_id,
                    encryption_backend=self._app.state.encryption_backend,
                )
                (
                    provider_name,
                    model_id,
                    provider_config,
                ) = await factory.resolve_default_provider_and_config()
                await _llm_session.commit()
            provider = factory.build_cubepi_provider(provider_config, cache_policy=None)
            one_shot = OneShotLLM(provider, Model(id=model_id, provider=provider_name))

            task = asyncio.create_task(
                mc.run_consolidation(
                    redis=self._redis,
                    prefix=self._key_prefix,
                    conversation_id=conversation_id,
                    user_id=ctx.user_id,
                    org_id=ctx.org_id,
                    workspace_id=ctx.workspace_id,
                    one_shot=one_shot,
                    session_maker=async_session_maker,
                    min_hours=min_hours,
                    min_runs=min_runs,
                ),
                name=f"memcons:{conversation_id}",
            )
            self._consolidation_tasks.add(task)
            task.add_done_callback(self._consolidation_tasks.discard)
        except Exception:
            logger.warning("memory consolidation gate failed", exc_info=True)

    async def _execute_run(
        self,
        *,
        run_id: str,
        conversation_id: str,
        content: str,
        attachments: list[str],
        ctx: RunContext,
    ) -> None:
        from cubebox.api.routes.v1.conversations import _update_conversation_timestamp
        from cubebox.middleware.citations.counter import (
            CitationCounter,
            citation_counter_var,
            citation_event_queue,
        )
        from cubebox.middleware.subagents import subagent_event_queue
        from cubebox.schedules.completion_hook import record_scheduled_run_terminal_state

        sandbox = None
        sandbox_manager = None
        sandbox_create_task: asyncio.Task[Any] | None = None
        stream_task: asyncio.Task[None] | None = None
        catalog_session_ctx: Any | None = None
        catalog_session: Any | None = None
        skill_catalog: Any | None = None
        event_q: asyncio.Queue[tuple[str, Any, Any] | None] = asyncio.Queue()
        cv_token = subagent_event_queue.set(event_q)

        citation_counter = CitationCounter(start=1)
        cc_token = citation_counter_var.set(citation_counter)
        ce_token = citation_event_queue.set(event_q)

        citation_buffers: dict[str | None, str] = {}
        turn_usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }

        async def emit_status(phase: str, detail: str | None = None) -> None:
            data: dict[str, str] = {"phase": phase}
            if detail:
                data["detail"] = detail
            await self._append_event(
                run_id,
                conversation_id,
                StatusEvent(
                    timestamp=datetime.now(UTC).isoformat(),
                    data=data,
                ),
            )

        async def publish_event(event: AgentEvent) -> None:
            await self._append_event(run_id, conversation_id, event)

        async def flush_citation_buffer(
            agent_key: str | None,
            fallback_agent_id: str | None,
        ) -> None:
            buf = citation_buffers.get(agent_key, "")
            if not buf:
                return
            from cubebox.agents.schemas import TextDeltaEvent

            citation_buffers[agent_key] = ""
            await publish_event(
                TextDeltaEvent(
                    timestamp=datetime.now(UTC).isoformat(),
                    data={
                        "content": buf,
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                    agent_id=fallback_agent_id,
                )
            )

        async def publish_stream_event(sse_event: AgentEvent, agent_key: str | None) -> None:
            if sse_event.type == "text_delta":
                buffered = citation_buffers.get(agent_key, "") + str(
                    sse_event.data.get("content", "")
                )
                citation_buffers[agent_key] = ""
                last_open = buffered.rfind("【")
                if last_open != -1 and "】" not in buffered[last_open:]:
                    citation_buffers[agent_key] = buffered[last_open:]
                    buffered = buffered[:last_open]
                if buffered:
                    sse_event.data["content"] = buffered
                    await publish_event(sse_event)
                return

            await flush_citation_buffer(agent_key, sse_event.agent_id)
            if sse_event.type == "usage":
                for key in turn_usage:
                    turn_usage[key] += sse_event.data.get(key, 0)
            await publish_event(sse_event)

        # Drainer for the shared subagent/citation queue (see
        # _drain_subagent_citation_queue). Without this, SubAgentMiddleware
        # and CitationMiddleware push events that never reach the SSE
        # consumer, breaking live subagent rendering and live citations.
        # Typed `| None` so the success path can null it after the pre-Done
        # drain (see the DoneEvent block below) and the safety net in
        # `finally` skips when it already ran.
        event_q_drainer: asyncio.Task[None] | None = asyncio.create_task(
            _drain_subagent_citation_queue(event_q, publish_stream_event),
            name=f"event_q_drainer:{run_id}",
        )

        # Outer-scope default so `finally` can branch on terminal status.
        # The success path inside `try` overwrites this with the real
        # status returned by `_run_cubepi_path`; on exception the default
        # "errored" applies, which keeps the existing teardown semantics.
        final_status: str = "errored"

        try:
            # Open a long-lived session for the SkillCatalogService — used by
            # both SkillsMiddleware (read prompts) and LazySandbox (push files
            # to sandbox on first use). Same session is fine: skill reads are
            # idempotent and no writes happen here.
            try:
                from pathlib import Path

                from cubebox.config import config as _cfg
                from cubebox.db.engine import async_session_maker
                from cubebox.skills.cache import SkillCache
                from cubebox.skills.service import SkillCatalogService

                catalog_session_ctx = async_session_maker()
                catalog_session = await catalog_session_ctx.__aenter__()
                skill_catalog = SkillCatalogService(
                    session=catalog_session,
                    cache=SkillCache(
                        cache_root=Path(_cfg.get("skills.cache_root", "skills_cache"))
                    ),
                )
            except Exception as exc:
                logger.warning("Skill catalog unavailable for run: {}", exc)

            sandbox_factory = getattr(self._app.state, "sandbox_factory", None)
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
                            user_id=ctx.user_id,
                            org_id=ctx.org_id,
                            workspace_id=ctx.workspace_id,
                            workdir=config.get("sandbox.workdir", "/workspace"),
                            catalog=skill_catalog,
                        )
                    except Exception as exc:
                        logger.warning("Sandbox unavailable, continuing without: {}", exc)
                        await emit_status("sandbox_failed", detail=str(exc))

            # Resolve effective model + context_window for the DoneEvent. The
            # actual cubepi.Provider construction happens inside _run_cubepi_path.
            from cubebox.db.engine import async_session_maker
            from cubebox.llm.factory import LLMFactory

            context_window: int = 0
            try:
                async with async_session_maker() as ctx_session:
                    ctx_factory = LLMFactory(
                        session=ctx_session,
                        org_id=ctx.org_id,
                        encryption_backend=self._app.state.encryption_backend,
                    )
                    (
                        _ctx_provider,
                        _ctx_model_id,
                        _ctx_provider_config,
                    ) = await ctx_factory.resolve_default_provider_and_config()
                    await ctx_session.commit()
                _model_cfg = ctx_factory.get_model_config(_ctx_provider, _ctx_model_id)
                context_window = int(_model_cfg.context_window or 0)
            except Exception as exc:
                logger.debug("Could not resolve context_window for DoneEvent: {}", exc)

            from sqlmodel import select as sqlmodel_select

            from cubebox.models.agent_config import AgentConfig
            from cubebox.prompts.system import BASE_SYSTEM_PROMPT

            effective_system_prompt = BASE_SYSTEM_PROMPT
            try:
                if catalog_session is not None:
                    result = await catalog_session.execute(
                        sqlmodel_select(AgentConfig).where(
                            AgentConfig.org_id == ctx.org_id,
                            AgentConfig.workspace_id == ctx.workspace_id,
                        )
                    )
                    agent_cfg = result.scalar_one_or_none()
                else:
                    async with async_session_maker() as _cfg_session:
                        result = await _cfg_session.execute(
                            sqlmodel_select(AgentConfig).where(
                                AgentConfig.org_id == ctx.org_id,
                                AgentConfig.workspace_id == ctx.workspace_id,
                            )
                        )
                        agent_cfg = result.scalar_one_or_none()
                if agent_cfg and agent_cfg.system_prompt:
                    effective_system_prompt = BASE_SYSTEM_PROMPT + "\n\n" + agent_cfg.system_prompt
            except Exception as exc:
                logger.warning("Failed to load AgentConfig, using base prompt: {}", exc)

            # Inject the available-skills list so the model knows what it can
            # load via load_skill. Without this the load_skill tool exists but
            # the agent never learns which skills are available, so it never
            # uses them. Appended as a stable suffix (same enabled set across
            # turns) to preserve the prompt-cache prefix.
            try:
                if skill_catalog is not None:
                    _enabled_skills = await skill_catalog.list_enabled_for_workspace(
                        ctx.workspace_id, org_id=ctx.org_id
                    )
                    if _enabled_skills:
                        from cubebox.prompts.skills import SKILLS_PROMPT_TEMPLATE

                        _skills_list = "\n".join(
                            f"- `{s.name}` — {s.description}"
                            for s in sorted(_enabled_skills, key=lambda s: s.name)
                        )
                        effective_system_prompt += "\n\n" + SKILLS_PROMPT_TEMPLATE.format(
                            skills_list=_skills_list
                        )
            except Exception as exc:
                logger.warning("Failed to inject available-skills list: {}", exc)

            # show_widget guidelines — appended unconditionally at a fixed spot
            # so the cache prefix stays deterministic (the tool is always
            # registered). See backend/docs/prompt-cache-discipline.md.
            from cubebox.prompts.widget import WIDGET_GUIDELINES

            effective_system_prompt += "\n\n" + WIDGET_GUIDELINES

            final_status = await self._run_cubepi_path(
                ctx=ctx,
                run_id=run_id,
                conversation_id=conversation_id,
                content=content,
                attachments=attachments,
                effective_system_prompt=effective_system_prompt,
                publish_stream_event=publish_stream_event,
                flush_citation_buffer=flush_citation_buffer,
                citation_buffers=citation_buffers,
                sandbox=sandbox,
                skill_catalog=skill_catalog,
                catalog_session=catalog_session,
                trigger=ctx.trigger,
            )
            await _update_conversation_timestamp(
                conversation_id,
                org_id=ctx.org_id,
                workspace_id=ctx.workspace_id,
                user_id=ctx.user_id,
            )
            # Drain the subagent/citation queue BEFORE DoneEvent: SSE
            # consumers (and the frontend) close on `done`, so any event
            # still sitting in the drainer after the final tool call would
            # be dropped — leaving last-turn 【N-M】 markers without hover
            # data and last-turn subagent text missing until the
            # conversation is reloaded. Bounded wait_for so a stuck
            # consumer can't block run teardown forever (mirrors the
            # safety pattern in the `finally` block).
            if event_q_drainer is not None:
                with suppress(Exception):
                    event_q.put_nowait(None)
                try:
                    await asyncio.wait_for(event_q_drainer, timeout=5.0)
                except (TimeoutError, Exception):
                    if not event_q_drainer.done():
                        event_q_drainer.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await event_q_drainer
                event_q_drainer = None
            # --- Aggregate session-level token totals ---
            from cubebox.services.usage import SessionUsage, get_session_usage

            session_usage: SessionUsage = {
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_cache_read_tokens": 0,
                "total_cache_write_tokens": 0,
            }
            try:
                from cubebox.db.engine import async_session_maker

                async with async_session_maker() as billing_session:
                    session_usage = await get_session_usage(billing_session, conversation_id)
            except Exception:
                logger.warning("Failed to query session usage for done event")

            # paused_hitl is a terminal-but-not-done state from the
            # frontend's perspective: the run task is over (worker
            # released), but the conversation is still waiting on the
            # user's HITL answer. Stamp data.paused so the frontend's
            # done handler preserves pendingAsk / pendingConfirmMap
            # instead of wiping them via finalizeCompletedStream.
            done_data: dict[str, Any] = {
                "usage": {
                    "turn": dict(turn_usage),
                    "session": session_usage,
                    "context_window": context_window,
                }
            }
            if final_status == "paused_hitl":
                done_data["paused"] = True

            await self._append_event(
                run_id,
                conversation_id,
                DoneEvent(
                    timestamp=datetime.now(UTC).isoformat(),
                    data=done_data,
                ),
            )
            # Mark the run terminal AFTER appending DoneEvent so the SSE consumer
            # cannot observe active_run=None with no more events (which would cause
            # it to exit before the DoneEvent is in the Redis stream). T6 classifies
            # the success terminal state: "completed" or "paused_hitl"; the latter
            # means the agent detached on a new pending HITL request.
            await update_run_meta(
                self._redis,
                prefix=self._key_prefix,
                run_id=run_id,
                status=final_status,
            )
            await record_scheduled_run_terminal_state(run_id=run_id, run_status=final_status)
            await self._maybe_consolidate_memory(conversation_id=conversation_id, ctx=ctx)
        except asyncio.CancelledError:
            await update_run_meta(
                self._redis,
                prefix=self._key_prefix,
                run_id=run_id,
                status="cancelled",
            )
            await record_scheduled_run_terminal_state(run_id=run_id, run_status="cancelled")
            # Defense in depth: cubepi backfills tool_results for tool_calls
            # left dangling by a cancel, but if that cleanup was itself cut
            # short the persisted thread would still have orphan tool_calls
            # and every later turn would 400. Repair here too — idempotent, so
            # it's a no-op when cubepi already handled it.
            with suppress(Exception):
                await _repair_dangling_tool_calls(conversation_id)
            with suppress(Exception):
                await self._append_error(run_id, conversation_id, "Run cancelled", "Run cancelled")
            raise
        except Exception as exc:
            logger.error("Run {} failed: {}", run_id, exc, exc_info=True)
            await update_run_meta(
                self._redis,
                prefix=self._key_prefix,
                run_id=run_id,
                status="failed",
            )
            await record_scheduled_run_terminal_state(run_id=run_id, run_status="failed")
            with suppress(Exception):
                await self._append_error(
                    run_id,
                    conversation_id,
                    "An unexpected error occurred during execution",
                    str(exc),
                )
        finally:
            if stream_task is not None and not stream_task.done():
                stream_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stream_task

            # Safety net for error/cancel paths that bypassed the
            # pre-DoneEvent drain — shut the drainer down so the task
            # doesn't leak. The success path nulls `event_q_drainer` after
            # its own drain, so this block is a no-op then.
            if event_q_drainer is not None:
                with suppress(Exception):
                    event_q.put_nowait(None)
                try:
                    await asyncio.wait_for(event_q_drainer, timeout=5.0)
                except (TimeoutError, Exception):
                    if not event_q_drainer.done():
                        event_q_drainer.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await event_q_drainer

            if sandbox_create_task is not None and not sandbox_create_task.done():
                sandbox_create_task.cancel()
                with suppress(asyncio.CancelledError):
                    await sandbox_create_task

            try:
                subagent_event_queue.reset(cv_token)
            except ValueError:
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
                    with suppress(Exception):
                        await sandbox._manager.release(
                            sandbox.id,
                            org_id=ctx.org_id,
                            workspace_id=ctx.workspace_id,
                        )
                elif sandbox_manager and not isinstance(sandbox, LazySandbox):
                    with suppress(Exception):
                        await sandbox_manager.release(
                            sandbox.id,
                            org_id=ctx.org_id,
                            workspace_id=ctx.workspace_id,
                        )

            if catalog_session_ctx is not None:
                with suppress(Exception):
                    await catalog_session_ctx.__aexit__(None, None, None)

            self._agents.pop(run_id, None)
            # paused_hitl: keep the Redis active-run key as the lock that
            # blocks `start_run` from spawning a brand-new turn while the
            # user still owes an answer. Clearing it here would let a
            # racing send pass create_run() and skip the DB-pending guard
            # in start_run (the guard only fires when create_run failed),
            # orphaning the paused turn. The respond / cancel paths clear
            # the lock when they terminate.
            if final_status != "paused_hitl":
                await clear_active_run(
                    self._redis,
                    prefix=self._key_prefix,
                    conversation_id=conversation_id,
                    run_id=run_id,
                )
                await expire_run_data(
                    self._redis,
                    prefix=self._key_prefix,
                    run_id=run_id,
                    ttl_seconds=self._run_event_ttl_seconds,
                )

    async def _execute_respond_run(
        self,
        *,
        run_id: str,
        conversation_id: str,
        question_id: str,
        answer: Any,
        claim_token: str,
        ctx: RunContext,
    ) -> None:
        """Spawn-wrapper around :meth:`_run_cubepi_respond_path`.

        Mirrors :meth:`_execute_run` for the resume path. Reuses the
        original ``run_id`` (events stream into the same Redis key the SSE
        consumer is still tailing), so this:

        * does NOT call :func:`update_run_meta` on terminal — the respond
          path's CAS-guarded :func:`finalize_run_meta_if_claim_matches`
          already wrote the terminal status. A naive ``update_run_meta``
          here would defeat the CAS;
        * still emits ``DoneEvent`` so the SSE consumer can close cleanly;
        * still clears the active-run pointer + expires run data in
          ``finally`` — exactly like the prompt path. ``claim_resume``
          handles the case where the active pointer is gone but the meta
          row still exists (paused_hitl status).

        The leading setup (citation counter, subagent/citation drainer,
        sandbox + skill catalog resolution, AgentConfig system-prompt
        merge, available-skills suffix, widget guidelines suffix) is
        identical to ``_execute_run``'s — keeping it byte-stable preserves
        the prompt cache prefix across pause/resume.
        """
        from cubebox.api.routes.v1.conversations import _update_conversation_timestamp
        from cubebox.middleware.citations.counter import (
            CitationCounter,
            citation_counter_var,
            citation_event_queue,
        )
        from cubebox.middleware.subagents import subagent_event_queue
        from cubebox.schedules.completion_hook import record_scheduled_run_terminal_state

        sandbox = None
        sandbox_manager = None
        sandbox_create_task: asyncio.Task[Any] | None = None
        stream_task: asyncio.Task[None] | None = None
        catalog_session_ctx: Any | None = None
        catalog_session: Any | None = None
        skill_catalog: Any | None = None
        event_q: asyncio.Queue[tuple[str, Any, Any] | None] = asyncio.Queue()
        cv_token = subagent_event_queue.set(event_q)

        citation_counter = CitationCounter(start=1)
        cc_token = citation_counter_var.set(citation_counter)
        ce_token = citation_event_queue.set(event_q)

        citation_buffers: dict[str | None, str] = {}
        turn_usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }

        async def emit_status(phase: str, detail: str | None = None) -> None:
            data: dict[str, str] = {"phase": phase}
            if detail:
                data["detail"] = detail
            await self._append_event(
                run_id,
                conversation_id,
                StatusEvent(
                    timestamp=datetime.now(UTC).isoformat(),
                    data=data,
                ),
            )

        async def publish_event(event: AgentEvent) -> None:
            await self._append_event(run_id, conversation_id, event)

        async def flush_citation_buffer(
            agent_key: str | None,
            fallback_agent_id: str | None,
        ) -> None:
            buf = citation_buffers.get(agent_key, "")
            if not buf:
                return
            from cubebox.agents.schemas import TextDeltaEvent

            citation_buffers[agent_key] = ""
            await publish_event(
                TextDeltaEvent(
                    timestamp=datetime.now(UTC).isoformat(),
                    data={
                        "content": buf,
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                    agent_id=fallback_agent_id,
                )
            )

        async def publish_stream_event(sse_event: AgentEvent, agent_key: str | None) -> None:
            if sse_event.type == "text_delta":
                buffered = citation_buffers.get(agent_key, "") + str(
                    sse_event.data.get("content", "")
                )
                citation_buffers[agent_key] = ""
                last_open = buffered.rfind("【")
                if last_open != -1 and "】" not in buffered[last_open:]:
                    citation_buffers[agent_key] = buffered[last_open:]
                    buffered = buffered[:last_open]
                if buffered:
                    sse_event.data["content"] = buffered
                    await publish_event(sse_event)
                return

            await flush_citation_buffer(agent_key, sse_event.agent_id)
            if sse_event.type == "usage":
                for key in turn_usage:
                    turn_usage[key] += sse_event.data.get(key, 0)
            await publish_event(sse_event)

        event_q_drainer: asyncio.Task[None] | None = asyncio.create_task(
            _drain_subagent_citation_queue(event_q, publish_stream_event),
            name=f"event_q_drainer_respond:{run_id}",
        )

        try:
            try:
                from pathlib import Path

                from cubebox.config import config as _cfg
                from cubebox.db.engine import async_session_maker
                from cubebox.skills.cache import SkillCache
                from cubebox.skills.service import SkillCatalogService

                catalog_session_ctx = async_session_maker()
                catalog_session = await catalog_session_ctx.__aenter__()
                skill_catalog = SkillCatalogService(
                    session=catalog_session,
                    cache=SkillCache(
                        cache_root=Path(_cfg.get("skills.cache_root", "skills_cache"))
                    ),
                )
            except Exception as exc:
                logger.warning("Skill catalog unavailable for respond run: {}", exc)

            sandbox_factory = getattr(self._app.state, "sandbox_factory", None)
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
                            user_id=ctx.user_id,
                            org_id=ctx.org_id,
                            workspace_id=ctx.workspace_id,
                            workdir=config.get("sandbox.workdir", "/workspace"),
                            catalog=skill_catalog,
                        )
                    except Exception as exc:
                        logger.warning("Sandbox unavailable, continuing without: {}", exc)
                        await emit_status("sandbox_failed", detail=str(exc))

            from cubebox.db.engine import async_session_maker
            from cubebox.llm.factory import LLMFactory

            context_window: int = 0
            try:
                async with async_session_maker() as ctx_session:
                    ctx_factory = LLMFactory(
                        session=ctx_session,
                        org_id=ctx.org_id,
                        encryption_backend=self._app.state.encryption_backend,
                    )
                    (
                        _ctx_provider,
                        _ctx_model_id,
                        _ctx_provider_config,
                    ) = await ctx_factory.resolve_default_provider_and_config()
                    await ctx_session.commit()
                _model_cfg = ctx_factory.get_model_config(_ctx_provider, _ctx_model_id)
                context_window = int(_model_cfg.context_window or 0)
            except Exception as exc:
                logger.debug("Could not resolve context_window for respond DoneEvent: {}", exc)

            from sqlmodel import select as sqlmodel_select

            from cubebox.models.agent_config import AgentConfig
            from cubebox.prompts.system import BASE_SYSTEM_PROMPT

            effective_system_prompt = BASE_SYSTEM_PROMPT
            try:
                if catalog_session is not None:
                    result = await catalog_session.execute(
                        sqlmodel_select(AgentConfig).where(
                            AgentConfig.org_id == ctx.org_id,
                            AgentConfig.workspace_id == ctx.workspace_id,
                        )
                    )
                    agent_cfg = result.scalar_one_or_none()
                else:
                    async with async_session_maker() as _cfg_session:
                        result = await _cfg_session.execute(
                            sqlmodel_select(AgentConfig).where(
                                AgentConfig.org_id == ctx.org_id,
                                AgentConfig.workspace_id == ctx.workspace_id,
                            )
                        )
                        agent_cfg = result.scalar_one_or_none()
                if agent_cfg and agent_cfg.system_prompt:
                    effective_system_prompt = BASE_SYSTEM_PROMPT + "\n\n" + agent_cfg.system_prompt
            except Exception as exc:
                logger.warning("Failed to load AgentConfig (respond), using base prompt: {}", exc)

            try:
                if skill_catalog is not None:
                    _enabled_skills = await skill_catalog.list_enabled_for_workspace(
                        ctx.workspace_id, org_id=ctx.org_id
                    )
                    if _enabled_skills:
                        from cubebox.prompts.skills import SKILLS_PROMPT_TEMPLATE

                        _skills_list = "\n".join(
                            f"- `{s.name}` — {s.description}"
                            for s in sorted(_enabled_skills, key=lambda s: s.name)
                        )
                        effective_system_prompt += "\n\n" + SKILLS_PROMPT_TEMPLATE.format(
                            skills_list=_skills_list
                        )
            except Exception as exc:
                logger.warning("Failed to inject available-skills list (respond): {}", exc)

            from cubebox.prompts.widget import WIDGET_GUIDELINES

            effective_system_prompt += "\n\n" + WIDGET_GUIDELINES

            await self._run_cubepi_respond_path(
                ctx=ctx,
                run_id=run_id,
                conversation_id=conversation_id,
                question_id=question_id,
                answer=answer,
                claim_token=claim_token,
                effective_system_prompt=effective_system_prompt,
                publish_stream_event=publish_stream_event,
                flush_citation_buffer=flush_citation_buffer,
                citation_buffers=citation_buffers,
                sandbox=sandbox,
                skill_catalog=skill_catalog,
                catalog_session=catalog_session,
            )
            await _update_conversation_timestamp(
                conversation_id,
                org_id=ctx.org_id,
                workspace_id=ctx.workspace_id,
                user_id=ctx.user_id,
            )

            # Drain shared subagent/citation queue BEFORE DoneEvent — same
            # rationale as _execute_run.
            if event_q_drainer is not None:
                with suppress(Exception):
                    event_q.put_nowait(None)
                try:
                    await asyncio.wait_for(event_q_drainer, timeout=5.0)
                except (TimeoutError, Exception):
                    if not event_q_drainer.done():
                        event_q_drainer.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await event_q_drainer
                event_q_drainer = None

            from cubebox.services.usage import SessionUsage, get_session_usage

            session_usage: SessionUsage = {
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_cache_read_tokens": 0,
                "total_cache_write_tokens": 0,
            }
            try:
                from cubebox.db.engine import async_session_maker

                async with async_session_maker() as billing_session:
                    session_usage = await get_session_usage(billing_session, conversation_id)
            except Exception:
                logger.warning("Failed to query session usage for respond done event")

            # Read the actual terminal status BEFORE emitting DoneEvent so
            # we can stamp data.paused=true on chained-HITL flows (respond
            # answers one question and the agent immediately emits a new
            # pending one). Without this flag, the frontend treats `done`
            # as completed and wipes pendingAsk / pendingConfirmMap, so the
            # follow-up HITL card disappears until a reload. See spec §6.
            #
            # _run_cubepi_respond_path's CAS-guarded finalize already wrote
            # the terminal status; read it back rather than re-compute.
            final_meta = await get_run_meta(
                self._redis,
                prefix=self._key_prefix,
                run_id=run_id,
            )
            final_status = final_meta.status if final_meta is not None else "completed"

            done_data: dict[str, Any] = {
                "usage": {
                    "turn": dict(turn_usage),
                    "session": session_usage,
                    "context_window": context_window,
                }
            }
            if final_status == "paused_hitl":
                done_data["paused"] = True

            await self._append_event(
                run_id,
                conversation_id,
                DoneEvent(
                    timestamp=datetime.now(UTC).isoformat(),
                    data=done_data,
                ),
            )
            # NOTE: no update_run_meta here — _run_cubepi_respond_path
            # already wrote the terminal status via
            # finalize_run_meta_if_claim_matches (CAS-guarded). A naive
            # update_run_meta would race with whatever flow stole the slot
            # while we were running.
            await record_scheduled_run_terminal_state(run_id=run_id, run_status=final_status)
            await self._maybe_consolidate_memory(conversation_id=conversation_id, ctx=ctx)
        except asyncio.CancelledError:
            # Mirror prompt-path cancel handling. We bypass the CAS guard
            # on cancel because cancel is itself the takeover signal — the
            # cancel route already set the meta state appropriately.
            await update_run_meta(
                self._redis,
                prefix=self._key_prefix,
                run_id=run_id,
                status="cancelled",
            )
            await record_scheduled_run_terminal_state(run_id=run_id, run_status="cancelled")
            with suppress(Exception):
                await _repair_dangling_tool_calls(conversation_id)
            with suppress(Exception):
                await self._append_error(run_id, conversation_id, "Run cancelled", "Run cancelled")
            raise
        except Exception as exc:
            logger.error("Respond run {} failed: {}", run_id, exc, exc_info=True)
            # Don't clear DB pending — leaving it allows the user to retry
            # the answer. Don't finalize meta here either: if the body
            # finally block already ran, it CAS-wrote whatever status
            # applies; if we crashed before that, the stale-run sweeper
            # picks the row up. Just record the error so the SSE consumer
            # sees it.
            with suppress(Exception):
                await self._append_error(
                    run_id,
                    conversation_id,
                    "An unexpected error occurred during respond execution",
                    str(exc),
                )
        finally:
            if stream_task is not None and not stream_task.done():
                stream_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stream_task

            if event_q_drainer is not None:
                with suppress(Exception):
                    event_q.put_nowait(None)
                try:
                    await asyncio.wait_for(event_q_drainer, timeout=5.0)
                except (TimeoutError, Exception):
                    if not event_q_drainer.done():
                        event_q_drainer.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await event_q_drainer

            if sandbox_create_task is not None and not sandbox_create_task.done():
                sandbox_create_task.cancel()
                with suppress(asyncio.CancelledError):
                    await sandbox_create_task

            try:
                subagent_event_queue.reset(cv_token)
            except ValueError:
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
                    with suppress(Exception):
                        await sandbox._manager.release(
                            sandbox.id,
                            org_id=ctx.org_id,
                            workspace_id=ctx.workspace_id,
                        )
                elif sandbox_manager and not isinstance(sandbox, LazySandbox):
                    with suppress(Exception):
                        await sandbox_manager.release(
                            sandbox.id,
                            org_id=ctx.org_id,
                            workspace_id=ctx.workspace_id,
                        )

            if catalog_session_ctx is not None:
                with suppress(Exception):
                    await catalog_session_ctx.__aexit__(None, None, None)

            self._agents.pop(run_id, None)
            # Clear the active-run pointer — claim_resume handles the case
            # where pointer is gone but meta is paused_hitl (it re-stamps
            # the pointer atomically). On "completed" the pointer must be
            # gone so the next start_run can allocate a fresh run.
            await clear_active_run(
                self._redis,
                prefix=self._key_prefix,
                conversation_id=conversation_id,
                run_id=run_id,
            )
            await expire_run_data(
                self._redis,
                prefix=self._key_prefix,
                run_id=run_id,
                ttl_seconds=self._run_event_ttl_seconds,
            )
