"""RunManager delivers a HITL answer to the run's in-process channel and
routes a cross-worker hitl_answer control message."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from cubepi.hitl import ApproveAnswer

from cubebox.streams.run_manager import RunManager


class _RecordingChannel:
    def __init__(self) -> None:
        self.answers: list[tuple[str, ApproveAnswer]] = []

    async def answer(self, question_id: str, answer: ApproveAnswer) -> None:
        self.answers.append((question_id, answer))


class _FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, data: str) -> int:
        self.published.append((channel, data))
        return 0


def _make_rm() -> RunManager:
    return RunManager(
        app=MagicMock(),
        redis=_FakeRedis(),  # type: ignore[arg-type]
        key_prefix="t",
        run_event_ttl_seconds=60,
    )


@pytest.mark.asyncio
async def test_dispatch_hitl_answer_delivers_in_process() -> None:
    rm = _make_rm()
    ch = _RecordingChannel()
    rm._hitl_channels["run_1"] = ch
    result = await rm.dispatch_hitl_answer("run_1", "qid_abc", "approve", None)
    assert result == "delivered"
    assert ch.answers == [("qid_abc", ApproveAnswer(decision="approve", reason=None))]


@pytest.mark.asyncio
async def test_dispatch_hitl_answer_publishes_when_not_local() -> None:
    rm = _make_rm()
    result = await rm.dispatch_hitl_answer("ghost", "qid_abc", "deny", "no")
    assert result == "published"
    # published on the control channel as a hitl_answer
    assert rm._redis.published  # type: ignore[attr-defined]
    _, payload = rm._redis.published[0]  # type: ignore[attr-defined]
    assert "hitl_answer" in payload
    assert "qid_abc" in payload


@pytest.mark.asyncio
async def test_handle_control_routes_hitl_answer() -> None:
    rm = _make_rm()
    ch = _RecordingChannel()
    rm._hitl_channels["run_1"] = ch
    await rm._handle_control(
        {
            "type": "hitl_answer",
            "run_id": "run_1",
            "question_id": "qid_abc",
            "decision": "approve",
            "reason": None,
        }
    )
    assert ch.answers == [("qid_abc", ApproveAnswer(decision="approve", reason=None))]


@pytest.mark.asyncio
async def test_handle_control_hitl_answer_unknown_run_is_dropped() -> None:
    rm = _make_rm()
    # must not raise
    await rm._handle_control(
        {
            "type": "hitl_answer",
            "run_id": "ghost",
            "question_id": "qid_abc",
            "decision": "deny",
            "reason": "x",
        }
    )
