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

from cubeplex.streams.hitl_resume import ClaimResumeOutcome, ClaimResumeResult
from cubeplex.streams.run_manager import (
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
    return RunContext(user_id="u1", org_id="o1", workspace_id="w1", conversation_id="c1")


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


async def test_cancel_paused_spawns_respond_with_cancel_marker_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path for ask_user cancel: synthesise a cancel-flavoured answer
    and spawn ``_execute_respond_run`` (same pipeline as a normal answer).
    The synthesised answer carries ``_cancelled`` + ``_reason`` so the
    model receiving the tool_result can respond contextually instead of
    seeing a cold "Conversation aborted" terminal write."""
    import asyncio as _asyncio

    pending = MagicMock()
    pending.question_id = "q1"
    pending.created_at = 1717200000.0
    pending.payload = MagicMock()
    pending.payload.kind = "ask"
    q1 = MagicMock()
    q1.key = "color"
    q2 = MagicMock()
    q2.key = "size"
    pending.payload.questions = [q1, q2]
    _patch_checkpointer(monkeypatch, pending=pending)
    _patch_claim_resume(monkeypatch, outcome=ClaimResumeOutcome.OK, token="tok-cancel")

    rm = _make_rm()

    respond_calls: list[dict[str, Any]] = []

    async def _fake_respond(**kwargs: Any) -> None:
        respond_calls.append(kwargs)

    monkeypatch.setattr(rm, "_execute_respond_run", _fake_respond)

    out = await rm.cancel_paused_run(
        conversation_id="c1",
        run_id="r1",
        reason="cancelled by user",
        ctx=_ctx(),
    )
    assert out == "r1"
    await _asyncio.sleep(0)
    await _asyncio.sleep(0)

    assert len(respond_calls) == 1
    kw = respond_calls[0]
    assert kw["run_id"] == "r1"
    assert kw["conversation_id"] == "c1"
    assert kw["question_id"] == "q1"
    assert kw["claim_token"] == "tok-cancel"
    answer = kw["answer"]
    assert answer["_cancelled"] is True
    assert answer["_reason"] == "cancelled by user"
    assert answer["color"] == "[user cancelled this question]"
    assert answer["size"] == "[user cancelled this question]"


async def test_cancel_paused_spawns_respond_with_cancel_marker_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """confirm-kind cancel: synthesised answer has ``approved=False`` (an
    explicit deny) plus the cancel marker so the model can distinguish a
    deliberate deny from a cancel-via-abandon."""
    import asyncio as _asyncio

    pending = MagicMock()
    pending.question_id = "q2"
    pending.created_at = 1717200000.0
    pending.payload = MagicMock()
    pending.payload.kind = "confirm"
    _patch_checkpointer(monkeypatch, pending=pending)
    _patch_claim_resume(monkeypatch, outcome=ClaimResumeOutcome.OK, token="tok-cancel")

    rm = _make_rm()

    respond_calls: list[dict[str, Any]] = []

    async def _fake_respond(**kwargs: Any) -> None:
        respond_calls.append(kwargs)

    monkeypatch.setattr(rm, "_execute_respond_run", _fake_respond)

    await rm.cancel_paused_run(
        conversation_id="c2",
        run_id="r2",
        reason="cancelled by user",
        ctx=_ctx(),
    )
    await _asyncio.sleep(0)
    await _asyncio.sleep(0)

    assert len(respond_calls) == 1
    answer = respond_calls[0]["answer"]
    assert answer == {
        "approved": False,
        "_cancelled": True,
        "_reason": "cancelled by user",
    }


async def test_build_cancel_answer_unknown_kind_falls_back_to_marker_only() -> None:
    """If the payload doesn't expose a recognised ``kind`` (e.g. a future
    HITL type), still return SOMETHING the model can interpret —
    just the cancel marker, no per-question keys."""
    from cubeplex.streams.run_manager import _build_cancel_answer

    payload = MagicMock()
    payload.kind = "future_unknown_kind"

    out = _build_cancel_answer(payload, "cancelled by user")
    assert out == {"_cancelled": True, "_reason": "cancelled by user"}
