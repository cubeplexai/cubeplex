"""``RunManager.resume_run_with_answer`` — the paused-HITL resume entrypoint.

Exercises the four error branches + the happy path. The respond task and
``claim_resume`` Lua call are stubbed; we only care that the method:

1. Loads DB pending and rejects when absent (``ResumeNoPending``).
2. Rejects when the answer's ``question_id`` doesn't match the pending
   (``ResumeStaleAnswer``).
3. Translates ``claim_resume`` outcomes into ``ResumeInFlight`` /
   ``ResumeConflict``.
4. On OK, spawns ``_execute_respond_run`` with the correct kwargs and
   returns the same ``run_id``.
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
    ResumeStaleAnswer,
    RunContext,
    RunManager,
)

PREFIX = "test_resume_rwa"


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


async def test_resume_returns_404_when_no_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB has no pending row → ``ResumeNoPending`` (route maps to 404)."""
    _patch_checkpointer(monkeypatch, pending=None)
    rm = _make_rm()

    with pytest.raises(ResumeNoPending):
        await rm.resume_run_with_answer(
            conversation_id="c1",
            run_id="r1",
            question_id="q1",
            answer={"foo": "bar"},
            ctx=_ctx(),
        )


async def test_resume_returns_409_on_qid_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pending has a different question_id → ``ResumeStaleAnswer``."""
    pending = MagicMock()
    pending.question_id = "q-actual"
    pending.created_at = 1717200000.0
    _patch_checkpointer(monkeypatch, pending=pending)
    rm = _make_rm()

    with pytest.raises(ResumeStaleAnswer):
        await rm.resume_run_with_answer(
            conversation_id="c1",
            run_id="r1",
            question_id="q-wrong",
            answer={"foo": "bar"},
            ctx=_ctx(),
        )


async def test_resume_returns_409_on_already_running(
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
        await rm.resume_run_with_answer(
            conversation_id="c1",
            run_id="r1",
            question_id="q1",
            answer={"x": 1},
            ctx=_ctx(),
        )


async def test_resume_returns_409_on_conflict(
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
        await rm.resume_run_with_answer(
            conversation_id="c1",
            run_id="r1",
            question_id="q1",
            answer={"x": 1},
            ctx=_ctx(),
        )


async def test_resume_happy_path_spawns_respond_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: spawns ``_execute_respond_run`` with the right kwargs and
    echoes the same ``run_id`` back. Avoids dragging in the real agent build
    by stubbing ``_execute_respond_run`` to a no-op.
    """
    pending = MagicMock()
    pending.question_id = "q1"
    pending.created_at = 1717200000.0
    _patch_checkpointer(monkeypatch, pending=pending)
    claim_mock = _patch_claim_resume(monkeypatch, outcome=ClaimResumeOutcome.OK, token="tok-abc")

    rm = _make_rm()
    captured: dict[str, Any] = {}

    async def _noop_respond(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(rm, "_execute_respond_run", _noop_respond)

    out = await rm.resume_run_with_answer(
        conversation_id="c1",
        run_id="r1",
        question_id="q1",
        answer={"x": 1},
        ctx=_ctx(),
    )
    assert out == "r1"

    # claim_resume was called once with the expected_run_id we passed in.
    claim_mock.assert_awaited_once()
    kwargs = claim_mock.await_args.kwargs
    assert kwargs["conversation_id"] == "c1"
    assert kwargs["expected_run_id"] == "r1"
    assert isinstance(kwargs["started_at"], str)  # iso-formatted from created_at
    assert kwargs["ttl_seconds"] == 60

    # The spawned task is tracked and, when it finishes, the kwargs match
    # what we passed in.
    task = rm._tasks.get("r1")
    assert task is not None
    await task
    assert captured["run_id"] == "r1"
    assert captured["conversation_id"] == "c1"
    assert captured["question_id"] == "q1"
    assert captured["answer"] == {"x": 1}
    assert captured["claim_token"] == "tok-abc"
    assert captured["ctx"].user_id == "u1"
