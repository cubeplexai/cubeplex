"""Unit tests for ask_user HITL event translation in convert_agent_event_to_sse."""

from __future__ import annotations

from unittest.mock import MagicMock

from cubeplex.agents.stream import convert_agent_event_to_sse


def _make_hitl_request(kind: str, question_id: str = "qid-1") -> MagicMock:
    req = MagicMock()
    req.question_id = question_id
    req.timeout_seconds = 120.0
    if kind == "ask":
        payload = MagicMock()
        payload.kind = "ask"
        q = MagicMock()
        q.model_dump.return_value = {
            "key": "color",
            "prompt": "Pick a color?",
            "options": None,
            "multi_select": False,
            "required": True,
        }
        payload.questions = [q]
        req.payload = payload
    else:
        payload = MagicMock()
        payload.kind = "approve"
        payload.tool_call_id = "tc-1"
        payload.tool_name = "execute"
        payload.args = {"command": "rm /tmp/x"}
        payload.details = {"matched_pattern": "rm *"}
        req.payload = payload
    return req


def _make_hitl_event(req: MagicMock) -> MagicMock:
    from cubepi.agent.types import HitlRequestEvent  # type: ignore[import-untyped]

    evt = MagicMock(spec=HitlRequestEvent)
    evt.__class__ = HitlRequestEvent
    evt.request = req
    return evt


def _make_answer_event(answer: object, *, cancelled: bool = False) -> MagicMock:
    from cubepi.agent.types import HitlAnswerEvent  # type: ignore[import-untyped]

    evt = MagicMock(spec=HitlAnswerEvent)
    evt.__class__ = HitlAnswerEvent
    evt.question_id = "qid-1"
    evt.answer = answer
    evt.cancelled = cancelled
    evt.timed_out = False
    return evt


def test_ask_kind_emits_ask_user_request() -> None:
    req = _make_hitl_request("ask", "qid-1")
    evt = _make_hitl_event(req)
    out = convert_agent_event_to_sse(evt)
    assert len(out) == 1
    d = out[0]
    assert d["type"] == "ask_user_request"
    assert d["question_id"] == "qid-1"
    assert d["timeout_seconds"] == 120.0
    assert len(d["questions"]) == 1
    assert d["questions"][0]["key"] == "color"


def test_approve_kind_still_emits_sandbox_confirm_request() -> None:
    req = _make_hitl_request("approve", "qid-2")
    evt = _make_hitl_event(req)
    out = convert_agent_event_to_sse(evt)
    assert len(out) == 1
    assert out[0]["type"] == "sandbox_confirm_request"


def test_answer_dict_emits_ask_user_resolved() -> None:
    evt = _make_answer_event({"color": "red"})
    out = convert_agent_event_to_sse(evt)
    assert len(out) == 1
    d = out[0]
    assert d["type"] == "ask_user_resolved"
    assert d["question_id"] == "qid-1"
    assert d["answers"] == {"color": "red"}
    assert d["cancelled"] is False


def test_approve_answer_emits_sandbox_confirm_resolved() -> None:
    from cubepi.hitl import ApproveAnswer

    evt = _make_answer_event(ApproveAnswer(decision="approve", reason=None))
    out = convert_agent_event_to_sse(evt)
    assert len(out) == 1
    assert out[0]["type"] == "sandbox_confirm_resolved"
    assert out[0]["decision"] == "approve"
