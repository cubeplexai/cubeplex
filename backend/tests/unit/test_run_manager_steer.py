"""Unit tests for RunManager's live-agent registry + steer_run."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from cubeloop.session.input import InputReceipt

from cubeplex.streams.run_manager import RunManager, _registration_was_replaced


class _FakeSession:
    def __init__(self) -> None:
        self.inputs: list = []
        self.cancelled: list[str] = []

    def submit_input(self, envelope) -> InputReceipt:  # noqa: ANN001
        self.inputs.append(envelope)
        return InputReceipt(input_id=envelope.input_id, status="queued")

    def cancel_input(self, steer_id: str) -> InputReceipt:
        self.cancelled.append(steer_id)
        return InputReceipt(input_id=steer_id, status="cancelled")


class _FakeAgent:
    def __init__(self) -> None:
        self.session = _FakeSession()


class _PreparingSession(_FakeSession):
    def __init__(self) -> None:
        super().__init__()
        self.accepting = False

    def submit_input(self, envelope) -> InputReceipt:  # noqa: ANN001
        if not self.accepting:
            return InputReceipt(input_id=envelope.input_id, status="closed")
        return super().submit_input(envelope)


class _PreparingAgent:
    def __init__(self) -> None:
        self.session = _PreparingSession()


class _FakeRedis:
    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.published.append(payload)


def _make_manager() -> RunManager:
    # Construct without touching Redis/app: registry + steer_run don't need them.
    return RunManager.__new__(RunManager)  # type: ignore[call-arg]


def test_missing_originating_agent_does_not_imply_a_replacement() -> None:
    assert _registration_was_replaced(current_agent=object(), originating_agent=None) is False


def test_different_registered_agent_is_a_replacement() -> None:
    assert (
        _registration_was_replaced(
            current_agent=object(),
            originating_agent=object(),
        )
        is True
    )


def test_lost_resume_ownership_is_a_replacement_without_a_registered_agent() -> None:
    assert (
        _registration_was_replaced(
            current_agent=None,
            originating_agent=object(),
            ownership_lost=True,
        )
        is True
    )


@pytest.mark.asyncio
async def test_steer_run_calls_agent_steer_for_registered_run() -> None:
    mgr = _make_manager()
    mgr._agents = {}
    agent = _FakeAgent()
    mgr._agents["run-1"] = agent

    steered = await mgr.steer_run("run-1", "go left instead")

    assert steered is True
    assert agent.session.inputs[0].message.content[0].text == "go left instead"


@pytest.mark.asyncio
async def test_steer_run_returns_false_when_no_agent() -> None:
    mgr = _make_manager()
    mgr._agents = {}

    steered = await mgr.steer_run("missing", "hello")

    assert steered is False


@pytest.mark.asyncio
async def test_dispatch_steer_threads_steer_id_into_metadata() -> None:
    mgr = _make_manager()
    mgr._agents = {}
    agent = _FakeAgent()
    mgr._agents["run-1"] = agent
    status = await mgr.dispatch_steer("run-1", "do X", steer_id="s1")
    assert status == "steered"
    assert agent.session.inputs[0].input_id == "s1"
    assert agent.session.inputs[0].message.metadata["steer_id"] == "s1"


@pytest.mark.asyncio
async def test_dispatch_cancel_steer_calls_agent() -> None:
    mgr = _make_manager()
    mgr._agents = {}
    agent = _FakeAgent()
    mgr._agents["run-1"] = agent
    status = await mgr.dispatch_cancel_steer("run-1", "s1")
    assert status == "cancelled"
    assert agent.session.cancelled == ["s1"]


@pytest.mark.asyncio
async def test_dispatch_cancel_steer_no_local_agent_publishes() -> None:
    mgr = _make_manager()
    mgr._agents = {}
    mgr._redis = _FakeRedis()
    mgr._control_channel = "ctrl"
    status = await mgr.dispatch_cancel_steer("missing-run", "s1")
    assert status == "published"


@pytest.mark.asyncio
async def test_dispatch_steer_buffers_until_session_starts_accepting_input() -> None:
    mgr = _make_manager()
    agent = _PreparingAgent()
    mgr._agents = {"run-1": agent}
    mgr._preparing_runs = {"run-1"}
    mgr._pending_session_inputs = {}

    status = await mgr.dispatch_steer("run-1", "during setup", steer_id="s-setup")

    assert status == "steered"
    assert agent.session.inputs == []
    agent.session.accepting = True
    await mgr._drain_pre_execution_inputs("run-1", agent.session)
    assert agent.session.inputs[0].input_id == "s-setup"
    assert agent.session.inputs[0].message.metadata["steer_id"] == "s-setup"


@pytest.mark.asyncio
async def test_dispatch_steer_forwards_closed_session_to_remote_owner() -> None:
    mgr = _make_manager()
    mgr._agents = {"run-1": _PreparingAgent()}
    mgr._preparing_runs = set()
    mgr._pending_session_inputs = {}
    mgr._ack_waiters = {}
    mgr._publish_control = AsyncMock()  # type: ignore[method-assign]

    status = await mgr.dispatch_steer(
        "run-1",
        "too late",
        steer_id="s-closed",
        ack_timeout=0,
    )

    assert status == "published"
    mgr._publish_control.assert_awaited_once()


@pytest.mark.asyncio
async def test_old_task_callback_preserves_replacement_registration() -> None:
    mgr = _make_manager()

    async def _wait() -> None:
        await asyncio.Event().wait()

    old_task = asyncio.create_task(_wait())
    replacement_task = asyncio.create_task(_wait())
    mgr._tasks = {"run-1": replacement_task}
    mgr._tasks_empty = asyncio.Event()
    mgr._preparing_runs = {"run-1"}
    mgr._pending_session_inputs = {"run-1": {"s1": ("keep", {})}}

    mgr._on_task_done("run-1", old_task)

    assert mgr._tasks["run-1"] is replacement_task
    assert "run-1" in mgr._preparing_runs
    assert "run-1" in mgr._pending_session_inputs
    old_task.cancel()
    replacement_task.cancel()
    await asyncio.gather(old_task, replacement_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_removes_a_buffered_pre_execution_steer() -> None:
    mgr = _make_manager()
    agent = _PreparingAgent()
    mgr._agents = {"run-1": agent}
    mgr._preparing_runs = {"run-1"}
    mgr._pending_session_inputs = {}
    await mgr.dispatch_steer("run-1", "during setup", steer_id="s-setup")

    status = await mgr.dispatch_cancel_steer("run-1", "s-setup")

    assert status == "cancelled"
    assert mgr._pending_session_inputs["run-1"] == {}
