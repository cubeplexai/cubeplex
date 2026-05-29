"""RunManager delivers an ask_user HITL answer to the in-process channel."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from cubebox.streams.run_manager import RunManager, cubepi_dict_to_agent_event


class _RecordingChannel:
    def __init__(self) -> None:
        self.answers: list[tuple[str, object]] = []

    async def answer(self, question_id: str, answer: object) -> None:
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
async def test_dispatch_ask_user_answer_delivers_in_process() -> None:
    rm = _make_rm()
    ch = _RecordingChannel()
    rm._hitl_channels["run_1"] = ch
    answers = {"color": "red", "size": "medium"}
    result = await rm.dispatch_ask_user_answer("run_1", "qid-ask", answers)
    assert result == "delivered"
    assert ch.answers == [("qid-ask", answers)]


@pytest.mark.asyncio
async def test_dispatch_ask_user_answer_publishes_when_not_local() -> None:
    rm = _make_rm()
    answers = {"topic": "python"}
    result = await rm.dispatch_ask_user_answer("ghost", "qid-ask", answers)
    assert result == "published"
    assert rm._redis.published  # type: ignore[attr-defined]
    _, payload = rm._redis.published[0]  # type: ignore[attr-defined]
    data = json.loads(payload)
    assert data["type"] == "ask_user_answer"
    assert data["question_id"] == "qid-ask"
    assert data["answers"] == answers


@pytest.mark.asyncio
async def test_handle_control_routes_ask_user_answer() -> None:
    rm = _make_rm()
    ch = _RecordingChannel()
    rm._hitl_channels["run_1"] = ch
    await rm._handle_control(
        {
            "type": "ask_user_answer",
            "run_id": "run_1",
            "question_id": "qid-ask",
            "answers": {"color": "blue"},
        }
    )
    assert ch.answers == [("qid-ask", {"color": "blue"})]


def test_cubepi_dict_to_agent_event_ask_user_request() -> None:
    from cubebox.agents.schemas import AskUserRequestEvent

    evt = cubepi_dict_to_agent_event(
        {
            "type": "ask_user_request",
            "question_id": "qid-1",
            "questions": [
                {
                    "key": "color",
                    "prompt": "Pick a color?",
                    "options": None,
                    "multi_select": False,
                    "required": True,
                }
            ],
            "timeout_seconds": 120.0,
        },
        "2026-05-30T00:00:00+00:00",
    )
    assert isinstance(evt, AskUserRequestEvent)
    assert evt.data["question_id"] == "qid-1"
    assert len(evt.data["questions"]) == 1
    assert evt.data["questions"][0]["key"] == "color"
    assert evt.data["timeout_seconds"] == 120.0


def test_cubepi_dict_to_agent_event_ask_user_resolved() -> None:
    from cubebox.agents.schemas import AskUserResolvedEvent

    evt = cubepi_dict_to_agent_event(
        {
            "type": "ask_user_resolved",
            "question_id": "qid-1",
            "answers": {"color": "red"},
            "cancelled": False,
            "timed_out": False,
        },
        "2026-05-30T00:00:00+00:00",
    )
    assert isinstance(evt, AskUserResolvedEvent)
    assert evt.data["question_id"] == "qid-1"
    assert evt.data["answers"] == {"color": "red"}
    assert evt.data["cancelled"] is False
