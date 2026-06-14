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
    assert pending.answer_key == "choice"
    assert pending.choices == [("yes", "yes", "default"), ("no", "no", "default")]
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
    # No ``value`` field on the options — the renderer mirrors ``key`` as the
    # label (legacy fixture compatibility). When real cubepi emits
    # {label, value}, the next test verifies label/value diverge correctly.
    assert pending.choices == [
        ("a", "a", "primary"),
        ("b", "b", "default"),
        ("c", "c", "danger"),
    ]


def test_ask_user_request_prefers_value_over_label_for_callback() -> None:
    """cubepi's normal option shape is {label, value} — the button text is
    ``label`` (human-visible) and the answer cubepi expects back is ``value``.
    If we used ``label`` for both, an option like {label:"Yes", value:"yes"}
    would send "Yes" to cubepi, which would reject the schema mismatch.
    """
    state = _state_with_card()
    fold_event(
        {
            "type": "ask_user_request",
            "data": {
                "question_id": "q_v",
                "questions": [
                    {
                        "key": "decide",
                        "prompt": "Approve?",
                        "options": [
                            {"label": "Yes", "value": "yes", "type": "primary"},
                            {"label": "No", "value": "no", "type": "danger"},
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
    # Button TEXT is the human-readable label; button VALUE is the schema key
    # cubepi expects in the answer dict.
    assert pending.choices == [
        ("Yes", "yes", "primary"),
        ("No", "no", "danger"),
    ]


def test_ask_user_multi_select_routes_to_web_client_notice() -> None:
    """Multi-select questions need a list answer; a single card-button click
    can only ship one scalar. Treat them like the free-form case — surface
    a notice pointing to the web client and skip the buttons.
    """
    state = _state_with_card()
    fold_event(
        {
            "type": "ask_user_request",
            "data": {
                "question_id": "q_multi",
                "questions": [
                    {
                        "key": "tags",
                        "prompt": "Pick all that apply",
                        "options": [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}],
                        "multi_select": True,
                    }
                ],
            },
        },
        state,
        now=0.0,
    )
    pending = state.card_state.pending_input
    assert pending is not None
    assert pending.choices == []
    assert "多选" in pending.question and "网页端" in pending.question


def test_ask_user_request_with_no_options_renders_text_input_notice() -> None:
    """Free-form questions (no options) cannot be answered via card buttons.
    Instead of synthesizing a misleading "OK" button (which would send the
    string "ok" back to cubepi and fail the schema), the renderer surfaces
    a notice pointing the user to the web client.
    """
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
    assert pending.choices == []
    assert "网页端" in pending.question


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
    assert pending.choices == [("允许", "approve", "primary"), ("拒绝", "deny", "danger")]
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


def test_done_with_paused_true_does_not_finalize() -> None:
    """When RunManager pauses for HITL it appends ``done`` with
    ``data.paused=true``; resume_run_with_answer later appends more events
    to the same stream. If we treat paused-done as terminal the tailer
    exits and the resumed answer never reaches the user. Emit a patch_card
    so any pending_input mutation lands, but DO NOT mark final.
    """
    state = _state_with_card()
    fold_event({"type": "text_delta", "data": {"content": "."}}, state, now=10.0)
    op = fold_event({"type": "done", "data": {"paused": True}}, state, now=11.0)
    assert state.card_state.finalized is False
    assert op is not None
    assert op.kind == "patch_card"
    assert op.final is False


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
