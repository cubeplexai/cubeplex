"""``RunManager.cancel_paused_run`` — the cancel-on-paused branch.

Covers the three exceptional outcomes from the claim/load steps plus the
happy path. The transient agent build and the cubepi ``trace`` /
``tracing_context`` calls are stubbed so we focus on:

1. ``ResumeNoPending`` when the DB pending is missing.
2. ``ResumeInFlight`` when ``claim_resume`` reports ALREADY_RUNNING.
3. ``ResumeConflict`` when ``claim_resume`` reports CONFLICT.
4. Happy path: ``agent.abort_pending(reason)`` awaited and
   ``finalize_run_meta_if_claim_matches`` called with ``status="cancelled"``
   and the same claim_token returned by ``claim_resume``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from cubebox.streams.hitl_resume import ClaimResumeOutcome, ClaimResumeResult
from cubebox.streams.run_manager import (
    ResumeConflict,
    ResumeInFlight,
    ResumeNoPending,
    RunContext,
    RunManager,
)

PREFIX = "test_cancel_paused"


def _make_rm() -> RunManager:
    return RunManager(
        app=MagicMock(),
        redis=MagicMock(),
        key_prefix=PREFIX,
        run_event_ttl_seconds=60,
    )


def _ctx() -> RunContext:
    return RunContext(user_id="u1", org_id="o1", workspace_id="w1")


def _patch_checkpointer(monkeypatch: pytest.MonkeyPatch, *, pending: Any) -> AsyncMock:
    """Patch ``init_checkpointer`` to yield a stub with ``load_pending_request``
    returning ``pending``. Returns the AsyncMock for assertion.
    """
    cp = MagicMock()
    load_mock = AsyncMock(return_value=pending)
    cp.load_pending_request = load_mock

    @asynccontextmanager
    async def _fake_cm() -> Any:
        yield cp

    monkeypatch.setattr(
        "cubebox.agents.checkpointer.init_checkpointer",
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
        "cubebox.streams.hitl_resume.claim_resume",
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


async def test_cancel_paused_raises_resume_in_flight_when_claim_already_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``claim_resume`` returns ALREADY_RUNNING → ``ResumeInFlight``."""
    pending = MagicMock()
    pending.question_id = "q1"
    pending.created_at = 1717200000.0
    _patch_checkpointer(monkeypatch, pending=pending)
    _patch_claim_resume(monkeypatch, outcome=ClaimResumeOutcome.ALREADY_RUNNING)
    rm = _make_rm()

    with pytest.raises(ResumeInFlight):
        await rm.cancel_paused_run(
            conversation_id="c1",
            run_id="r1",
            reason="cancelled by user",
            ctx=_ctx(),
        )


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


async def test_cancel_paused_calls_abort_pending_and_finalizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: build agent, call ``abort_pending(reason)``, then
    ``finalize_run_meta_if_claim_matches`` with ``status="cancelled"`` and
    the same claim_token returned by ``claim_resume``.
    """
    pending = MagicMock()
    pending.question_id = "q1"
    pending.created_at = 1717200000.0
    _patch_checkpointer(monkeypatch, pending=pending)
    _patch_claim_resume(monkeypatch, outcome=ClaimResumeOutcome.OK, token="tok-cancel")

    rm = _make_rm()

    # Stub the transient agent build — return an agent whose abort_pending
    # is an AsyncMock and whose subscribe is a no-op. _all_tools / channel
    # are unused.
    fake_agent = MagicMock()
    fake_agent.abort_pending = AsyncMock()
    fake_agent.subscribe = MagicMock()

    async def _fake_build(**_kwargs: Any) -> tuple[Any, list[Any], Any]:
        return fake_agent, [], None

    monkeypatch.setattr(rm, "_build_agent_for_conversation", _fake_build)

    # Stub the cubepi tracing primitives so we don't drag in the tracer.
    from contextlib import contextmanager as _cm

    @_cm
    def _fake_tracing_context(metadata: Any = None) -> Any:
        yield None

    @asynccontextmanager
    async def _fake_trace(_tracer: Any, _agent: Any) -> Any:
        yield None

    monkeypatch.setattr("cubepi.tracing.tracing_context", _fake_tracing_context)
    monkeypatch.setattr("cubepi.tracing.trace", _fake_trace)

    # Stub finalize_run_meta_if_claim_matches so we can capture kwargs.
    finalize_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "cubebox.streams.hitl_resume.finalize_run_meta_if_claim_matches",
        finalize_mock,
    )

    out = await rm.cancel_paused_run(
        conversation_id="c1",
        run_id="r1",
        reason="cancelled by user",
        ctx=_ctx(),
    )
    assert out == "r1"

    # abort_pending was awaited with the reason we passed in.
    fake_agent.abort_pending.assert_awaited_once_with("cancelled by user")

    # finalize was called with status="cancelled" and the same claim_token
    # claim_resume returned.
    finalize_mock.assert_awaited_once()
    kwargs = finalize_mock.await_args.kwargs
    assert kwargs["run_id"] == "r1"
    assert kwargs["claim_token"] == "tok-cancel"
    assert kwargs["status"] == "cancelled"
    assert kwargs["prefix"] == PREFIX
