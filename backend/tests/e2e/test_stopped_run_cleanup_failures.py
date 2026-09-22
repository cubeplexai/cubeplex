"""A historical terminal result is not proof that a recovery attempt finished cleanup."""

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.streams.hitl_resume import (
    _BEGIN_FINALIZATION_IF_CLAIM_MATCHES_LUA,
    ClaimResumeOutcome,
    claim_resume,
)
from cubeplex.streams.run_events import _CLEAR_ACTIVE_IF_MATCHES_LUA, _run_meta_key, get_active_run
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
@pytest.mark.parametrize("failure", ["lease", "release_owner", "expiry_owner"])
async def test_known_terminal_state_does_not_hide_failed_recovery_finalization(
    db_session: AsyncSession,
    run_manager: RunManager,
    paused_execution: PausedExecution,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    failure: str,
) -> None:
    paused = paused_execution
    original_eval = run_manager._redis.eval

    async def fail_release(script: str, numkeys: int, *args: Any) -> Any:
        if script == _CLEAR_ACTIVE_IF_MATCHES_LUA:
            raise ConnectionError("original worker could not release its slot")
        return await original_eval(script, numkeys, *args)

    with monkeypatch.context() as fault:
        fault.setattr(run_manager._redis, "eval", fail_release)
        await respond(run_manager, paused, action)
        await run_manager.drain(timeout_seconds=15)
    await service(db_session).stop_run(
        conversation_id=paused.ctx.conversation_id,
        run_id=paused.run_id,
        actor_user_id=paused.ctx.user_id,
        now=datetime.now(UTC),
    )
    await db_session.commit()
    await run_manager._redis.hset(
        _run_meta_key(run_manager._key_prefix, paused.run_id), "resume_finalizing_until", "0"
    )
    failed = False

    async def fail_recovery(script: str, numkeys: int, *args: Any) -> Any:
        nonlocal failed
        if failure == "lease" and script == _BEGIN_FINALIZATION_IF_CLAIM_MATCHES_LUA:
            failed = True
            raise ConnectionError("recovery could not reserve finalization")
        lose_release = failure == "release_owner" and script == _CLEAR_ACTIVE_IF_MATCHES_LUA
        lose_expiry = failure == "expiry_owner" and script.startswith(
            "if redis.call('HGET', KEYS[1], 'claim_token') ~= ARGV[1] then return 0 end "
        )
        if not failed and (lose_release or lose_expiry):
            failed = True
            await run_manager._redis.hset(
                _run_meta_key(run_manager._key_prefix, paused.run_id),
                "resume_finalizing_until",
                "0",
            )
            successor = await claim_resume(
                run_manager._redis,
                prefix=run_manager._key_prefix,
                conversation_id=paused.ctx.conversation_id,
                expected_run_id=paused.run_id,
                started_at=datetime.now(UTC).isoformat(),
                ttl_seconds=60,
                cleanup_only=True,
            )
            assert successor.outcome == ClaimResumeOutcome.OK
        return await original_eval(script, numkeys, *args)

    with monkeypatch.context() as fault:
        fault.setattr(run_manager._redis, "eval", fail_recovery)
        assert await run_manager.recover_stopped_run(paused.admitted.admission.id)
        await run_manager.drain(timeout_seconds=15)
    assert failed
    await db_session.refresh(paused.admitted.admission)
    assert paused.admitted.admission.run_finished_at is None
    active = await get_active_run(
        run_manager._redis,
        prefix=run_manager._key_prefix,
        conversation_id=paused.ctx.conversation_id,
    )
    assert (active is not None) == (failure != "expiry_owner")
    assert await run_manager.recover_stopped_run(paused.admitted.admission.id)
    await run_manager.drain(timeout_seconds=15)
    await db_session.refresh(paused.admitted.admission)
    assert paused.admitted.admission.run_finished_at is not None
    assert paused.provider.call_count == (2 if action == "answer" else 1)
