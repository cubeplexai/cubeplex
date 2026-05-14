"""Background run orchestration decoupled from HTTP connections."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

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
    update_run_meta,
)
from cubebox.utils.time import utc_isoformat


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


def cubepi_dict_to_agent_event(d: dict[str, Any], timestamp: str) -> AgentEvent | None:
    """Translate a single SSE dict produced by ``convert_agent_event_to_sse``
    into a typed cubebox ``AgentEvent``.

    Returns ``None`` for dicts that should be silently dropped at this layer
    (tool_call_delta — frontend only consumes complete tool_call; done — the
    caller emits done with usage data; unknown types).
    """
    from cubebox.agents.schemas import (
        ErrorEvent,
        ReasoningEvent,
        TextDeltaEvent,
        ToolCallEvent,
        ToolResultEvent,
        UsageEvent,
    )

    t = d.get("type")
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
        return ToolResultEvent(
            timestamp=timestamp,
            data={
                "tool_call_id": d.get("tool_call_id", ""),
                "name": d.get("name", ""),
                "content": str(d.get("result", "")),
                "is_error": d.get("is_error", False),
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
    ) -> None:
        """Execute a single user turn through the cubepi runtime.

        Builds a cubepi.Provider + cubepi.Agent, subscribes an event listener, then
        awaits agent.prompt(). Each AgentEvent is translated into a cubebox AgentEvent
        schema object and forwarded to ``publish_stream_event`` so the rest of
        _execute_run (DoneEvent, update_run_meta, etc.) sees the same turn_usage and
        citation buffers as the LangGraph path.

        Tools wired (M2.5):
          - no-DI builtin tools (calculator, datetime)
          - view_images (per-request DI: org_id, workspace_id, objectstore, capabilities)
          - memory CRUD tools (service factory per-request)
          - load_skill (catalog + workspace/org)
          - MCP tools (workspace-enabled HTTP MCP servers)
        """
        from collections.abc import AsyncIterator as _AsyncIterator
        from contextlib import asynccontextmanager as _asynccontextmanager

        from cubebox.agents.checkpointer import init_checkpointer
        from cubebox.agents.graph import create_cubebox_agent
        from cubebox.agents.stream import convert_agent_event_to_sse
        from cubebox.db.engine import async_session_maker
        from cubebox.llm.cache_markers_pi import CubeboxCacheMarkerPolicy
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

        # Resolve model config to extract max_tokens + temperature for byte-parity
        # with the langgraph path which reads these from ModelConfig.
        try:
            _model_config = factory.get_model_config(provider_name, model_id)
            _model_max_tokens: int = _model_config.max_tokens or 32000
            _model_temperature: float = 0.7  # langgraph default; ModelConfig has no temperature
        except Exception:
            _model_max_tokens = 32000
            _model_temperature = 0.7

        provider = factory.build_cubepi_provider(
            provider_config, cache_policy=CubeboxCacheMarkerPolicy()
        )

        # --- Compose tool list (M2.5 / M5.4 byte-parity) ---
        # Tools are accumulated in separate buckets and merged in the exact
        # same order as langgraph's create_cubebox_agent to achieve byte-parity:
        #   sandbox(execute/write_file/edit_file/file_read)
        #   → save_artifact
        #   → write_todos
        #   → subagent
        #   → calculator/datetime
        #   → view_images
        #   → memory_*
        #   → load_skill
        #   → mcp_tools
        #
        # Middleware that contributes tools writes to _sandbox_tools,
        # _artifact_tools, _todo_tools, _subagent_tools rather than all_tools.
        # All other tools accumulate in _builtin_tools.  At the end we merge
        # them in the correct order.

        from cubebox.tools.registry_pi import list_builtin_tools_for_cubepi

        _sandbox_tools: list[Any] = []
        _artifact_tools: list[Any] = []
        _todo_tools: list[Any] = []
        _subagent_tools: list[Any] = []
        _builtin_tools: list[Any] = list(list_builtin_tools_for_cubepi())

        # Memory tools — service factory opened per tool call
        # Placed before view_images and load_skill to match langgraph tool order:
        # calculator → datetime → memory_save → memory_search → memory_update
        # → load_skill → view_images → mcp_tools
        try:
            from cubebox.db.engine import async_session_maker as _mem_session_maker
            from cubebox.repositories.memory import MemoryRepository as _MemoryRepository
            from cubebox.services.memory import MemoryService as _MemoryService
            from cubebox.tools.builtin.memory_pi import create_memory_tools_pi

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
                create_memory_tools_pi(
                    service_factory=_memory_service_factory,
                    conversation_id=conversation_id,
                    run_id=run_id,
                )
            )
        except Exception as _exc:
            logger.warning("memory_pi tools unavailable for cubepi run: {}", _exc)

        # load_skill — requires a non-None catalog (may be absent if DB is down)
        if skill_catalog is not None:
            try:
                from cubebox.tools.builtin.load_skill_pi import create_load_skill_tool_pi

                _builtin_tools.append(
                    create_load_skill_tool_pi(
                        catalog=skill_catalog,
                        workspace_id=ctx.workspace_id,
                        org_id=ctx.org_id,
                    )
                )
            except Exception as _exc:
                logger.warning("load_skill_pi unavailable for cubepi run: {}", _exc)

        # view_images — per-request DI: objectstore + LLM capabilities
        # Must come after memory tools and load_skill to match langgraph tool order.
        try:
            from cubebox.llm.capabilities import LLMCapabilities
            from cubebox.objectstore import get_objectstore_client
            from cubebox.tools.builtin.view_images_pi import make_view_images_tool

            _builtin_tools.append(
                make_view_images_tool(
                    org_id=ctx.org_id,
                    workspace_id=ctx.workspace_id,
                    objectstore=get_objectstore_client(),
                    capabilities=LLMCapabilities(factory.llm_config),
                )
            )
        except Exception as _exc:
            logger.warning("view_images_pi unavailable for cubepi run: {}", _exc)

        # MCP tools — per-workspace enabled HTTP MCP servers
        try:
            from cubebox.credentials.dependencies import build_credential_service
            from cubebox.mcp.runtime_pi import load_workspace_mcp_tools_for_cubepi

            async with async_session_maker() as mcp_session:
                cred_service = build_credential_service(
                    mcp_session,
                    self._app.state.encryption_backend,
                    org_id=ctx.org_id,
                    actor_user_id=ctx.user_id,
                )
                mcp_tools = await load_workspace_mcp_tools_for_cubepi(
                    session=mcp_session,
                    workspace_id=ctx.workspace_id,
                    org_id=ctx.org_id,
                    user_id=ctx.user_id,
                    cred_service=cred_service,
                    signer=self._app.state.mcp_user_token_signer,
                )
                _builtin_tools.extend(mcp_tools)
        except Exception as _exc:
            logger.warning("MCP tools unavailable for cubepi run: {}", _exc)

        # Collect SSE dicts from the cubepi listener before converting to typed events.
        # agent.prompt() is an async method that drives the agent loop and calls
        # synchronous listeners on each AgentEvent as they arrive.  We buffer the
        # translated dicts here and flush after prompt() returns.
        sse_dicts: list[dict[str, Any]] = []

        # --- Build the 11 cubepi middleware (M3.f) ---
        # extra_ref late-binding: compaction, skills, and todo all need access to
        # agent._extra, which is only available after the agent is constructed.
        # We capture it via a holder dict so the closures resolve to the right
        # object once we populate the holder below.
        extra_ref_holder: dict[str, Any] = {"extra": None}

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
                    citation_configs={},
                    event_queue=citation_event_queue.get(None),
                )
            )
        except Exception as _exc:
            logger.warning("CitationMiddleware unavailable: {}", _exc)

        # 4. MemoryMiddleware — needs repo_factory
        try:
            from collections.abc import AsyncIterator as _AsyncIterator2
            from contextlib import asynccontextmanager as _asynccontextmanager2

            from cubebox.db.engine import async_session_maker as _mem2_session_maker
            from cubebox.middleware.memory import MemoryMiddleware
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

            cubepi_middleware.append(MemoryMiddleware(repo_factory=_mem_repo_factory))
        except Exception as _exc:
            logger.warning("MemoryMiddleware unavailable: {}", _exc)

        # 5. CompactionMiddleware — needs extra_ref + summary_llm + config
        try:
            from cubebox.config import config as _comp_cfg
            from cubebox.llm.factory import LLMFactory as _CompLLMFactory
            from cubebox.middleware.compaction import CompactionMiddleware

            if _comp_cfg.get("compaction.enabled", False):
                _summary_provider = _comp_cfg.get("compaction.summary_provider")
                _summary_model_id = _comp_cfg.get("compaction.summary_model")
                _comp_factory = _CompLLMFactory()
                _summary_llm = _comp_factory.create(
                    provider_name=_summary_provider,
                    model_id=_summary_model_id,
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

        # 6. SandboxMiddleware — needs sandbox
        if sandbox is not None:
            try:
                from cubebox.middleware.sandbox import SandboxMiddleware

                sandbox_mw = SandboxMiddleware(
                    sandbox=sandbox,
                    conversation_id=conversation_id,
                    workspace_id=ctx.workspace_id,
                )
                cubepi_middleware.append(sandbox_mw)
                # Middleware tools (execute, write_file, edit_file, file_read) collected for
                # ordered merge below
                _sandbox_tools.extend(sandbox_mw.tools)
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
                # as shared tools for subagent spawning.
                shared_tools=_sandbox_tools + _artifact_tools + _builtin_tools,
                inherited_middleware=_cost_mw_for_inherit,
            )
            cubepi_middleware.append(subagent_mw)
            _subagent_tools.extend(subagent_mw.tools)
        except Exception as _exc:
            logger.warning("SubAgentMiddleware unavailable: {}", _exc)

        # 9. CostMiddleware — needs org/workspace/user/conversation IDs
        try:
            from cubebox.middleware.cost import CostMiddleware

            cubepi_middleware.append(
                CostMiddleware(
                    org_id=ctx.org_id,
                    workspace_id=ctx.workspace_id,
                    user_id=ctx.user_id,
                    conversation_id=conversation_id,
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

        # --- Final tool merge (M5.4 byte-parity) ---
        # Compose in the same order as langgraph's create_cubebox_agent:
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

        async with init_checkpointer() as cp:
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
            )

            # Late-bind extra_ref to the live agent._extra dict so compaction /
            # skills / todo middleware can read and write persistent state.
            extra_ref_holder["extra"] = agent._extra

            def _on_event(evt: Any, _signal: Any = None) -> None:
                translated = convert_agent_event_to_sse(evt)
                sse_dicts.extend(translated)

            agent.subscribe(_on_event)

            # Compute relevance-memory snapshot before the agent loop starts
            # and bake it into the UserMessage metadata so MemoryMiddleware
            # can prepend the rendered snapshot text during transform_context.
            # This is the cubepi equivalent of the LangGraph path's snapshot
            # channel injection (M3.b.1 / cache discipline).
            import time as _time

            from cubepi.providers.base import TextContent as _TextContent
            from cubepi.providers.base import UserMessage as _UserMessage

            from cubebox.middleware.memory import compute_relevance_snapshot as _compute_snap

            _user_msg_metadata: dict[str, Any] = {}
            try:
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
            await agent.prompt(_user_msg)

        # Translate buffered SSE dicts → typed AgentEvent objects and push through
        # publish_stream_event, which handles citation buffering and turn_usage.
        ts = datetime.now(UTC).isoformat()
        for d in sse_dicts:
            sse_event = cubepi_dict_to_agent_event(d, ts)
            if sse_event is None:
                continue
            await publish_stream_event(sse_event, None)

        for agent_key in list(citation_buffers):
            await flush_citation_buffer(agent_key, agent_key)

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

            await self._run_cubepi_path(
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
            )
            await _update_conversation_timestamp(
                conversation_id,
                org_id=ctx.org_id,
                workspace_id=ctx.workspace_id,
                user_id=ctx.user_id,
            )
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

            await self._append_event(
                run_id,
                conversation_id,
                DoneEvent(
                    timestamp=datetime.now(UTC).isoformat(),
                    data={
                        "usage": {
                            "turn": dict(turn_usage),
                            "session": session_usage,
                            "context_window": context_window,
                        }
                    },
                ),
            )
            # Mark the run completed AFTER appending DoneEvent so the SSE consumer
            # cannot observe active_run=None with no more events (which would cause
            # it to exit before the DoneEvent is in the Redis stream).
            await update_run_meta(
                self._redis,
                prefix=self._key_prefix,
                run_id=run_id,
                status="completed",
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
