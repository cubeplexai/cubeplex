"""Tests for fold_event handling text_delta events on the new CardState path."""

from cubeplex.im.outbound import OutboundOp, fold_event
from cubeplex.im.types import RenderState


def _state() -> RenderState:
    return RenderState(bot_name="cubeplex", run_id="run_1")


def test_first_text_delta_emits_card_create() -> None:
    state = _state()
    op = fold_event({"type": "text_delta", "data": {"content": "Hi"}}, state, now=0.0)
    assert isinstance(op, OutboundOp)
    assert op.kind == "card_create"
    assert state.card_state.streaming_content == "Hi"


def test_subsequent_text_delta_sends_cumulative_content() -> None:
    """Feishu CardKit streaming_mode markdown expects the FULL cumulative text
    on every PUT; the platform diffs it client-side for the typewriter effect.
    Sending only the delta would replace the rendered text with just the tail."""
    state = _state()
    fold_event({"type": "text_delta", "data": {"content": "Hi"}}, state, now=0.0)
    # The card_create op is dispatched externally; tailer sets card_id.
    state.card_id = "AAQA"
    op = fold_event({"type": "text_delta", "data": {"content": " there"}}, state, now=0.2)
    assert op is not None
    assert op.kind == "stream_text"
    assert state.card_state.streaming_content == "Hi there"
    # Cumulative full content, not the delta.
    assert op.text == "Hi there"


def test_throttled_delta_then_unthrottled_resends_cumulative() -> None:
    """A throttled text_delta updates streaming_content but emits no op;
    the next un-throttled emit carries the full cumulative content so the
    typewriter increment matches what was buffered while throttled."""
    state = _state()
    state.card_id = "AAQA"
    state.card_state.streaming_content = "Hi"
    state.last_stream_monotonic = 0.0
    # First delta arrives at t=0.05 — throttled (< stream_interval 0.1).
    op_throttled = fold_event({"type": "text_delta", "data": {"content": "AB"}}, state, now=0.05)
    assert op_throttled is None
    assert state.card_state.streaming_content == "HiAB"
    # Second delta arrives at t=0.15 — un-throttled. Must send full "HiABCD".
    op = fold_event({"type": "text_delta", "data": {"content": "CD"}}, state, now=0.15)
    assert op is not None
    assert op.kind == "stream_text"
    assert op.text == "HiABCD"


def test_throttled_delta_returns_none() -> None:
    state = _state()
    fold_event({"type": "text_delta", "data": {"content": "a"}}, state, now=0.0)
    state.card_id = "AAQA"
    state.last_stream_monotonic = 1.0
    op = fold_event({"type": "text_delta", "data": {"content": "b"}}, state, now=1.05)
    assert op is None
    assert state.card_state.streaming_content == "ab"
