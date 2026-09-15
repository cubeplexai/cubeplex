"""Unit tests for RunManager's live-agent registry + steer_run."""

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
