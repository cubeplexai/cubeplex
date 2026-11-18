"""Unit tests for pure background-notice IM receipt key parsing."""

from __future__ import annotations

from cubeplex.im.run_handoff import (
    background_notice_event_id,
    parse_background_notice_attempt_id,
)


def test_round_trip_background_notice_event_id() -> None:
    event_id = background_notice_event_id("bgte-123", "attempt-abc")
    assert event_id == "background_notice:bgte-123:attempt-abc"
    assert parse_background_notice_attempt_id(event_id, notice_id="bgte-123") == "attempt-abc"


def test_parse_background_notice_attempt_id_rejects_mismatched_or_empty() -> None:
    assert (
        parse_background_notice_attempt_id(
            "background_notice:bgte-other:attempt-abc",
            notice_id="bgte-123",
        )
        is None
    )
    assert (
        parse_background_notice_attempt_id(
            "background_notice:bgte-123:",
            notice_id="bgte-123",
        )
        is None
    )
    assert parse_background_notice_attempt_id("schedule:run-1", notice_id="bgte-123") is None
