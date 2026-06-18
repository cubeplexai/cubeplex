"""``RunManager.start_run`` refuses to start a new turn on a paused conversation.

Two guards, exercised independently here:

1. Redis-side: ``get_active_run`` reports ``status == "paused_hitl"`` →
   raise "already has an active run". This covers the normal path where
   the worker that paused the conversation is still alive (or another
   worker can see its meta hash).
2. DB-side: Redis has no active row (meta aged out, or was never written
   because the worker crashed between ``save_pending_request`` and the
   ``update_run_meta(status='paused_hitl')`` write). The new guard
   consults ``PostgresCheckpointer.load_pending`` and refuses
   when a pending HITL request still lives in the DB.

The happy path — no active row, no DB pending — must still spawn the
background task and return a run_id.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest

from cubebox.streams.run_events import create_run, update_run_meta
from cubebox.streams.run_manager import RunContext, RunManager

PREFIX = "test_start_run_paused"


def _make_rm(redis: Any) -> RunManager:
    return RunManager(
        app=MagicMock(),
        redis=redis,
        key_prefix=PREFIX,
        run_event_ttl_seconds=60,
    )


def _ctx() -> RunContext:
    return RunContext(user_id="u1", org_id="o1", workspace_id="w1", conversation_id="c1")


@pytest.fixture
def redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def stub_no_db_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``init_checkpointer`` so ``load_pending`` returns None.

    Used by tests that should NOT raise on the DB-pending guard. The guard
    only runs on the conflict branch (when ``create_run`` returns None),
    but we patch unconditionally to keep tests hermetic against any
    accidental DB connection attempt.
    """
    cp = MagicMock()
    cp.load_pending = AsyncMock(return_value=None)

    @asynccontextmanager
    async def _fake_cm() -> Any:
        yield cp

    monkeypatch.setattr(
        "cubebox.agents.checkpointer.init_checkpointer",
        _fake_cm,
    )


async def test_start_run_rejects_paused_hitl_status(
    redis: fakeredis.aioredis.FakeRedis,
    stub_no_db_pending: None,
) -> None:
    """Existing active run in ``paused_hitl`` → new turn refused with 'already has'."""
    created = await create_run(
        redis,
        prefix=PREFIX,
        run_id="r1",
        conversation_id="c1",
        status="running",
        started_at="2026-06-02T00:00:00+00:00",
        user_message="hi",
        ttl_seconds=60,
    )
    assert created is not None
    paused = await update_run_meta(redis, prefix=PREFIX, run_id="r1", status="paused_hitl")
    assert paused is not None and paused.status == "paused_hitl"

    rm = _make_rm(redis)
    with pytest.raises(RuntimeError, match="already has an active run"):
        await rm.start_run(
            conversation_id="c1",
            content="follow up",
            ctx=_ctx(),
        )


async def test_start_run_rejects_when_db_pending_present(
    redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker-crash window: no Redis active row, but DB pending → refused.

    Simulates the gap where ``save_pending_request`` landed but the
    worker died before the ``update_run_meta(status='paused_hitl')``
    write — so by the time the user re-submits, Redis has no live row
    for the conversation (TTL aged out, or never written) yet the DB
    pending lingers. ``start_run`` must refuse with a 'pending HITL
    request' message that names the question_id.

    Drives the branch by monkey-patching ``create_run`` to return None
    (conflict) and ``get_active_run`` to return None (no meta hash) —
    the exact post-crash shape the guard exists to catch.
    """
    pending = MagicMock()
    pending.question_id = "q_abc"

    cp = MagicMock()
    cp.load_pending = AsyncMock(return_value=(pending, "old_run_1"))

    @asynccontextmanager
    async def _fake_cm() -> Any:
        yield cp

    monkeypatch.setattr(
        "cubebox.agents.checkpointer.init_checkpointer",
        _fake_cm,
    )

    async def _create_run_conflict(*_: Any, **__: Any) -> None:
        return None

    async def _get_active_none(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(
        "cubebox.streams.run_manager.create_run",
        _create_run_conflict,
    )
    monkeypatch.setattr(
        "cubebox.streams.run_manager.get_active_run",
        _get_active_none,
    )

    rm = _make_rm(redis)
    with pytest.raises(RuntimeError, match="pending HITL request") as ei:
        await rm.start_run(
            conversation_id="c1",
            content="follow up",
            ctx=_ctx(),
        )
    assert "q_abc" in str(ei.value)
    cp.load_pending.assert_awaited_once_with("c1")


async def test_start_run_succeeds_when_no_pending(
    redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clean state → ``start_run`` returns a run_id and spawns a task.

    We stub ``_execute_run`` to a no-op so the test doesn't try to spin
    up an actual cubepi agent, and cancel the resulting task before
    exiting to avoid event-loop warnings.
    """
    cp = MagicMock()
    cp.load_pending = AsyncMock(return_value=None)

    @asynccontextmanager
    async def _fake_cm() -> Any:
        yield cp

    monkeypatch.setattr(
        "cubebox.agents.checkpointer.init_checkpointer",
        _fake_cm,
    )

    async def _noop_execute(**_: Any) -> None:
        return None

    rm = _make_rm(redis)
    monkeypatch.setattr(rm, "_execute_run", _noop_execute)

    run_id = await rm.start_run(
        conversation_id="c_clean",
        content="hello",
        ctx=_ctx(),
    )
    assert isinstance(run_id, str) and run_id

    # Up-front DB-pending guard runs unconditionally — the durability
    # claim depends on it (a TTL-expired Redis lock could otherwise let
    # a new turn slip past while DB pending lingers). One read per start.
    cp.load_pending.assert_awaited_once()

    # Drain the spawned task before the loop closes.
    task = rm._tasks.get(run_id)
    assert task is not None
    await task
