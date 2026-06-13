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
    # The card_create op is dispatched externally; tailer sets card_id.
    state.card_id = "AAQA"
    op = fold_event({"type": "text_delta", "data": {"content": " there"}}, state, now=0.2)
    assert op is not None
    assert op.kind == "stream_text"
    assert state.card_state.streaming_content == "Hi there"
    assert op.text == " there"


def test_throttled_delta_returns_none() -> None:
    state = _state()
    fold_event({"type": "text_delta", "data": {"content": "a"}}, state, now=0.0)
    state.card_id = "AAQA"
    state.last_stream_monotonic = 1.0
    op = fold_event({"type": "text_delta", "data": {"content": "b"}}, state, now=1.05)
    assert op is None
    assert state.card_state.streaming_content == "ab"
