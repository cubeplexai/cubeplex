"""Unit tests for runtime status writeback classification (spec §4.4a).

These are the pure, no-DB tests: map a simulated provider exception to the
auth_error / model_not_found / other outcome, and confirm the scheduler
declines to act on non-actionable errors. The DB-touching writeback tests
(real Provider/Model rows + separate-session SQL UPDATEs) live in
``tests/e2e/test_provider_runtime_writeback_e2e.py``.
"""

from __future__ import annotations

import pytest

from cubeplex.llm.runtime_writeback import (
    classify_runtime_error,
    schedule_runtime_status_writeback,
)


class _StatusExc(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


class _Resp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _RespExc(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.response = _Resp(status_code)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_StatusExc("Unauthorized", 401), "auth_error"),
        (_StatusExc("forbidden", 403), "auth_error"),
        (_RespExc("nope", 401), "auth_error"),
        (_StatusExc("Invalid API key provided"), "auth_error"),
        (_StatusExc("authentication failed"), "auth_error"),
        (_StatusExc("model_not_found", 404), "model_not_found"),
        (_StatusExc("The model `gpt-x` does not exist"), "model_not_found"),
        (_RespExc("unknown model", 404), "model_not_found"),
        (_StatusExc("connection reset", 502), "other"),
        (_StatusExc("request timed out"), "other"),
    ],
)
def test_classify_runtime_error(exc: Exception, expected: str) -> None:
    assert classify_runtime_error(exc) == expected


def test_auth_wins_over_model_marker() -> None:
    # A 401 is a provider-credential problem even if a model name appears.
    exc = _StatusExc("model gpt-x: unauthorized — invalid api key", 401)
    assert classify_runtime_error(exc) == "auth_error"


def test_schedule_returns_none_for_non_actionable_error() -> None:
    # A transient/other error must not schedule a writeback task at all.
    task = schedule_runtime_status_writeback(
        org_id="org-x", provider_slug="p", model_id="m", exc=_StatusExc("boom", 500)
    )
    assert task is None
