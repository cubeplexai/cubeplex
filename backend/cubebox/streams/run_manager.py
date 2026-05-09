"""Background run orchestration decoupled from HTTP connections."""

from __future__ import annotations

import asyncio
import re
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from langchain_core.messages import HumanMessage
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
    update_run_meta,
)
from cubebox.utils.time import utc_isoformat

_CITATION_ID_PATTERN = re.compile(r"【(\d+)-\d+】")


@dataclass(slots=True)
class RunContext:
    """Scoped context required to execute a run."""

    user_id: str
    org_id: str
    workspace_id: str


def _ns_to_agent_id(ns: tuple[Any, ...]) -> str | None:
    if not ns:
        return None
    return ":".join(str(part) for part in ns)


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
    return events


def _extract_citation_ids(content: Any) -> list[int]:
    if isinstance(content, str):
        return [int(match.group(1)) for match in _CITATION_ID_PATTERN.finditer(content)]
    if isinstance(content, list):
        list_ids: list[int] = []
        for item in content:
            list_ids.extend(_extract_citation_ids(item))
        return list_ids
    if isinstance(content, dict):
        dict_ids: list[int] = []
        for value in content.values():
            dict_ids.extend(_extract_citation_ids(value))
        return dict_ids
    return []


async def _recover_next_citation_id(agent: Any, conversation_id: str) -> int:
    aget_state = getattr(agent, "aget_state", None)
    if aget_state is None:
        return 1

    try:
        from langchain_core.runnables import RunnableConfig

        state = await aget_state(RunnableConfig(configurable={"thread_id": conversation_id}))
    except Exception as exc:
        logger.debug("Could not recover citation counter: {}", exc)
        return 1

    if not state or not getattr(state, "values", None):
        return 1

    max_citation_id = 0
    for message in state.values.get("messages", []):
        for citation_id in _extract_citation_ids(getattr(message, "content", "")):
            if citation_id > max_citation_id:
                max_citation_id = citation_id

    return max_citation_id + 1 if max_citation_id > 0 else 1


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
    ) -> str:
        """Create and start a new background run."""
        run_id = str(uuid7())
        started_at = utc_isoformat(datetime.now(UTC))
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
            if existing and existing.status == "running":
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

    async def drain(self, timeout_seconds: float) -> None:
        """Wait for in-flight runs to finish, then return.

        On timeout, cancels residual tasks via ``cancel_all`` (which lets
        the per-task cancel path mark status=cancelled and write an
        ``error`` event before the lock is released).

        Logs a status line on entry when there's anything to wait for, plus
        a progress line every 30 seconds while waiting.
        """
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

        checkpointer = None
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

        tool_delta_context: dict[tuple[str | None, int], dict[str, Any]] = {}
        citation_buffers: dict[str | None, str] = {}

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
            await publish_event(sse_event)

        try:
            factory = getattr(self._app.state, "checkpointer_factory", None)
            if factory:
                checkpointer = factory()
            else:
                from cubebox.agents.checkpointer import create_checkpointer

                checkpointer = await create_checkpointer()

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

            from cubebox.agents.graph import create_cubebox_agent
            from cubebox.db.engine import async_session_maker
            from cubebox.llm.factory import LLMFactory
            from cubebox.middleware.citations import CitationConfig, load_citation_configs
            from cubebox.tools import get_registry

            try:
                async with async_session_maker() as llm_session:
                    llm = await LLMFactory(
                        session=llm_session,
                        org_id=ctx.org_id,
                        encryption_backend=self._app.state.encryption_backend,
                    ).create_default()
                    await llm_session.commit()
            except Exception:
                logger.warning("LLMFactory DB load failed, falling back to config-only")
                llm = await LLMFactory().create_default()
            tools = get_registry().list_tools()
            try:
                from cubebox.credentials.dependencies import build_credential_service
                from cubebox.db.engine import async_session_maker
                from cubebox.mcp.runtime import load_mcp_tools_for_workspace

                async with async_session_maker() as mcp_session:
                    cred_service = build_credential_service(
                        mcp_session,
                        self._app.state.encryption_backend,
                        org_id=ctx.org_id,
                        actor_user_id=ctx.user_id,
                    )
                    tools.extend(
                        await load_mcp_tools_for_workspace(
                            org_id=ctx.org_id,
                            workspace_id=ctx.workspace_id,
                            user_id=ctx.user_id,
                            cred_service=cred_service,
                            signer=self._app.state.mcp_user_token_signer,
                            session=mcp_session,
                        )
                    )
            except Exception as exc:
                logger.warning("DB MCP tools unavailable for run: {}", exc)

            all_citation_configs: dict[str, CitationConfig] = {}
            try:
                from cubebox.config import config as app_config

                mcp_servers = app_config.get("mcp.servers", {})
                for _server_name, server_cfg in (mcp_servers or {}).items():
                    tool_defs = server_cfg.get("tools", [])
                    if tool_defs:
                        all_citation_configs.update(load_citation_configs(tool_defs))
            except Exception as exc:
                logger.debug("Failed to load citation configs: {}", exc)

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

            from collections.abc import AsyncIterator as _AsyncIterator
            from contextlib import asynccontextmanager as _asynccontextmanager

            from cubebox.db.engine import async_session_maker as _memory_session_maker
            from cubebox.repositories.memory import MemoryRepository as _MemoryRepository
            from cubebox.services.memory import MemoryService as _MemoryService

            @_asynccontextmanager
            async def _memory_repo_factory() -> _AsyncIterator[_MemoryRepository]:
                async with _memory_session_maker() as _session:
                    yield _MemoryRepository(
                        _session,
                        user_id=ctx.user_id,
                        org_id=ctx.org_id,
                        workspace_id=ctx.workspace_id,
                    )

            @_asynccontextmanager
            async def _memory_service_factory() -> _AsyncIterator[_MemoryService]:
                async with _memory_session_maker() as _session:
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

            agent = create_cubebox_agent(
                llm=llm,
                tools=tools,
                system_prompt=effective_system_prompt,
                sandbox=sandbox,
                conversation_id=conversation_id,
                org_id=ctx.org_id,
                workspace_id=ctx.workspace_id,
                catalog_session=catalog_session,
                user_id=ctx.user_id,
                checkpointer=checkpointer,
                citation_configs=all_citation_configs,
                event_queue=event_q,
                memory_repo_factory=_memory_repo_factory,
                memory_service_factory=_memory_service_factory,
            )
            config_dict = {"configurable": {"thread_id": conversation_id}}
            citation_counter._next = await _recover_next_citation_id(agent, conversation_id)

            async def drain_main_stream() -> None:
                try:
                    # M7: hydrate attachments into sandbox + build mixed content
                    attachment_blocks: list[dict[str, Any]] = []
                    if attachments:
                        if sandbox is not None:
                            from opensandbox.exceptions.sandbox import (
                                SandboxReadyTimeoutException,
                            )

                            from cubebox.agents.hydrator import (
                                AttachmentHydrationError,
                                AttachmentHydrator,
                            )
                            from cubebox.db.engine import async_session_maker
                            from cubebox.objectstore import get_objectstore_client
                            from cubebox.repositories import AttachmentRepository

                            try:
                                async with async_session_maker() as h_session:
                                    h_repo = AttachmentRepository(
                                        h_session,
                                        org_id=ctx.org_id,
                                        workspace_id=ctx.workspace_id,
                                    )
                                    hydrator = AttachmentHydrator(
                                        repo=h_repo,
                                        sandbox=sandbox,
                                        objectstore=get_objectstore_client(),
                                    )
                                    await hydrator.hydrate(
                                        conversation_id=conversation_id,
                                        file_ids=attachments,
                                    )
                            except (AttachmentHydrationError, SandboxReadyTimeoutException) as exc:
                                # Hydration failure is non-fatal: the run continues
                                # without files staged in the sandbox. The LLM still
                                # receives the attachment hint text; the sandbox_path
                                # references simply won't resolve to real files.
                                logger.warning(
                                    "Attachment hydration failed (run continues): {}", exc
                                )
                                await emit_status("hydration_failed", detail=str(exc))

                        attachment_blocks = await _build_attachment_content_blocks(
                            org_id=ctx.org_id,
                            workspace_id=ctx.workspace_id,
                            conversation_id=conversation_id,
                            attachment_ids=attachments,
                        )

                    # Persist the user-typed text only. AttachmentHintMiddleware
                    # appends the [Attachments] hint at model-call time so the LLM
                    # sees sandbox paths, while the checkpoint stays equal to
                    # what the user wrote.
                    human_msg = HumanMessage(
                        content=content,
                        additional_kwargs=(
                            {"attachments_meta": attachment_blocks} if attachment_blocks else {}
                        ),
                        response_metadata={"created_at": datetime.now(UTC).isoformat()},
                    )

                    if attachments:
                        from cubebox.db.engine import async_session_maker
                        from cubebox.repositories import AttachmentRepository

                        async with async_session_maker() as att_session:
                            mark_repo = AttachmentRepository(
                                att_session,
                                org_id=ctx.org_id,
                                workspace_id=ctx.workspace_id,
                            )
                            await mark_repo.mark_attached_bulk(
                                conversation_id=conversation_id,
                                attachment_ids=attachments,
                            )

                    async for event in agent.astream(  # type: ignore[call-overload]
                        {"messages": [human_msg]},
                        stream_mode=["messages", "updates"],
                        stream_subgraphs=True,
                        config=config_dict,
                    ):
                        ns: tuple[Any, ...] = ()
                        payload = event
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
                    await event_q.put(None)

            from cubebox.agents.stream import convert_messages_chunk, convert_updates_chunk

            stream_task = asyncio.create_task(drain_main_stream())

            while True:
                item = await event_q.get()
                if item is None:
                    break

                kind = item[0]
                if kind == "main":
                    ns, payload = item[1], item[2]
                    agent_id = _ns_to_agent_id(ns)
                    if isinstance(payload, tuple) and len(payload) == 2:
                        mode, data = payload
                        if mode == "messages":
                            evts = convert_messages_chunk(data, agent_id=agent_id)
                        elif mode == "updates":
                            evts = convert_updates_chunk(data, agent_id=agent_id)
                        else:
                            evts = []
                        for sse_event in _dicts_to_sse_events(evts, tool_delta_context):
                            await publish_stream_event(sse_event, agent_id)
                elif kind == "subagent":
                    sa_agent_id, payload = item[1], item[2]
                    if isinstance(payload, tuple) and len(payload) == 2:
                        mode, data = payload
                        if mode == "messages":
                            evts = convert_messages_chunk(data, agent_id=sa_agent_id)
                        elif mode == "updates":
                            evts = convert_updates_chunk(data, agent_id=sa_agent_id)
                        else:
                            evts = []
                        for sse_event in _dicts_to_sse_events(evts, tool_delta_context):
                            await publish_stream_event(sse_event, sa_agent_id)
                elif kind == "citation":
                    from cubebox.agents.schemas import CitationEvent

                    citation_event = CitationEvent(
                        timestamp=datetime.now(UTC).isoformat(),
                        data=item[2],
                        agent_id=item[1],
                    )
                    await publish_event(citation_event)
                elif kind == "error":
                    raise item[2]

            for agent_key in list(citation_buffers):
                await flush_citation_buffer(agent_key, agent_key)

            if stream_task is not None:
                await stream_task

            await _update_conversation_timestamp(
                conversation_id,
                org_id=ctx.org_id,
                workspace_id=ctx.workspace_id,
                user_id=ctx.user_id,
            )
            await update_run_meta(
                self._redis,
                prefix=self._key_prefix,
                run_id=run_id,
                status="completed",
            )
            await self._append_event(
                run_id,
                conversation_id,
                DoneEvent(timestamp=datetime.now(UTC).isoformat()),
            )
        except asyncio.CancelledError:
            await update_run_meta(
                self._redis,
                prefix=self._key_prefix,
                run_id=run_id,
                status="cancelled",
            )
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
