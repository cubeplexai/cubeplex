"""Unit tests for RunManager's live-agent registry + steer_run."""

import pytest

from cubeplex.streams.run_manager import RunManager


class _FakeAgent:
    def __init__(self) -> None:
        self.steered: list = []
        self.cancelled: list[str] = []

    def steer(self, message) -> None:  # noqa: ANN001 - cubepi Message
        self.steered.append(message)

    def cancel_steer(self, steer_id: str) -> bool:  # noqa: ANN001
        self.cancelled.append(steer_id)
        return True


class _FakeRedis:
    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.published.append(payload)


def _make_manager() -> RunManager:
    # Construct without touching Redis/app: registry + steer_run don't need them.
    return RunManager.__new__(RunManager)  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_steer_run_calls_agent_steer_for_registered_run() -> None:
    mgr = _make_manager()
    mgr._agents = {}
    agent = _FakeAgent()
    mgr._agents["run-1"] = agent

    steered = await mgr.steer_run("run-1", "go left instead")

    assert steered is True
    assert agent.steered[0].content[0].text == "go left instead"


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
    assert agent.steered[0].metadata["steer_id"] == "s1"


@pytest.mark.asyncio
async def test_dispatch_cancel_steer_calls_agent() -> None:
    mgr = _make_manager()
    mgr._agents = {}
    agent = _FakeAgent()
    mgr._agents["run-1"] = agent
    status = await mgr.dispatch_cancel_steer("run-1", "s1")
    assert status == "cancelled"
    assert agent.cancelled == ["s1"]


@pytest.mark.asyncio
async def test_dispatch_cancel_steer_no_local_agent_publishes() -> None:
    mgr = _make_manager()
    mgr._agents = {}
    mgr._redis = _FakeRedis()
    mgr._control_channel = "ctrl"
    status = await mgr.dispatch_cancel_steer("missing-run", "s1")
    assert status == "published"
