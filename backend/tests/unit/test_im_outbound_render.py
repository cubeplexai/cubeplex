"""Unit tests for the outbound render fold (Task 8)."""

from cubebox.im.outbound import (
    OutboundOp,
    fold_event,
    note_edit_success,
    note_flood_strike,
)
from cubebox.im.types import RenderState


def test_first_text_delta_emits_post_with_placeholder_text() -> None:
    st = RenderState()
    op = fold_event({"type": "text_delta", "data": {"content": "Hel"}}, st, now=0.0)
    assert isinstance(op, OutboundOp)
    assert op.kind == "post"
    assert "Hel" in op.text
    assert st.text_buffer == "Hel"


def test_streaming_text_is_debounced() -> None:
    st = RenderState(message_id="m1", text_buffer="Hel", last_edit_monotonic=10.0)
    # 0.2s elapsed < 0.8s default debounce → suppressed
    op = fold_event({"type": "text_delta", "data": {"content": "lo"}}, st, now=10.2)
    assert op is None
    assert st.text_buffer == "Hello"
    # 1.0s elapsed >= 0.8s → emit edit
    op2 = fold_event({"type": "text_delta", "data": {"content": "!"}}, st, now=11.0)
    assert op2 is not None
    assert op2.kind == "edit"
    assert "Hello!" in op2.text


def test_tool_call_coalesced_into_one_line_per_name() -> None:
    st = RenderState(message_id="m1", last_edit_monotonic=0.0)
    fold_event({"type": "tool_call", "data": {"name": "web_search"}}, st, now=5.0)
    fold_event({"type": "tool_call", "data": {"name": "web_search"}}, st, now=6.0)
    fold_event({"type": "tool_call", "data": {"name": "calculator"}}, st, now=7.0)
    assert sum("web_search" in line for line in st.tool_lines) == 1
    assert sum("calculator" in line for line in st.tool_lines) == 1


def test_done_finalizes_to_edit_when_message_already_posted() -> None:
    st = RenderState(message_id="m1", text_buffer="Answer", last_edit_monotonic=0.0)
    op = fold_event({"type": "done", "data": {}}, st, now=99.0)
    assert op is not None
    assert op.kind == "edit"
    assert op.final is True
    assert "Answer" in op.text


def test_done_emits_post_when_no_placeholder_ever_existed() -> None:
    """A run that produces zero text_delta (e.g. tool-only) must still
    surface a final message; without the post-fallback the user sees nothing."""
    st = RenderState()
    op = fold_event({"type": "done", "data": {}}, st, now=99.0)
    assert op is not None
    assert op.kind == "post"
    assert op.final is True


def test_error_replaces_with_notice() -> None:
    st = RenderState(message_id="m1", text_buffer="partial", last_edit_monotonic=0.0)
    op = fold_event({"type": "error", "data": {"message": "boom"}}, st, now=99.0)
    assert op is not None
    assert op.kind == "edit"
    assert op.final is True
    assert "boom" in op.text


def test_error_emits_post_when_no_placeholder_ever_existed() -> None:
    st = RenderState()
    op = fold_event({"type": "error", "data": {"message": "boom"}}, st, now=99.0)
    assert op is not None
    assert op.kind == "post"
    assert op.final is True
    assert "boom" in op.text


def test_artifact_event_emits_op_once_per_id_for_created() -> None:
    st = RenderState()
    art = {"id": "art-1", "name": "chart.png", "artifact_type": "image"}
    op1 = fold_event(
        {"type": "artifact", "data": {"action": "created", "artifact": art}},
        st,
        now=1.0,
    )
    assert op1 is not None
    assert op1.kind == "artifact"
    assert op1.artifact == art
    # Re-emit of "created" for the same id is suppressed.
    op2 = fold_event(
        {"type": "artifact", "data": {"action": "created", "artifact": art}},
        st,
        now=2.0,
    )
    assert op2 is None


def test_artifact_update_re_emits_after_initial_post() -> None:
    st = RenderState()
    art = {"id": "art-2", "name": "doc.md"}
    fold_event({"type": "artifact", "data": {"action": "created", "artifact": art}}, st, now=1.0)
    op = fold_event(
        {"type": "artifact", "data": {"action": "updated", "artifact": art}}, st, now=2.0
    )
    assert op is not None
    assert op.kind == "artifact"


def test_adaptive_backoff_doubles_interval_and_disables_after_three_strikes() -> None:
    st = RenderState()
    assert st.edit_interval == 0.8
    note_flood_strike(st)
    assert st.edit_interval == 1.6
    note_flood_strike(st)
    assert st.edit_interval == 3.2
    note_flood_strike(st)
    assert st.edits_disabled is True
    # Successful edits reset the strike counter but NOT the interval ceiling.
    note_edit_success(st)
    assert st.consecutive_flood_strikes == 0


def test_edits_disabled_suppresses_text_deltas_but_not_terminal() -> None:
    st = RenderState(message_id="m1", text_buffer="hi", last_edit_monotonic=0.0)
    st.edits_disabled = True
    # During the streaming window: no edit emitted.
    op_stream = fold_event({"type": "text_delta", "data": {"content": "!"}}, st, now=99.0)
    assert op_stream is None
    # Terminal event still emits a final edit so the user sees the answer.
    op_done = fold_event({"type": "done", "data": {}}, st, now=100.0)
    assert op_done is not None
    assert op_done.kind == "edit"
    assert op_done.final is True


def test_unknown_event_type_returns_none() -> None:
    st = RenderState()
    op = fold_event({"type": "reasoning", "data": {}}, st, now=0.0)
    assert op is None
