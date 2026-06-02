"""Unit tests for ReflectionRunner.

Uses lightweight mock agents to exercise the runner logic without running
a real cubepi event loop or hitting the database.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock

import pytest
from cubepi.agent.types import AgentToolResult, ToolExecutionEndEvent
from cubepi.providers.base import TextContent

from cubebox.models.user_event import UserEventType
from cubebox.services.reflection_runner import (
    AgentFactory,
    ReflectionInput,
    ReflectionRunner,
    ReflectionTurn,
)

# ---------------------------------------------------------------------------
# Mock agent helpers
# ---------------------------------------------------------------------------


class _MockAgent:
    """Minimal Agent-like object for unit tests.

    The test fixture configures which ToolExecutionEndEvent sequences
    prompt() should deliver.
    """

    def __init__(self, events_to_emit: list[ToolExecutionEndEvent]) -> None:
        self._events = events_to_emit
        self._listener: Callable | None = None

    def subscribe(self, listener: Callable) -> Callable[[], None]:
        self._listener = listener
        return lambda: setattr(self, "_listener", None)

    async def prompt(self, text: str) -> None:
        if self._listener is not None:
            for ev in self._events:
                # cubepi calls listener(event, signal) — pass None for signal
                self._listener(ev, None)

    async def wait_for_idle(self) -> None:
        return


class _HangingAgent:
    """Agent whose prompt() never returns (simulates timeout)."""

    def subscribe(self, listener: Callable) -> Callable[[], None]:
        return lambda: None

    async def prompt(self, text: str) -> None:  # noqa: RUF029
        await asyncio.sleep(10)  # effectively infinite in test context

    async def wait_for_idle(self) -> None:
        return


def _tool_event(tool_name: str, memory_id: str) -> ToolExecutionEndEvent:
    """Build a ToolExecutionEndEvent that looks like a successful memory write."""
    if tool_name == "memory_save":
        payload = {"status": "saved", "memory_id": memory_id}
    else:
        payload = {"status": "updated", "memory_id": memory_id}
    result = AgentToolResult(content=[TextContent(text=__import__("json").dumps(payload))])
    return ToolExecutionEndEvent(
        tool_call_id="tc_1",
        tool_name=tool_name,
        result=result,
    )


def _mk_input(run_id: str = "run_y") -> ReflectionInput:
    return ReflectionInput(
        conversation_id="conv_x",
        run_id=run_id,
        user_id="usr_z",
        workspace_id=None,
        turn=ReflectionTurn(
            user_message="我喜欢简洁的回答",
            assistant_message="收到。",
            tool_summaries=[],
        ),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def user_event_service_mock() -> MagicMock:
    svc = MagicMock()
    svc.publish = AsyncMock()
    return svc


@pytest.fixture
def agent_factory_mock() -> AgentFactory:
    """Factory returning an agent that fires one memory_save event."""
    events = [_tool_event("memory_save", "mem_abc")]

    def factory(inp: ReflectionInput) -> _MockAgent:  # type: ignore[return]
        return _MockAgent(events)

    return factory  # type: ignore[return-value]


@pytest.fixture
def agent_factory_silent() -> AgentFactory:
    """Factory returning an agent that fires no relevant events."""

    def factory(inp: ReflectionInput) -> _MockAgent:  # type: ignore[return]
        return _MockAgent([])

    return factory  # type: ignore[return-value]


@pytest.fixture
def agent_factory_hanging() -> AgentFactory:
    """Factory returning an agent whose prompt() never returns."""

    def factory(inp: ReflectionInput) -> _HangingAgent:  # type: ignore[return]
        return _HangingAgent()

    return factory  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reflect_publishes_event_when_memory_saved(
    user_event_service_mock: MagicMock,
    agent_factory_mock: AgentFactory,
) -> None:
    runner = ReflectionRunner(
        user_event_service=user_event_service_mock,
        agent_factory=agent_factory_mock,
        memory_service_factory=MagicMock(),
        timeout_sec=5.0,
    )
    await runner.reflect(_mk_input())

    user_event_service_mock.publish.assert_called_once()
    inp = user_event_service_mock.publish.call_args.args[0]
    assert inp.type == UserEventType.MEMORY_UPDATED
    assert inp.payload["items"][0]["op"] == "save"
    assert inp.payload["items"][0]["memory_id"] == "mem_abc"


@pytest.mark.asyncio
async def test_reflect_no_publish_when_no_memory_saved(
    user_event_service_mock: MagicMock,
    agent_factory_silent: AgentFactory,
) -> None:
    runner = ReflectionRunner(
        user_event_service=user_event_service_mock,
        agent_factory=agent_factory_silent,
        memory_service_factory=MagicMock(),
        timeout_sec=5.0,
    )
    await runner.reflect(
        ReflectionInput(
            conversation_id="conv_x",
            run_id="run_y",
            user_id="usr_z",
            workspace_id=None,
            turn=ReflectionTurn(
                user_message="hi",
                assistant_message="hi",
                tool_summaries=[],
            ),
        )
    )
    user_event_service_mock.publish.assert_not_called()


@pytest.mark.asyncio
async def test_reflect_drops_silently_on_timeout(
    user_event_service_mock: MagicMock,
    agent_factory_hanging: AgentFactory,
) -> None:
    runner = ReflectionRunner(
        user_event_service=user_event_service_mock,
        agent_factory=agent_factory_hanging,
        memory_service_factory=MagicMock(),
        timeout_sec=0.1,
    )
    # must NOT raise
    await runner.reflect(
        ReflectionInput(
            conversation_id="c",
            run_id="r",
            user_id="u",
            workspace_id=None,
            turn=ReflectionTurn(
                user_message="x",
                assistant_message="y",
                tool_summaries=[],
            ),
        )
    )
    user_event_service_mock.publish.assert_not_called()


@pytest.mark.asyncio
async def test_reflect_idempotency(
    user_event_service_mock: MagicMock,
) -> None:
    """Second call with same run_id is a no-op: no agent created, no publish."""
    factory_call_count = 0

    def counting_factory(inp: ReflectionInput) -> _MockAgent:  # type: ignore[return]
        nonlocal factory_call_count
        factory_call_count += 1
        return _MockAgent([_tool_event("memory_save", "mem_xyz")])

    runner = ReflectionRunner(
        user_event_service=user_event_service_mock,
        agent_factory=counting_factory,  # type: ignore[arg-type]
        memory_service_factory=MagicMock(),
        timeout_sec=5.0,
    )
    inp = _mk_input(run_id="dedup_run")
    await runner.reflect(inp)
    await runner.reflect(inp)  # second call — same run_id

    assert factory_call_count == 1
    assert user_event_service_mock.publish.call_count == 1
