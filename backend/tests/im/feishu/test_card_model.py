"""Tests for CardState — pure data shape, no IO."""

from cubebox.im.feishu.card_model import (
    ArtifactItem,
    CardState,
    PendingInput,
    ToolStep,
)


def test_card_state_defaults_are_empty() -> None:
    state = CardState(bot_name="cubebox", run_id="run_1")
    assert state.streaming_content == ""
    assert state.tool_steps == []
    assert state.artifacts == []
    assert state.pending_input is None
    assert state.finalized is False
    assert state.error is None
    assert state.elapsed_ms == 0
    assert state.next_seq == 0


def test_tool_step_status_transitions() -> None:
    step = ToolStep(id="tc_1", name="read_file", args={"path": "a"})
    assert step.status == "running"
    step.mark_succeeded(result={"ok": True}, elapsed_ms=312)
    assert step.status == "succeeded"
    assert step.result == {"ok": True}
    assert step.elapsed_ms == 312


def test_tool_step_failure_keeps_error() -> None:
    step = ToolStep(id="tc_2", name="bash", args={"cmd": "x"})
    step.mark_failed(error="permission denied", elapsed_ms=20)
    assert step.status == "failed"
    assert step.error == "permission denied"


def test_artifact_item_carries_share_url() -> None:
    art = ArtifactItem(
        id="art_1",
        artifact_type="document",
        name="report.pdf",
        share_url="https://example.com/share/abc",
    )
    assert art.image_key is None


def test_pending_input_question_and_choices() -> None:
    pending = PendingInput(
        kind="ask_user",
        run_id="run_1",
        question="Continue?",
        choices=[("yes", "primary"), ("no", "default")],
    )
    assert pending.kind == "ask_user"
    assert pending.resolved_choice is None


def test_card_state_advance_seq() -> None:
    state = CardState(bot_name="cubebox", run_id="run_1")
    assert state.advance_seq() == 0
    assert state.advance_seq() == 1
    assert state.next_seq == 2
