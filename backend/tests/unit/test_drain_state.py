"""DrainState transitions."""

from __future__ import annotations

from cubeplex.lifecycle.drain import DrainState


def test_initial_state_is_accepting() -> None:
    state = DrainState()
    assert state.is_accepting()
    assert not state.is_draining()


def test_enter_draining_flips_flag() -> None:
    state = DrainState()
    state.enter_draining()
    assert not state.is_accepting()
    assert state.is_draining()


def test_enter_draining_is_idempotent() -> None:
    state = DrainState()
    state.enter_draining()
    state.enter_draining()
    assert state.is_draining()
