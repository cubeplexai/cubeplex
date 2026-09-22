"""Persisted Stop finishes runs that never crossed the worker-entry boundary."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from cubeloop.providers.faux import FauxProvider
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.services.conversation_execution import RunExecutionBinding, UserMessageIntent
from cubeplex.streams.run_events import (
    RunClaimLost,
    _active_run_key,
    _run_meta_key,
    create_run,
    get_active_run,
    get_run_meta,
)
from cubeplex.streams.run_manager import RunContext, RunManager
from tests.e2e import test_admitted_run_execution as run_fixtures
from tests.e2e import test_background_task_reservation as reservation_fixtures
from tests.e2e.test_admitted_run_execution import cleanup_run_rows
from tests.e2e.test_background_task_reservation import ReservationContext
from tests.e2e.test_conversation_execution_control import actor_id, service, snapshot

reservation_context = reservation_fixtures.reservation_context
run_manager = run_fixtures.run_manager


@pytest.mark.parametrize("all_work", [False, True])
@pytest.mark.parametrize("start_claimed", [False, True])
@pytest.mark.parametrize("redis_expired", [False, True])
async def test_stop_recovery_finishes_run_that_never_entered_worker(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    run_manager: RunManager,
    all_work: bool,
    start_claimed: bool,
    redis_expired: bool,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    conversation_id = reservation_context.conversation_id
    admitted = await service(db_session).admit_user_message(
        conversation_id=conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=str(uuid4()),
        intent=UserMessageIntent(content="stop before worker entry"),
        snapshot=snapshot(),
        now=datetime.now(UTC),
    )
    run_id = admitted.admission.run_id
    assert run_id is not None
    claim_token = "claimed-start" if start_claimed else "redis-only-start"
    if start_claimed:
        assert await service(db_session).claim_run_start(
            admission_id=admitted.admission.id,
            attempt_id=claim_token,
            now=datetime.now(UTC),
        )
    await db_session.commit()
    assert await create_run(
        run_manager._redis,
        prefix=run_manager._key_prefix,
        run_id=run_id,
        conversation_id=conversation_id,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
        ttl_seconds=60,
        claim_token=claim_token,
    )
    if all_work:
        await service(db_session).close_generation(
            conversation_id=conversation_id,
            actor_user_id=actor,
            execution_generation=admitted.admission.execution_generation,
            now=datetime.now(UTC),
        )
    else:
        await service(db_session).stop_run(
            conversation_id=conversation_id,
            run_id=run_id,
            actor_user_id=actor,
            now=datetime.now(UTC),
        )
    await db_session.commit()
    if redis_expired:
        await run_manager._redis.delete(
            _active_run_key(run_manager._key_prefix, conversation_id),
            _run_meta_key(run_manager._key_prefix, run_id),
        )

    replacement = RunManager(
        app=run_manager._app,
        redis=run_manager._redis,
        key_prefix=run_manager._key_prefix,
        run_event_ttl_seconds=60,
    )
    try:
        assert await replacement.recover_stopped_run(admitted.admission.id)
        await replacement.drain(timeout_seconds=15)
        await db_session.refresh(admitted.admission)
        assert admitted.admission.run_started_at is None
        assert admitted.admission.run_finished_at is not None
        meta = await get_run_meta(replacement._redis, prefix=replacement._key_prefix, run_id=run_id)
        assert meta is not None and meta.status == "cancelled"
        assert (
            await get_active_run(
                replacement._redis,
                prefix=replacement._key_prefix,
                conversation_id=conversation_id,
            )
            is None
        )
    finally:
        await replacement.cancel_all()
        await cleanup_run_rows(db_session, conversation_id)


async def test_unstarted_stop_recovery_does_not_replace_a_newer_active_run(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    run_manager: RunManager,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    conversation_id = reservation_context.conversation_id
    admitted = await service(db_session).admit_user_message(
        conversation_id=conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=str(uuid4()),
        intent=UserMessageIntent(content="old unstarted run"),
        snapshot=snapshot(),
        now=datetime.now(UTC),
    )
    run_id = admitted.admission.run_id
    assert run_id is not None
    await service(db_session).stop_run(
        conversation_id=conversation_id,
        run_id=run_id,
        actor_user_id=actor,
        now=datetime.now(UTC),
    )
    await db_session.commit()
    await run_manager._redis.set(
        _active_run_key(run_manager._key_prefix, conversation_id), "newer-run", ex=60
    )

    try:
        assert not await run_manager.recover_stopped_run(admitted.admission.id)
        await db_session.refresh(admitted.admission)
        assert admitted.admission.run_finished_at is None
        assert (
            await run_manager._redis.get(_active_run_key(run_manager._key_prefix, conversation_id))
            == "newer-run"
        )
    finally:
        await cleanup_run_rows(db_session, conversation_id)


async def test_recovered_unstarted_run_rejects_a_late_original_worker(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    run_manager: RunManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    conversation_id = reservation_context.conversation_id
    admitted = await service(db_session).admit_user_message(
        conversation_id=conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=str(uuid4()),
        intent=UserMessageIntent(content="late worker must stay stopped"),
        snapshot=snapshot(),
        now=datetime.now(UTC),
    )
    run_id = admitted.admission.run_id
    assert run_id is not None
    claim_token = "original-worker"
    assert await service(db_session).claim_run_start(
        admission_id=admitted.admission.id,
        attempt_id=claim_token,
        now=datetime.now(UTC),
    )
    await db_session.commit()
    assert await create_run(
        run_manager._redis,
        prefix=run_manager._key_prefix,
        run_id=run_id,
        conversation_id=conversation_id,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
        ttl_seconds=60,
        claim_token=claim_token,
    )
    await service(db_session).stop_run(
        conversation_id=conversation_id,
        run_id=run_id,
        actor_user_id=actor,
        now=datetime.now(UTC),
    )
    await db_session.commit()
    assert await run_manager.recover_stopped_run(admitted.admission.id)
    await run_manager.drain(timeout_seconds=15)

    provider = FauxProvider(provider_id="provider")
    monkeypatch.setattr("cubeplex.llm.builder.build_provider", lambda *args, **kwargs: provider)
    ctx = RunContext(
        user_id=actor,
        org_id=admitted.admission.org_id,
        workspace_id=admitted.admission.workspace_id,
        conversation_id=conversation_id,
        execution=RunExecutionBinding(
            admission_id=admitted.admission.id,
            attempt_id=claim_token,
            start_token=claim_token,
            execution_generation=admitted.admission.execution_generation,
            execution=admitted.execution,
        ),
    )
    try:
        with pytest.raises(RunClaimLost):
            await run_manager._execute_run(
                run_id=run_id,
                conversation_id=conversation_id,
                content="late worker must stay stopped",
                attachments=[],
                ctx=ctx,
                llm_snapshot=snapshot(),
            )
        assert provider.call_count == 0
        await db_session.refresh(admitted.admission)
        assert admitted.admission.run_started_at is None
        assert admitted.admission.run_finished_at is not None
        meta = await get_run_meta(run_manager._redis, prefix=run_manager._key_prefix, run_id=run_id)
        assert meta is not None and meta.status == "cancelled"
    finally:
        await cleanup_run_rows(db_session, conversation_id)
