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

from cubeplex.models.user_event import UserEventType
from cubeplex.services.reflection_runner import (
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
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING")
    runner = ReflectionRunner(
        user_event_service=user_event_service_mock,
        agent_factory=agent_factory_hanging,
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
    assert any("reflection timed out" in r.message for r in caplog.records)


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
        timeout_sec=5.0,
    )
    inp = _mk_input(run_id="dedup_run")
    await runner.reflect(inp)
    await runner.reflect(inp)  # second call — same run_id

    assert factory_call_count == 1
    assert user_event_service_mock.publish.call_count == 1


class TestBuildSeedPrompt:
    """Unit tests for _build_seed_prompt — no async needed."""

    def _runner(self) -> ReflectionRunner:
        return ReflectionRunner(
            user_event_service=MagicMock(),
            agent_factory=MagicMock(),
        )

    def _inp(
        self,
        *,
        existing: list[tuple[str, str, str]] | None = None,
        tool_summaries: list[dict[str, str]] | None = None,
    ) -> ReflectionInput:
        return ReflectionInput(
            conversation_id="c",
            run_id="r",
            user_id="u",
            workspace_id=None,
            turn=ReflectionTurn(
                user_message="USER MSG",
                assistant_message="ASST MSG",
                tool_summaries=tool_summaries or [],
            ),
            existing_memory_items=existing or [],
        )

    def test_no_existing_memory_no_memory_block(self) -> None:
        seed = self._runner()._build_seed_prompt(self._inp())
        assert "current memory" not in seed
        assert "USER MSG" in seed
        assert "ASST MSG" in seed

    def test_existing_memory_renders_block(self) -> None:
        items = [
            ("mem-abc", "preference", "用户偏好中文交流"),
            ("mem-def", "project_fact", "CubePi 是 Agent 框架"),
        ]
        seed = self._runner()._build_seed_prompt(self._inp(existing=items))
        assert "current memory" in seed
        assert "[mem-abc]" in seed
        assert "(preference)" in seed
        assert "用户偏好中文交流" in seed
        assert "[mem-def]" in seed
        # memory block appears BEFORE the turn
        assert seed.index("current memory") < seed.index("Last turn")

    def test_existing_memory_content_truncated_to_200_chars(self) -> None:
        long_content = "x" * 300
        items = [("mem-xyz", "project_fact", long_content)]
        seed = self._runner()._build_seed_prompt(self._inp(existing=items))
        assert long_content not in seed
        assert "x" * 200 in seed

    def test_tool_summaries_rendered(self) -> None:
        summaries = [
            {"name": "execute", "args_summary": "pip install foo", "outcome": "ok"},
            {"name": "execute", "args_summary": "twitter whoami", "outcome": "error: HTTP 403"},
        ]
        seed = self._runner()._build_seed_prompt(self._inp(tool_summaries=summaries))
        assert "Tools called" in seed
        assert "execute(pip install foo) -> ok" in seed
        assert "execute(twitter whoami) -> error: HTTP 403" in seed
