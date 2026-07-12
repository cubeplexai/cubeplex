"""Tests for RenderState's new card-oriented shape."""

from cubeplex.im.feishu.card_model import CardState
from cubeplex.im.types import RenderState


def test_render_state_owns_card_state() -> None:
    state = RenderState(bot_name="cubeplex", run_id="run_1")
    assert isinstance(state.card_state, CardState)
    assert state.card_state.run_id == "run_1"
    assert state.card_id is None
    assert state.card_unavailable is False


def test_render_state_keeps_reaction_id_field() -> None:
    state = RenderState(bot_name="cubeplex", run_id="run_1")
    assert state.reaction_in_progress_id is None
    state.reaction_in_progress_id = "rx_1"
    assert state.reaction_in_progress_id == "rx_1"


def test_render_state_throttle_buckets() -> None:
    state = RenderState(bot_name="cubeplex", run_id="run_1")
    assert state.stream_interval == 0.1
    assert state.patch_interval == 1.5
    assert state.last_stream_monotonic == 0.0
    assert state.last_patch_monotonic == 0.0
