"""Out-of-band memory reflection — runs after AgentEndEvent.

Spawns a detached cubepi Agent (cheap model, memory tools only) seeded with
the last conversation turn plus the current memory snapshot. Captures any
memory_save / memory_update tool executions and publishes a UserEvent so
the frontend can surface the change.

Failure semantics: fire-and-forget. Timeout, LLM errors, and memory write
errors are logged and swallowed; never propagate to the main conversation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cubepi import Agent
from cubepi.agent.types import AgentEvent

from cubebox.models.user_event import UserEventType
from cubebox.services.reflection_context import set_reflection_source
from cubebox.services.user_event import PublishUserEventInput, UserEventService

logger = logging.getLogger(__name__)


@dataclass
class ReflectionTurn:
    user_message: str
    assistant_message: str
    tool_summaries: list[dict[str, str]] = field(default_factory=list)
    # each: {"name": "...", "args_summary": "...", "outcome": "ok"|"error"}


@dataclass
class ReflectionInput:
    conversation_id: str
    run_id: str
    user_id: str
    workspace_id: str | None
    turn: ReflectionTurn


# Agent factory signature: given a ReflectionInput, build & return an Agent
# whose tools include memory_save/memory_update/memory_search bound to the
# user's MemoryService. Concrete factory wired in run_manager / DI setup.
AgentFactory = Callable[[ReflectionInput], "Agent[Any]"]


class ReflectionRunner:
    def __init__(
        self,
        *,
        user_event_service: UserEventService,
        agent_factory: AgentFactory,
        timeout_sec: float = 30.0,
    ) -> None:
        self._svc = user_event_service
        self._make_agent = agent_factory
        self._timeout = timeout_sec
        self._seen_runs: set[str] = set()  # idempotency

    async def reflect(self, inp: ReflectionInput) -> None:
        if inp.run_id in self._seen_runs:
            logger.debug("reflection already completed for run_id=%s, skipping", inp.run_id)
            return
        try:
            await asyncio.wait_for(self._reflect_impl(inp), timeout=self._timeout)
        except TimeoutError:
            logger.warning(
                "reflection timed out for run_id=%s conversation_id=%s",
                inp.run_id,
                inp.conversation_id,
            )
            return
        except Exception:
            logger.exception(
                "reflection failed for run_id=%s conversation_id=%s",
                inp.run_id,
                inp.conversation_id,
            )
            return
        self._seen_runs.add(inp.run_id)

    async def _reflect_impl(self, inp: ReflectionInput) -> None:
        agent = self._make_agent(inp)
        seed = self._build_seed_prompt(inp.turn)

        items: list[dict[str, Any]] = []

        # cubepi calls listeners with (event, signal) — accept both positionally.
        def listener(event: AgentEvent, signal: Any = None) -> None:
            if event.type != "tool_execution_end":
                return
            name = getattr(event, "tool_name", None)
            if name not in ("memory_save", "memory_update"):
                return
            payload = self._extract_memory_result(event)
            if payload is not None:
                items.append(
                    {
                        "op": "save" if name == "memory_save" else "update",
                        **payload,
                    }
                )

        unsub = agent.subscribe(listener)
        try:
            # Keep the ContextVar active across wait_for_idle: cubepi can
            # execute memory tool calls after prompt() returns (they finish
            # during the idle drain), and tool callbacks must see
            # reflection_source_active() == True to tag writes correctly.
            with set_reflection_source():
                await agent.prompt(seed)
                await agent.wait_for_idle()
        finally:
            unsub()

        if not items:
            return

        await self._svc.publish(
            PublishUserEventInput(
                user_id=inp.user_id,
                workspace_id=inp.workspace_id,
                type=UserEventType.MEMORY_UPDATED,
                payload={
                    "conversation_id": inp.conversation_id,
                    "run_id": inp.run_id,
                    "items": items,
                },
            )
        )

    def _build_seed_prompt(self, turn: ReflectionTurn) -> str:
        # Pack the last turn into a single user-message string. The reflection
        # system prompt frames the task; this just gives it the material.
        tools_block = ""
        if turn.tool_summaries:
            tools_block = "\n\nTools called in this turn:\n" + "\n".join(
                f"- {t['name']}({t.get('args_summary', '')}) -> {t.get('outcome', 'ok')}"
                for t in turn.tool_summaries
            )
        return (
            "Last turn for review:\n\n"
            f"USER: {turn.user_message}\n\n"
            f"ASSISTANT: {turn.assistant_message}"
            f"{tools_block}"
        )

    def _extract_memory_result(self, event: AgentEvent) -> dict[str, Any] | None:
        # tool_execution_end carries the AgentToolResult; memory_save returns
        # {"status": "saved", "memory_id": "..."} and memory_update returns
        # {"status": "updated", "memory_id": "..."} as JSON text content.
        try:
            result = getattr(event, "result", None)
            if result is None or not result.content:
                return None
            text = result.content[0].text
            obj = json.loads(text)
        except Exception:
            return None
        if obj.get("status") not in ("saved", "updated"):
            return None
        memory_id = obj.get("memory_id")
        if not memory_id:
            return None
        return {"memory_id": memory_id}
