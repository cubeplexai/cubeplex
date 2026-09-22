"""Persisted Stop can finish paused cleanup after a worker or control channel is lost."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from cubeplex.agents.checkpointer import shared_checkpointer
from cubeplex.models import ConversationExecutionAdmission, User
from cubeplex.streams.hitl_resume import _BEGIN_FINALIZATION_IF_CLAIM_MATCHES_LUA
from cubeplex.streams.recovery import StoppedRunRecovery
from cubeplex.streams.run_events import _active_run_key, _run_meta_key, get_active_run, get_run_meta
from cubeplex.streams.run_manager import RunManager
from tests.e2e import test_admitted_hitl_execution as hitl_fixtures
from tests.e2e import test_admitted_run_execution as run_fixtures
from tests.e2e import test_background_task_reservation as reservation_fixtures
from tests.e2e.test_admitted_hitl_execution import PausedExecution
from tests.e2e.test_conversation_execution_control import service

reservation_context = reservation_fixtures.reservation_context
run_manager = run_fixtures.run_manager
paused_execution = hitl_fixtures.paused_execution


@pytest.mark.parametrize("all_work", [False, True])
@pytest.mark.parametrize("actor_revoked", [False, True])
async def test_durable_stop_recovers_expired_pause_without_restoring_execution_authority(
    db_session: AsyncSession,
    run_manager: RunManager,
    paused_execution: PausedExecution,
    all_work: bool,
    actor_revoked: bool,
) -> None:
    paused = paused_execution
    if all_work:
        await service(db_session).close_generation(
            conversation_id=paused.ctx.conversation_id,
            actor_user_id=paused.ctx.user_id,
            execution_generation=paused.admitted.admission.execution_generation,
            now=datetime.now(UTC),
        )
    else:
        await service(db_session).stop_run(
            conversation_id=paused.ctx.conversation_id,
            run_id=paused.run_id,
            actor_user_id=paused.ctx.user_id,
            now=datetime.now(UTC),
        )
    actor: User | None = None
    if actor_revoked:
        actor = await db_session.get(User, paused.ctx.user_id)
        assert actor is not None and actor.is_active
    await db_session.commit()
    await run_manager._redis.delete(
        _active_run_key(run_manager._key_prefix, paused.ctx.conversation_id),
        _run_meta_key(run_manager._key_prefix, paused.run_id),
    )
    replacement = RunManager(
        app=run_manager._app,
        redis=run_manager._redis,
        key_prefix=run_manager._key_prefix,
        run_event_ttl_seconds=60,
    )
    try:
        if actor is not None:
            actor.is_active = False
            await db_session.commit()
        await replacement.recover_stopped_run(paused.admitted.admission.id)
        await replacement.drain(timeout_seconds=15)
        async with shared_checkpointer() as cp:
            assert await cp.load_pending(paused.ctx.conversation_id) is None
        await db_session.refresh(paused.admitted.admission)
        assert paused.admitted.admission.run_finished_at is not None
        assert paused.provider.call_count == 1
        meta = await get_run_meta(
            replacement._redis, prefix=replacement._key_prefix, run_id=paused.run_id
        )
        assert meta is not None and meta.status == "cancelled"
        assert (
            await get_active_run(
                replacement._redis,
                prefix=replacement._key_prefix,
                conversation_id=paused.ctx.conversation_id,
            )
            is None
        )
    finally:
        await replacement.cancel_all()
        if actor is not None:
            await db_session.rollback()
            actor = await db_session.get(User, paused.ctx.user_id)
            assert actor is not None
            actor.is_active = True
            await db_session.commit()


async def test_recovery_page_uses_persisted_intent_after_restart(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    run_manager: RunManager,
    paused_execution: PausedExecution,
) -> None:
    paused = paused_execution
    await service(db_session).stop_run(
        conversation_id=paused.ctx.conversation_id,
        run_id=paused.run_id,
        actor_user_id=paused.ctx.user_id,
        now=datetime.now(UTC),
    )
    await db_session.commit()
    replacement = RunManager(
        app=run_manager._app,
        redis=run_manager._redis,
        key_prefix=run_manager._key_prefix,
        run_event_ttl_seconds=60,
    )
    recovery = StoppedRunRecovery(session_factory, replacement.recover_stopped_run, batch_size=1)
    # Resume at this page without depending on other tests' older unfinished admissions.
    recovery._after = await db_session.scalar(
        select(func.max(col(ConversationExecutionAdmission.id))).where(
            col(ConversationExecutionAdmission.id) < paused.admitted.admission.id
        )
    )
    try:
        assert await recovery.reconcile_once() == 1
        assert recovery._after == paused.admitted.admission.id
        await replacement.drain(timeout_seconds=15)
        await db_session.refresh(paused.admitted.admission)
        assert paused.admitted.admission.run_finished_at is not None
        assert paused.provider.call_count == 1
    finally:
        await replacement.cancel_all()


async def test_recovery_does_not_cancel_an_authorized_pause(
    run_manager: RunManager, paused_execution: PausedExecution
) -> None:
    paused = paused_execution
    assert not await run_manager.recover_stopped_run(paused.admitted.admission.id)
    async with shared_checkpointer() as cp:
        pending = await cp.load_pending(paused.ctx.conversation_id)
    assert pending is not None and pending[1] == paused.run_id
    assert paused.provider.call_count == 1


@pytest.mark.parametrize("replacement", ["question", "active_run", "terminal"])
async def test_old_recovery_does_not_clear_newer_work_or_rewrite_terminal_state(
    db_session: AsyncSession,
    run_manager: RunManager,
    paused_execution: PausedExecution,
    replacement: str,
) -> None:
    paused = paused_execution
    await service(db_session).stop_run(
        conversation_id=paused.ctx.conversation_id,
        run_id=paused.run_id,
        actor_user_id=paused.ctx.user_id,
        now=datetime.now(UTC),
    )
    await db_session.commit()
    async with shared_checkpointer() as cp:
        pending = await cp.load_pending(paused.ctx.conversation_id)
        assert pending is not None
        if replacement == "question":
            await cp.save_pending_request(
                paused.ctx.conversation_id, pending[0], run_id="newer-run"
            )
        expected_pending = await cp.load_pending(paused.ctx.conversation_id)
    active_key = _active_run_key(run_manager._key_prefix, paused.ctx.conversation_id)
    meta_key = _run_meta_key(run_manager._key_prefix, paused.run_id)
    if replacement == "active_run":
        await run_manager._redis.set(active_key, "newer-run", ex=60)
    elif replacement == "terminal":
        await run_manager._redis.hset(meta_key, mapping={"status": "completed"})
    expected_meta = await run_manager._redis.hgetall(meta_key)
    expected_active = await run_manager._redis.get(active_key)
    assert not await run_manager.recover_stopped_run(paused.admitted.admission.id)
    assert await run_manager._redis.hgetall(meta_key) == expected_meta
    assert await run_manager._redis.get(active_key) == expected_active
    async with shared_checkpointer() as cp:
        assert await cp.load_pending(paused.ctx.conversation_id) == expected_pending
    assert paused.provider.call_count == 1
    await db_session.refresh(paused.admitted.admission)
    assert paused.admitted.admission.run_finished_at is None


async def test_two_recovery_workers_share_one_paused_cleanup_claim(
    db_session: AsyncSession,
    run_manager: RunManager,
    paused_execution: PausedExecution,
) -> None:
    paused = paused_execution
    await service(db_session).stop_run(
        conversation_id=paused.ctx.conversation_id,
        run_id=paused.run_id,
        actor_user_id=paused.ctx.user_id,
        now=datetime.now(UTC),
    )
    await db_session.commit()
    replacement = RunManager(
        app=run_manager._app,
        redis=run_manager._redis,
        key_prefix=run_manager._key_prefix,
        run_event_ttl_seconds=60,
    )
    try:
        await asyncio.gather(
            run_manager.recover_stopped_run(paused.admitted.admission.id),
            replacement.recover_stopped_run(paused.admitted.admission.id),
        )
        await asyncio.gather(
            run_manager.drain(timeout_seconds=15), replacement.drain(timeout_seconds=15)
        )
        await db_session.refresh(paused.admitted.admission)
        assert paused.admitted.admission.run_finished_at is not None
        assert paused.provider.call_count == 1
        async with shared_checkpointer() as cp:
            assert await cp.load_pending(paused.ctx.conversation_id) is None
    finally:
        await replacement.cancel_all()


@pytest.mark.parametrize("commit_lease", [False, True])
async def test_failed_paused_cleanup_retries_only_after_previous_claim_loses_authority(
    db_session: AsyncSession,
    run_manager: RunManager,
    paused_execution: PausedExecution,
    monkeypatch: pytest.MonkeyPatch,
    commit_lease: bool,
) -> None:
    paused = paused_execution
    original_eval = run_manager._redis.eval
    failed = False

    async def fail_finalization(script: str, numkeys: int, *args: Any) -> Any:
        nonlocal failed
        if script == _BEGIN_FINALIZATION_IF_CLAIM_MATCHES_LUA and not failed:
            failed = True
            if commit_lease:
                await original_eval(script, numkeys, *args)
            raise ConnectionError("cleanup worker lost its Redis connection")
        return await original_eval(script, numkeys, *args)

    with monkeypatch.context() as fault:
        fault.setattr(run_manager._redis, "eval", fail_finalization)
        await run_manager.cancel_paused_run(
            conversation_id=paused.ctx.conversation_id, run_id=paused.run_id, ctx=paused.ctx
        )
        await run_manager.drain(timeout_seconds=15)
    assert failed
    await db_session.refresh(paused.admitted.admission)
    assert paused.admitted.admission.run_finished_at is None
    replacement = RunManager(
        app=run_manager._app,
        redis=run_manager._redis,
        key_prefix=run_manager._key_prefix,
        run_event_ttl_seconds=60,
    )
    try:
        assert not await replacement.recover_stopped_run(paused.admitted.admission.id)
        meta_key = _run_meta_key(run_manager._key_prefix, paused.run_id)
        old_claim = await run_manager._redis.hget(meta_key, "claim_token")
        await run_manager._redis.hset(
            meta_key,
            mapping={"last_event_at": (datetime.now(UTC) - timedelta(days=1)).isoformat()},
        )
        if commit_lease:
            assert not await replacement.recover_stopped_run(paused.admitted.admission.id)
            assert await run_manager._redis.hget(meta_key, "claim_token") == old_claim
        await run_manager._redis.hset(meta_key, mapping={"resume_finalizing_until": "0"})
        assert await replacement.recover_stopped_run(paused.admitted.admission.id)
        await replacement.drain(timeout_seconds=15)
        await db_session.refresh(paused.admitted.admission)
        assert paused.admitted.admission.run_finished_at is not None
        assert paused.provider.call_count == 1
        async with shared_checkpointer() as cp:
            assert await cp.load_pending(paused.ctx.conversation_id) is None
    finally:
        await replacement.cancel_all()
