"""A durable terminal outcome lets stopped cleanup survive expired Redis metadata."""

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.agents.checkpointer import shared_checkpointer
from cubeplex.services.conversation_execution import ConversationExecutionService
from cubeplex.streams.run_events import (
    _CLEAR_ACTIVE_IF_MATCHES_LUA,
    _active_run_key,
    _run_events_key,
    _run_meta_key,
    get_active_run,
    get_run_meta,
)
from cubeplex.streams.run_manager import RunManager
from tests.e2e import test_admitted_hitl_execution as hitl_fixtures
from tests.e2e import test_admitted_run_execution as run_fixtures
from tests.e2e import test_background_task_reservation as reservation_fixtures
from tests.e2e.test_admitted_hitl_execution import PausedExecution, respond
from tests.e2e.test_conversation_execution_control import service

reservation_context = reservation_fixtures.reservation_context
run_manager = run_fixtures.run_manager
paused_execution = hitl_fixtures.paused_execution


@pytest.mark.parametrize("action", ["answer", "cancel"])
async def test_stopped_cleanup_recovers_outcome_after_redis_metadata_expires(
    db_session: AsyncSession,
    run_manager: RunManager,
    paused_execution: PausedExecution,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    paused = paused_execution
    terminal = "completed" if action == "answer" else "cancelled"
    original_eval = run_manager._redis.eval

    async def fail_release(script: str, numkeys: int, *args: Any) -> Any:
        if script == _CLEAR_ACTIVE_IF_MATCHES_LUA:
            raise ConnectionError("terminal result survived but cleanup did not")
        return await original_eval(script, numkeys, *args)

    with monkeypatch.context() as fault:
        fault.setattr(run_manager._redis, "eval", fail_release)
        await respond(run_manager, paused, action)
        await run_manager.drain(timeout_seconds=15)
    prior_events = await run_manager._redis.xrange(
        _run_events_key(run_manager._key_prefix, paused.run_id)
    )
    await db_session.refresh(paused.admitted.admission)
    assert paused.admitted.admission.run_finished_at is None
    assert paused.admitted.admission.run_terminal_status == terminal
    await service(db_session).stop_run(
        conversation_id=paused.ctx.conversation_id,
        run_id=paused.run_id,
        actor_user_id=paused.ctx.user_id,
        now=datetime.now(UTC),
    )
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
        assert await replacement.recover_stopped_run(paused.admitted.admission.id)
        await replacement.drain(timeout_seconds=15)
        await db_session.refresh(paused.admitted.admission)
        assert paused.admitted.admission.run_finished_at is not None
        assert paused.provider.call_count == (2 if action == "answer" else 1)
        meta = await get_run_meta(
            replacement._redis, prefix=replacement._key_prefix, run_id=paused.run_id
        )
        assert meta is not None and meta.status == terminal
        assert (
            await get_active_run(
                replacement._redis,
                prefix=replacement._key_prefix,
                conversation_id=paused.ctx.conversation_id,
            )
            is None
        )
        assert (
            await replacement._redis.xrange(_run_events_key(replacement._key_prefix, paused.run_id))
            == prior_events
        )
    finally:
        await replacement.cancel_all()


@pytest.mark.parametrize("action", ["answer", "cancel"])
async def test_terminal_outcome_write_failure_keeps_redis_for_recovery(
    db_session: AsyncSession,
    run_manager: RunManager,
    paused_execution: PausedExecution,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    paused = paused_execution
    terminal = "completed" if action == "answer" else "cancelled"

    async def fail_outcome(*args: Any, **kwargs: Any) -> bool:
        raise ConnectionError("terminal outcome database write failed")

    with monkeypatch.context() as fault:
        fault.setattr(
            ConversationExecutionService,
            "record_run_terminal_outcome",
            fail_outcome,
        )
        await respond(run_manager, paused, action)
        await run_manager.drain(timeout_seconds=15)
    await db_session.refresh(paused.admitted.admission)
    assert paused.admitted.admission.run_terminal_status is None
    assert paused.admitted.admission.run_finished_at is None
    meta = await get_run_meta(
        run_manager._redis, prefix=run_manager._key_prefix, run_id=paused.run_id
    )
    assert meta is not None and meta.status == terminal
    assert (
        await get_active_run(
            run_manager._redis,
            prefix=run_manager._key_prefix,
            conversation_id=paused.ctx.conversation_id,
        )
        is not None
    )
    # The failed worker still owns a finalization lease. A replacement may
    # recover only after that lease expires; forcing it here models the next
    # scanner pass after the crashed owner can no longer make progress.
    await run_manager._redis.hset(
        _run_meta_key(run_manager._key_prefix, paused.run_id),
        "resume_finalizing_until",
        "0",
    )
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
        assert await replacement.recover_stopped_run(paused.admitted.admission.id)
        await replacement.drain(timeout_seconds=15)
        await db_session.refresh(paused.admitted.admission)
        assert paused.admitted.admission.run_terminal_status == terminal
        assert paused.admitted.admission.run_finished_at is not None
        assert paused.provider.call_count == (2 if action == "answer" else 1)
    finally:
        await replacement.cancel_all()


async def test_recovery_refuses_conflicting_durable_and_redis_outcomes(
    db_session: AsyncSession,
    run_manager: RunManager,
    paused_execution: PausedExecution,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paused = paused_execution
    original_eval = run_manager._redis.eval

    async def fail_release(script: str, numkeys: int, *args: Any) -> Any:
        if script == _CLEAR_ACTIVE_IF_MATCHES_LUA:
            raise ConnectionError("leave terminal cleanup pending")
        return await original_eval(script, numkeys, *args)

    with monkeypatch.context() as fault:
        fault.setattr(run_manager._redis, "eval", fail_release)
        await respond(run_manager, paused, "answer")
        await run_manager.drain(timeout_seconds=15)
    await service(db_session).stop_run(
        conversation_id=paused.ctx.conversation_id,
        run_id=paused.run_id,
        actor_user_id=paused.ctx.user_id,
        now=datetime.now(UTC),
    )
    await db_session.commit()
    await run_manager._redis.hset(
        _run_meta_key(run_manager._key_prefix, paused.run_id), "status", "cancelled"
    )

    assert not await run_manager.recover_stopped_run(paused.admitted.admission.id)
    await db_session.refresh(paused.admitted.admission)
    assert paused.admitted.admission.run_terminal_status == "completed"
    assert paused.admitted.admission.run_finished_at is None


@pytest.mark.parametrize("redis_expired", [False, True])
async def test_stopped_cleanup_reclaims_terminal_resume_with_retained_pending(
    db_session: AsyncSession,
    run_manager: RunManager,
    paused_execution: PausedExecution,
    monkeypatch: pytest.MonkeyPatch,
    redis_expired: bool,
) -> None:
    paused = paused_execution

    async def fail_resume(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("resume failed before the pending answer was consumed")

    monkeypatch.setattr(run_manager, "_build_agent_for_conversation", fail_resume)
    await respond(run_manager, paused, "answer")
    await run_manager.drain(timeout_seconds=15)

    await db_session.refresh(paused.admitted.admission)
    assert paused.admitted.admission.run_terminal_status == "errored"
    assert paused.admitted.admission.run_finished_at is None
    meta = await get_run_meta(
        run_manager._redis, prefix=run_manager._key_prefix, run_id=paused.run_id
    )
    assert meta is not None and meta.status == "errored"
    async with shared_checkpointer() as cp:
        pending = await cp.load_pending(paused.ctx.conversation_id)
    assert pending is not None and pending[0].question_id == paused.question_id
    if redis_expired:
        await run_manager._redis.delete(
            _active_run_key(run_manager._key_prefix, paused.ctx.conversation_id),
            _run_meta_key(run_manager._key_prefix, paused.run_id),
        )

    await service(db_session).stop_run(
        conversation_id=paused.ctx.conversation_id,
        run_id=paused.run_id,
        actor_user_id=paused.ctx.user_id,
        now=datetime.now(UTC),
    )
    await db_session.commit()

    assert await run_manager.recover_stopped_run(paused.admitted.admission.id)
    await run_manager.drain(timeout_seconds=15)
    await db_session.refresh(paused.admitted.admission)
    assert paused.admitted.admission.run_terminal_status == "errored"
    assert paused.admitted.admission.run_finished_at is not None
    assert paused.provider.call_count == 1
    meta = await get_run_meta(
        run_manager._redis, prefix=run_manager._key_prefix, run_id=paused.run_id
    )
    assert meta is not None and meta.status == "errored"
    async with shared_checkpointer() as cp:
        assert await cp.load_pending(paused.ctx.conversation_id) is None


async def test_terminal_resume_with_pending_requires_a_durable_outcome(
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
    await run_manager._redis.hset(
        _run_meta_key(run_manager._key_prefix, paused.run_id), "status", "errored"
    )

    assert not await run_manager.recover_stopped_run(paused.admitted.admission.id)
    await db_session.refresh(paused.admitted.admission)
    assert paused.admitted.admission.run_terminal_status is None
    assert paused.admitted.admission.run_finished_at is None
    async with shared_checkpointer() as cp:
        pending = await cp.load_pending(paused.ctx.conversation_id)
    assert pending is not None and pending[0].question_id == paused.question_id
