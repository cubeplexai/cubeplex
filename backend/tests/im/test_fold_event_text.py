"""Tests for fold_event handling text_delta events on the new CardState path."""

from cubebox.im.outbound import OutboundOp, fold_event
from cubebox.im.types import RenderState


def _state() -> RenderState:
    return RenderState(bot_name="cubebox", run_id="run_1")


def test_first_text_delta_emits_card_create() -> None:
    state = _state()
    op = fold_event({"type": "text_delta", "data": {"content": "Hi"}}, state, now=0.0)
    assert isinstance(op, OutboundOp)
    assert op.kind == "card_create"
    assert state.card_state.streaming_content == "Hi"


def test_subsequent_text_delta_emits_stream_text() -> None:
    state = _state()
    fold_event({"type": "text_delta", "data": {"content": "Hi"}}, state, now=0.0)
    # The card_create op is dispatched externally; tailer sets card_id +
    # advances streamed_to to the full streaming_content length (the
    # initial render folded all buffered text into the markdown element).
    state.card_id = "AAQA"
    state.card_state.streamed_to = len(state.card_state.streaming_content)
    op = fold_event({"type": "text_delta", "data": {"content": " there"}}, state, now=0.2)
    assert op is not None
    assert op.kind == "stream_text"
    assert state.card_state.streaming_content == "Hi there"
    assert op.text == " there"


def test_throttled_delta_then_unthrottled_resends_full_gap() -> None:
    """A throttled text_delta updates streaming_content but not streamed_to;
    the next un-throttled delta must replay every pending character because
    CardKit stream_text is append-semantics, not full-replace."""
    state = _state()
    # Card already created, all prior content streamed.
    state.card_id = "AAQA"
    state.card_state.streaming_content = "Hi"
    state.card_state.streamed_to = 2
    state.last_stream_monotonic = 0.0
    # First delta arrives at t=0.05 — throttled (< stream_interval 0.1).
    op_throttled = fold_event({"type": "text_delta", "data": {"content": "AB"}}, state, now=0.05)
    assert op_throttled is None
    assert state.card_state.streaming_content == "HiAB"
    assert state.card_state.streamed_to == 2  # not advanced
    # Second delta arrives at t=0.15 — un-throttled. Must send "ABCD"
    # (the full pending tail), not just the new "CD".
    op = fold_event({"type": "text_delta", "data": {"content": "CD"}}, state, now=0.15)
    assert op is not None
    assert op.kind == "stream_text"
    assert op.text == "ABCD"


def test_throttled_delta_returns_none() -> None:
    state = _state()
    fold_event({"type": "text_delta", "data": {"content": "a"}}, state, now=0.0)
    state.card_id = "AAQA"
    state.card_state.streamed_to = 1
    state.last_stream_monotonic = 1.0
    op = fold_event({"type": "text_delta", "data": {"content": "b"}}, state, now=1.05)
    assert op is None
    assert state.card_state.streaming_content == "ab"
