"""Tests for fold_event ask_user_request / sandbox_confirm_request / *_resolved / done / error."""

from cubebox.im.outbound import fold_event
from cubebox.im.types import RenderState


def _state_with_card() -> RenderState:
    s = RenderState(bot_name="cubebox", run_id="run_1")
    s.card_id = "AAQA"
    return s


def test_ask_user_request_populates_pending_input() -> None:
    state = _state_with_card()
    state.last_patch_monotonic = 100.0
    op = fold_event(
        {
            "type": "ask_user_request",
            "data": {
                "question_id": "q_1",
                "questions": [
                    {
                        "key": "choice",
                        "prompt": "Continue?",
                        "options": ["yes", "no"],
                        "multi_select": False,
                        "required": True,
                    }
                ],
                "timeout_seconds": 600,
            },
        },
        state,
        now=100.1,
    )
    pending = state.card_state.pending_input
    assert pending is not None
    assert pending.kind == "ask_user"
    assert pending.question == "Continue?"
    assert pending.question_id == "q_1"
    assert pending.choices == [("yes", "default"), ("no", "default")]
    assert op is not None and op.kind == "patch_card"


def test_ask_user_request_with_dict_options() -> None:
    state = _state_with_card()
    fold_event(
        {
            "type": "ask_user_request",
            "data": {
                "question_id": "q_2",
                "questions": [
                    {
                        "key": "pick",
                        "prompt": "Pick one",
                        "options": [
                            {"key": "a", "type": "primary"},
                            {"key": "b"},
                            {"label": "c", "type": "danger"},
                        ],
                    }
                ],
            },
        },
        state,
        now=0.0,
    )
    pending = state.card_state.pending_input
    assert pending is not None
    assert pending.choices == [
        ("a", "primary"),
        ("b", "default"),
        ("c", "danger"),
    ]


def test_ask_user_request_with_no_options_falls_back_to_ok() -> None:
    state = _state_with_card()
    fold_event(
        {
            "type": "ask_user_request",
            "data": {
                "question_id": "q_3",
                "questions": [{"key": "freeform", "prompt": "Type something"}],
            },
        },
        state,
        now=0.0,
    )
    pending = state.card_state.pending_input
    assert pending is not None
    assert pending.choices == [("ok", "primary")]


def test_ask_user_request_multiple_questions_notes_count() -> None:
    state = _state_with_card()
    fold_event(
        {
            "type": "ask_user_request",
            "data": {
                "question_id": "q_x",
                "questions": [
                    {"key": "k1", "prompt": "First?", "options": ["yes"]},
                    {"key": "k2", "prompt": "Second?"},
                ],
            },
        },
        state,
        now=0.0,
    )
    pending = state.card_state.pending_input
    assert pending is not None
    assert "First?" in pending.question
    assert "1 more" in pending.question or "+1" in pending.question


def test_sandbox_confirm_request_renders_command() -> None:
    state = _state_with_card()
    op = fold_event(
        {
            "type": "sandbox_confirm_request",
            "data": {
                "question_id": "qsc_1",
                "tool_call_id": "tc_1",
                "command": "rm -rf /",
                "matched_pattern": "dangerous",
                "timeout_seconds": 300,
            },
        },
        state,
        now=0.0,
    )
    pending = state.card_state.pending_input
    assert pending is not None
    assert pending.kind == "sandbox_confirm"
    assert "rm -rf /" in pending.question
    assert pending.question_id == "qsc_1"
    assert pending.choices == [("approve", "primary"), ("deny", "danger")]
    assert op is not None and op.kind == "patch_card"


def test_ask_user_resolved_flips_receipt_when_question_id_matches() -> None:
    state = _state_with_card()
    fold_event(
        {
            "type": "ask_user_request",
            "data": {
                "question_id": "q_1",
                "questions": [{"key": "x", "prompt": "Yes?", "options": ["yes"]}],
            },
        },
        state,
        now=0.0,
    )
    op = fold_event(
        {
            "type": "ask_user_resolved",
            "data": {
                "question_id": "q_1",
                "answers": {"x": "yes"},
                "cancelled": False,
                "timed_out": False,
            },
        },
        state,
        now=5.0,
    )
    pending = state.card_state.pending_input
    assert pending is not None
    assert pending.resolved_choice == "answered"
    assert op is not None and op.kind == "patch_card"


def test_ask_user_resolved_with_mismatched_question_id_dropped() -> None:
    state = _state_with_card()
    fold_event(
        {
            "type": "ask_user_request",
            "data": {
                "question_id": "q_1",
                "questions": [{"key": "x", "prompt": "Yes?", "options": ["yes"]}],
            },
        },
        state,
        now=0.0,
    )
    op = fold_event(
        {
            "type": "ask_user_resolved",
            "data": {"question_id": "q_OTHER", "cancelled": False, "timed_out": False},
        },
        state,
        now=5.0,
    )
    pending = state.card_state.pending_input
    assert pending is not None
    assert pending.resolved_choice is None
    assert op is None


def test_sandbox_confirm_resolved_records_decision() -> None:
    state = _state_with_card()
    fold_event(
        {
            "type": "sandbox_confirm_request",
            "data": {
                "question_id": "qsc_1",
                "tool_call_id": "tc_1",
                "command": "ls",
                "matched_pattern": None,
                "timeout_seconds": None,
            },
        },
        state,
        now=0.0,
    )
    fold_event(
        {
            "type": "sandbox_confirm_resolved",
            "data": {
                "question_id": "qsc_1",
                "decision": "approve",
                "cancelled": False,
                "timed_out": False,
                "reason": None,
            },
        },
        state,
        now=2.0,
    )
    pending = state.card_state.pending_input
    assert pending is not None
    assert pending.resolved_choice == "approve"


def test_done_finalizes_with_elapsed_from_run_start() -> None:
    state = _state_with_card()
    # First event stamps run_start_monotonic.
    fold_event({"type": "text_delta", "data": {"content": "."}}, state, now=10.0)
    # Done event 2.5 seconds later.
    op = fold_event({"type": "done", "data": {}}, state, now=12.5)
    assert state.card_state.finalized is True
    assert state.card_state.elapsed_ms == 2500
    assert op is not None and op.kind == "finalize"
    assert op.final is True


def test_error_finalizes_with_message() -> None:
    state = _state_with_card()
    op = fold_event(
        {
            "type": "error",
            "data": {
                "error_code": "run_error",
                "message": "boom",
                "details": "stack...",
            },
        },
        state,
        now=0.0,
    )
    assert state.card_state.finalized is True
    assert state.card_state.error == "boom"
    assert op is not None and op.kind == "finalize"
    assert op.final is True


def test_first_event_stamps_run_start_monotonic() -> None:
    state = RenderState(bot_name="cubebox", run_id="run_1")
    assert state.card_state.run_start_monotonic == 0.0
    fold_event({"type": "text_delta", "data": {"content": "."}}, state, now=42.0)
    assert state.card_state.run_start_monotonic == 42.0
    # Second event does not overwrite.
    fold_event({"type": "text_delta", "data": {"content": "."}}, state, now=99.0)
    assert state.card_state.run_start_monotonic == 42.0
