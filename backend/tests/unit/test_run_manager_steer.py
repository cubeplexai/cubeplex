"""Unit tests for RunManager's live-agent registry + steer_run."""

import pytest

from cubebox.streams.run_manager import RunManager


class _FakeAgent:
    def __init__(self) -> None:
        self.steered: list[str] = []

    def steer(self, message) -> None:  # noqa: ANN001 - cubepi Message
        self.steered.append(message.content[0].text)


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
    assert agent.steered == ["go left instead"]


@pytest.mark.asyncio
async def test_steer_run_returns_false_when_no_agent() -> None:
    mgr = _make_manager()
    mgr._agents = {}

    steered = await mgr.steer_run("missing", "hello")

    assert steered is False
