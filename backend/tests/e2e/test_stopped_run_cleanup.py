"""Stopped execution can finish teardown after its HITL question is already gone."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.agents.checkpointer import shared_checkpointer
from cubeplex.streams.hitl_resume import _CLAIM_RESUME_LUA
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


@pytest.mark.parametrize(
    ("action", "failure"),
    [
        ("cancel", "terminal_write"),
        ("cancel", "slot_release"),
        ("answer", "slot_release"),
        ("cancel", "slot_release_reply"),
        ("answer", "slot_release_reply"),
    ],
)
async def test_persisted_stop_finishes_interrupted_teardown_without_replaying_the_model(
    db_session: AsyncSession,
    run_manager: RunManager,
    paused_execution: PausedExecution,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    failure: str,
) -> None:
    paused = paused_execution
    terminal = "cancelled" if action == "cancel" else "completed"
    original_eval = run_manager._redis.eval
    failed = False

    async def interrupt_teardown(script: str, numkeys: int, *args: Any) -> Any:
        nonlocal failed
        if (failure == "terminal_write" and terminal in args) or (
            failure.startswith("slot_release") and script == _CLEAR_ACTIVE_IF_MATCHES_LUA
        ):
            failed = True
            if failure == "slot_release_reply":
                await original_eval(script, numkeys, *args)
            raise ConnectionError("worker cannot finish Redis teardown")
        return await original_eval(script, numkeys, *args)

    with monkeypatch.context() as fault:
        fault.setattr(run_manager._redis, "eval", interrupt_teardown)
        await respond(run_manager, paused, action)
        await run_manager.drain(timeout_seconds=15)
    assert failed
    events_key = _run_events_key(run_manager._key_prefix, paused.run_id)
    prior_events = await run_manager._redis.xrange(events_key)
    async with shared_checkpointer() as cp:
        assert await cp.load_pending(paused.ctx.conversation_id) is None
    receipt = await service(db_session).stop_run(
        conversation_id=paused.ctx.conversation_id,
        run_id=paused.run_id,
        actor_user_id=paused.ctx.user_id,
        now=datetime.now(UTC),
    )
    await db_session.commit()
    assert receipt.cleanup_pending, "interrupted teardown is still pending cleanup"
    await run_manager._redis.hset(
        _run_meta_key(run_manager._key_prefix, paused.run_id),
        mapping={
            "last_event_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "resume_finalizing_until": "0",
        },
    )
    replacement = RunManager(
        app=run_manager._app,
        redis=run_manager._redis,
        key_prefix=run_manager._key_prefix,
        run_event_ttl_seconds=60,
    )
    try:
        reclaimed_released_slot = False

        async def observe_claim(script: str, numkeys: int, *args: Any) -> Any:
            nonlocal reclaimed_released_slot
            result = await original_eval(script, numkeys, *args)
            if failure == "slot_release_reply" and script == _CLAIM_RESUME_LUA and result == "ok":
                reclaimed_released_slot = (
                    await run_manager._redis.get(
                        _active_run_key(run_manager._key_prefix, paused.ctx.conversation_id)
                    )
                    == paused.run_id
                )
            return result

        with monkeypatch.context() as observe:
            observe.setattr(run_manager._redis, "eval", observe_claim)
            assert await replacement.recover_stopped_run(paused.admitted.admission.id)
            await replacement.drain(timeout_seconds=15)
        assert not reclaimed_released_slot, "terminal cleanup must not reoccupy a released slot"
        await db_session.refresh(paused.admitted.admission)
        assert paused.admitted.admission.run_finished_at is not None
        assert paused.provider.call_count == (1 if action == "cancel" else 2)
        meta = await get_run_meta(
            replacement._redis, prefix=replacement._key_prefix, run_id=paused.run_id
        )
        assert meta is not None and meta.status == terminal
        if failure != "terminal_write":
            assert await run_manager._redis.xrange(events_key) == prior_events
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
