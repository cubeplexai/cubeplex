"""Paused Stop dispatches cleanup or cancels a racing answer, never a new model turn.

Real checkpoint, authority and Redis ownership are covered in admitted HITL E2E.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from cubeplex.streams.hitl_resume import ClaimResumeOutcome, ClaimResumeResult
from cubeplex.streams.run_manager import (
    ResumeConflict,
    ResumeNoPending,
    RunContext,
    RunManager,
)

PREFIX = "test_cancel_paused"


def _make_rm() -> RunManager:
    manager = RunManager(
        app=MagicMock(),
        redis=MagicMock(),
        key_prefix=PREFIX,
        run_event_ttl_seconds=60,
    )
    # Durable authority is covered with real Postgres in admitted HITL E2E tests.
    manager._stop_paused_execution = AsyncMock(return_value=None)
    return manager


def _ctx() -> RunContext:
    return RunContext(user_id="u1", org_id="o1", workspace_id="w1", conversation_id="c1")


def _patch_checkpointer(monkeypatch: pytest.MonkeyPatch, *, pending: Any) -> AsyncMock:
    """Patch ``init_checkpointer`` to yield a stub with ``load_pending``
    returning ``pending``. Returns the AsyncMock for assertion.
    """
    cp = MagicMock()
    load_mock = AsyncMock(return_value=(pending, "r1") if pending is not None else None)
    cp.load_pending = load_mock

    @asynccontextmanager
    async def _fake_cm() -> Any:
        yield cp

    monkeypatch.setattr(
        "cubeplex.agents.checkpointer.shared_checkpointer",
        _fake_cm,
    )
    return load_mock


def _patch_claim_resume(
    monkeypatch: pytest.MonkeyPatch,
    *,
    outcome: ClaimResumeOutcome,
    token: str | None = "tok-1",
) -> AsyncMock:
    result = ClaimResumeResult(
        outcome=outcome,
        claim_token=token if outcome == ClaimResumeOutcome.OK else None,
    )
    mock = AsyncMock(return_value=result)
    monkeypatch.setattr(
        "cubeplex.streams.hitl_resume.claim_resume",
        mock,
    )
    return mock


async def test_cancel_paused_returns_no_pending_when_pending_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB has no pending row → ``ResumeNoPending`` (route maps to no_active_run)."""
    _patch_checkpointer(monkeypatch, pending=None)
    rm = _make_rm()

    with pytest.raises(ResumeNoPending):
        await rm.cancel_paused_run(
            conversation_id="c1",
            run_id="r1",
            reason="cancelled by user",
            ctx=_ctx(),
        )


async def test_cancel_paused_cancels_a_resume_that_won_the_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A racing answer does not turn Stop into a rejected request."""
    pending = MagicMock()
    pending.question_id = "q1"
    pending.created_at = 1717200000.0
    _patch_checkpointer(monkeypatch, pending=pending)
    _patch_claim_resume(monkeypatch, outcome=ClaimResumeOutcome.ALREADY_RUNNING)
    rm = _make_rm()

    rm.dispatch_cancel = AsyncMock(return_value="published")
    assert await rm.cancel_paused_run(conversation_id="c1", run_id="r1", ctx=_ctx()) == "r1"
    rm.dispatch_cancel.assert_awaited_once_with("r1")


async def test_cancel_paused_raises_resume_conflict_when_claim_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``claim_resume`` returns CONFLICT → ``ResumeConflict``."""
    pending = MagicMock()
    pending.question_id = "q1"
    pending.created_at = 1717200000.0
    _patch_checkpointer(monkeypatch, pending=pending)
    _patch_claim_resume(monkeypatch, outcome=ClaimResumeOutcome.CONFLICT)
    rm = _make_rm()

    with pytest.raises(ResumeConflict):
        await rm.cancel_paused_run(
            conversation_id="c1",
            run_id="r1",
            reason="cancelled by user",
            ctx=_ctx(),
        )


async def test_cancel_paused_spawns_cleanup_without_a_model_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The claimed question is handed to cleanup, never model execution."""

    pending = MagicMock()
    pending.question_id = "q1"
    pending.created_at = 1717200000.0
    pending.payload = MagicMock()
    pending.payload.kind = "ask"
    _patch_checkpointer(monkeypatch, pending=pending)
    _patch_claim_resume(monkeypatch, outcome=ClaimResumeOutcome.OK, token="tok-cancel")

    rm = _make_rm()

    cleanup_calls: list[dict[str, Any]] = []

    async def _fake_cleanup(**kwargs: Any) -> None:
        cleanup_calls.append(kwargs)

    monkeypatch.setattr(rm, "_execute_cancel_paused_run", _fake_cleanup)
    respond_mock = AsyncMock()
    monkeypatch.setattr(rm, "_execute_respond_run", respond_mock)

    out = await rm.cancel_paused_run(
        conversation_id="c1",
        run_id="r1",
        reason="cancelled by user",
        ctx=_ctx(),
    )
    assert out == "r1"
    await rm.drain(timeout_seconds=1)

    assert len(cleanup_calls) == 1
    kw = cleanup_calls[0]
    assert kw["run_id"] == "r1"
    assert kw["conversation_id"] == "c1"
    assert kw["question_id"] == "q1"
    assert kw["claim_token"] == "tok-cancel"
    assert kw["reason"] == "cancelled by user"
    assert "answer" not in kw
    respond_mock.assert_not_awaited()
