"""Tests for pending_hitl in the conversation bootstrap response.

Three branches: ask_user, sandbox_confirm, and null. Cold-start fallback
is the load-bearing path — when Redis active-run has aged out, the
serialized pending_hitl must still carry a run_id (from the cubepi v3
load_pending_run_id companion)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cubeplex.streams.hitl_resume import serialize_pending_hitl

# Unit-test the serializer directly; the route-level integration is
# covered by T16 E2E (route setup is heavy — needs full FastAPI deps).


def _fake_ask_pending(qid: str = "q1") -> MagicMock:
    from cubepi.hitl.types import Option, Question

    pending = MagicMock()
    pending.question_id = qid
    pending.created_at = 1700000000.0
    pending.payload.kind = "ask"
    # Real Pydantic models so _as_dict() exercises the model_dump path.
    pending.payload.questions = [
        Question(
            key="a",
            prompt="pick",
            options=[Option(label="X", value="x")],
            multi_select=False,
            required=True,
        )
    ]
    return pending


def _fake_approve_pending(qid: str = "q1") -> MagicMock:
    pending = MagicMock()
    pending.question_id = qid
    pending.created_at = 1700000000.0
    pending.payload.kind = "approve"
    pending.payload.tool_call_id = "tc1"
    pending.payload.args = {"command": "rm -rf /tmp/x"}
    pending.payload.details = {"matched_pattern": "rm *"}
    return pending


def test_serialize_ask_user_pending() -> None:
    p = _fake_ask_pending()
    out = serialize_pending_hitl(p, run_id="r1")
    assert out["run_id"] == "r1"
    assert out["question_id"] == "q1"
    assert out["kind"] == "ask_user"
    # utc_isoformat convention: ISO 8601 with explicit UTC offset.
    assert out["requested_at"].endswith("+00:00")
    assert len(out["questions"]) == 1
    assert out["questions"][0]["key"] == "a"
    assert out["questions"][0]["options"] == [
        {"label": "X", "value": "x", "description": None, "allow_input": False}
    ]


def test_serialize_sandbox_confirm_pending() -> None:
    p = _fake_approve_pending()
    out = serialize_pending_hitl(p, run_id="r1")
    assert out["kind"] == "sandbox_confirm"
    assert out["tool_call_id"] == "tc1"
    assert out["command"] == "rm -rf /tmp/x"
    assert out["matched_pattern"] == "rm *"


def test_serialize_handles_missing_details() -> None:
    """Defensive: ApproveRequest.details defaults to None in cubepi."""
    p = _fake_approve_pending()
    p.payload.details = None
    p.payload.args = None
    out = serialize_pending_hitl(p, run_id="r1")
    assert out["command"] == ""
    assert out["matched_pattern"] == ""


def test_serialize_handles_dict_questions_post_jsonb_roundtrip() -> None:
    """Defensive: when pending comes back from JSONB, inner objects may
    be dicts rather than Pydantic models — _as_dict must handle both."""
    p = MagicMock()
    p.question_id = "q1"
    p.created_at = 1700000000.0
    p.payload.kind = "ask"
    # Plain dicts, NOT Pydantic models.
    p.payload.questions = [
        {
            "key": "a",
            "prompt": "pick",
            "options": [{"label": "X", "value": "x"}],
            "multi_select": False,
            "required": True,
        }
    ]
    out = serialize_pending_hitl(p, run_id="r1")
    assert out["questions"][0]["key"] == "a"
    assert out["questions"][0]["options"] == [{"label": "X", "value": "x"}]


def test_serialize_unknown_kind_raises() -> None:
    p = MagicMock()
    p.question_id = "q1"
    p.created_at = 1700000000.0
    p.payload.kind = "confirm"
    with pytest.raises(ValueError, match="unsupported pending HITL kind"):
        serialize_pending_hitl(p, run_id="r1")
