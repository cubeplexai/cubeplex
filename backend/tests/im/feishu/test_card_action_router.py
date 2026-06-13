"""Tests for card_action_router.dispatch — pure routing logic."""

import pytest

from cubebox.im.feishu.card_action_router import (
    ActionPayload,
    InvalidAction,
    ResumeAction,
    dispatch,
    parse_action_payload,
)


def test_parse_payload_extracts_fields() -> None:
    event = {
        "operator": {"open_id": "ou_user_1"},
        "action": {
            "tag": "button",
            "value": {
                "action": "ask_user",
                "run_id": "run_1",
                "choice": "yes",
                "question_id": "q_1",
            },
        },
    }
    parsed = parse_action_payload(event)
    assert parsed.kind == "ask_user"
    assert parsed.run_id == "run_1"
    assert parsed.choice == "yes"
    assert parsed.question_id == "q_1"
    assert parsed.operator_open_id == "ou_user_1"


def test_parse_payload_question_id_optional() -> None:
    event = {
        "operator": {"open_id": "ou_user_1"},
        "action": {"value": {"action": "ask_user", "run_id": "run_1", "choice": "yes"}},
    }
    parsed = parse_action_payload(event)
    assert parsed.question_id == ""


def test_parse_payload_rejects_missing_action() -> None:
    with pytest.raises(InvalidAction):
        parse_action_payload({"operator": {"open_id": "x"}, "action": {}})


def test_parse_payload_rejects_unknown_kind() -> None:
    with pytest.raises(InvalidAction):
        parse_action_payload(
            {
                "operator": {"open_id": "x"},
                "action": {"value": {"action": "weird", "run_id": "r", "choice": "c"}},
            }
        )


def test_parse_payload_rejects_missing_operator() -> None:
    with pytest.raises(InvalidAction):
        parse_action_payload(
            {
                "action": {"value": {"action": "ask_user", "run_id": "r", "choice": "c"}},
            }
        )


def test_parse_payload_rejects_missing_run_id() -> None:
    with pytest.raises(InvalidAction):
        parse_action_payload(
            {
                "operator": {"open_id": "x"},
                "action": {"value": {"action": "ask_user", "choice": "c"}},
            }
        )


def test_dispatch_ask_user_returns_resume_action() -> None:
    payload = ActionPayload(
        kind="ask_user",
        run_id="run_1",
        choice="yes",
        operator_open_id="ou_x",
        question_id="q_1",
    )
    action = dispatch(payload, expected_responder_open_id="ou_x")
    assert isinstance(action, ResumeAction)
    assert action.run_id == "run_1"
    assert action.input_kind == "ask_user"
    assert action.choice == "yes"
    assert action.question_id == "q_1"
    assert action.operator_open_id == "ou_x"


def test_dispatch_responder_mismatch_returns_none() -> None:
    payload = ActionPayload(
        kind="ask_user",
        run_id="run_1",
        choice="yes",
        operator_open_id="ou_x",
        question_id="q_1",
    )
    action = dispatch(payload, expected_responder_open_id="ou_other")
    assert action is None


def test_dispatch_expected_responder_none_returns_none() -> None:
    payload = ActionPayload(
        kind="ask_user",
        run_id="run_1",
        choice="yes",
        operator_open_id="ou_x",
        question_id="q_1",
    )
    action = dispatch(payload, expected_responder_open_id=None)
    assert action is None


def test_dispatch_sandbox_confirm_maps_to_sandbox_decision() -> None:
    payload = ActionPayload(
        kind="sandbox_confirm",
        run_id="run_1",
        choice="approve",
        operator_open_id="ou_x",
        question_id="qsc_1",
    )
    action = dispatch(payload, expected_responder_open_id="ou_x")
    assert action is not None
    assert action.input_kind == "sandbox_confirm"
    assert action.choice == "approve"
    assert action.question_id == "qsc_1"
