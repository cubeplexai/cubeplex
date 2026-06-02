"""Truth-table tests for ``classify_terminal_status``.

Covers the five rows in cubebox/streams/hitl_resume.py docstring. Pure-function
tests, no fixtures.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from cubebox.streams.hitl_resume import TerminalClassification, classify_terminal_status


def _fake_pending(qid: str) -> MagicMock:
    p = MagicMock()
    p.question_id = qid
    return p


def test_no_pending_completes() -> None:
    r = classify_terminal_status(
        final_pending=None,
        answered_question_id=None,
        saw_hitl_request_event=True,
    )
    assert r == TerminalClassification(status="completed", clear_pending=False)


def test_pending_without_hitl_event_is_stale() -> None:
    r = classify_terminal_status(
        final_pending=_fake_pending("q-leftover"),
        answered_question_id=None,
        saw_hitl_request_event=False,
    )
    assert r == TerminalClassification(status="completed", clear_pending=True)


def test_respond_dangling_pending_clears() -> None:
    # Hypothetical respond-path case (T8 will exercise this end-to-end).
    r = classify_terminal_status(
        final_pending=_fake_pending("q-original"),
        answered_question_id="q-original",
        saw_hitl_request_event=False,
    )
    assert r == TerminalClassification(status="completed", clear_pending=True)


def test_respond_new_pending_paused() -> None:
    r = classify_terminal_status(
        final_pending=_fake_pending("q-new"),
        answered_question_id="q-original",
        saw_hitl_request_event=True,
    )
    assert r == TerminalClassification(status="paused_hitl", clear_pending=False)


def test_prompt_new_pending_paused() -> None:
    r = classify_terminal_status(
        final_pending=_fake_pending("q-new"),
        answered_question_id=None,
        saw_hitl_request_event=True,
    )
    assert r == TerminalClassification(status="paused_hitl", clear_pending=False)
