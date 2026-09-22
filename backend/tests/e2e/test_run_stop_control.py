"""Stopping one run cannot revoke already handed-off work or a later run."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubeplex.models import BackgroundTask, Conversation, ConversationExecutionAdmission
from cubeplex.models.steering_message import SteeringMessage, SteeringMessageState
from cubeplex.sandbox.base import ProcessSnapshot
from cubeplex.services.background_tasks import TaskExecutionRevokedError
from cubeplex.services.conversation_execution import ExecutionRevokedError, UserMessageIntent
from cubeplex.streams.run_events import create_run, get_active_run
from cubeplex.streams.run_manager import RunManager
from tests.e2e import test_admitted_run_execution as run_fixtures
from tests.e2e import test_background_task_reservation as reservation_fixtures
from tests.e2e.test_background_task_lifecycle import events
from tests.e2e.test_background_task_reservation import (
    NOW,
    ReservationContext,
    reserve,
)
from tests.e2e.test_background_task_reservation import (
    service as tasks,
)
from tests.e2e.test_conversation_execution_control import actor_id, service, snapshot

reservation_context = reservation_fixtures.reservation_context
run_manager = run_fixtures.run_manager


async def stop(session: AsyncSession, context: ReservationContext) -> None:
    await service(session).stop_run(
        conversation_id=context.conversation_id,
        run_id=context.spec.originating_run_id,
        actor_user_id=await actor_id(session, context),
        now=NOW,
    )


async def test_stop_before_start_receipt_does_not_invent_finished_cleanup(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    run_manager: RunManager,
) -> None:
    run_id = reservation_context.spec.originating_run_id
    await create_run(
        run_manager._redis,
        prefix=run_manager._key_prefix,
        conversation_id=reservation_context.conversation_id,
        run_id=run_id,
        status="running",
        started_at=NOW.isoformat(),
        claim_token="redis-claimed-before-durable-start",
        ttl_seconds=60,
    )
    receipt = await service(db_session).stop_run(
        conversation_id=reservation_context.conversation_id,
        run_id=run_id,
        actor_user_id=await actor_id(db_session, reservation_context),
        now=NOW,
    )
    await db_session.commit()
    assert receipt.admission.run_start_token is None
    assert receipt.admission.run_finished_at is None
    assert receipt.cleanup_pending
    assert (
        await get_active_run(
            run_manager._redis,
            prefix=run_manager._key_prefix,
            conversation_id=reservation_context.conversation_id,
        )
        is not None
    )


async def test_stopped_run_keeps_its_background_result_and_blocks_new_work(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await reserve(db_session, reservation_context)
    await tasks(db_session).handoff_task(
        task_id=item.task.id, owner_token=item.task.owner_token, now=NOW
    )
    await stop(db_session, reservation_context)
    await db_session.commit()
    assert await tasks(db_session).begin_start(
        task_id=item.task.id, owner_token=item.task.owner_token, now=NOW
    )
    await tasks(db_session).prepare_observation(
        task_id=item.task.id, owner_token=item.task.owner_token, now=NOW
    )
    await tasks(db_session).record_observation(
        task_id=item.task.id,
        owner_token=item.task.owner_token,
        snapshot=ProcessSnapshot(status="exited", exit_code=0),
        log_state="complete",
        now=NOW,
    )
    await db_session.commit()
    assert item.task.stop_requested_at is None and item.task.state == "succeeded"
    assert len(await events(db_session, item.task.id)) == 1
    with pytest.raises(TaskExecutionRevokedError):
        await reserve(db_session, reservation_context)


async def test_run_stop_preserves_late_start_handle_but_forbids_handoff(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await reserve(db_session, reservation_context)
    token = item.task.owner_token
    assert await tasks(db_session).begin_start(task_id=item.task.id, owner_token=token, now=NOW)
    await stop(db_session, reservation_context)
    await db_session.commit()
    await tasks(db_session).register_start_receipt(
        task_id=item.task.id,
        start_token=token,
        sandbox_instance_id=reservation_context.details.sandbox_instance_id,
        provider_ref="late-original-process",
        now=NOW,
    )
    await db_session.commit()
    assert item.command.provider_ref == "late-original-process"
    assert item.task.stop_reason == "run_stop"
    assert not await tasks(db_session).begin_start(task_id=item.task.id, owner_token=token, now=NOW)
    with pytest.raises(ValueError, match="stopped foreground"):
        await tasks(db_session).handoff_task(task_id=item.task.id, owner_token=token, now=NOW)


@pytest.mark.parametrize("stop_first", [True, False])
async def test_run_stop_and_handoff_have_one_durable_order(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
    stop_first: bool,
) -> None:
    item = await reserve(db_session, reservation_context)
    await db_session.commit()
    first_written, second_started = asyncio.Event(), asyncio.Event()

    async def handoff(session: AsyncSession) -> None:
        await tasks(session).handoff_task(
            task_id=item.task.id, owner_token=item.task.owner_token, now=NOW
        )

    async def first() -> None:
        async with session_factory() as session:
            if stop_first:
                await stop(session, reservation_context)
            else:
                await handoff(session)
            first_written.set()
            await second_started.wait()
            await session.commit()

    async def second() -> None:
        await first_written.wait()
        async with session_factory() as session:
            second_started.set()
            if stop_first:
                with pytest.raises(ValueError, match="stopped foreground"):
                    await handoff(session)
            else:
                await stop(session, reservation_context)
            await session.commit()

    async with asyncio.timeout(10):
        await asyncio.gather(first(), second())
    await db_session.refresh(item.task)
    assert (item.task.backgrounded_at is None) == stop_first
    assert (item.task.stop_requested_at is not None) == stop_first
    assert (item.task.notifications_cancelled_at is not None) == stop_first


async def test_reservation_refreshes_cached_admission_after_another_session_stops_run(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
) -> None:
    cached = await db_session.get(ConversationExecutionAdmission, reservation_context.admission_id)
    assert cached is not None and cached.run_stop_requested_at is None
    await db_session.commit()
    async with session_factory() as other:
        await stop(other, reservation_context)
        await other.commit()
    assert cached.run_stop_requested_at is None
    with pytest.raises(TaskExecutionRevokedError):
        await reserve(db_session, reservation_context)


async def test_stopped_run_cannot_start_resume_or_write_post_run_memory(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    admission = await db_session.get(
        ConversationExecutionAdmission, reservation_context.admission_id
    )
    assert admission is not None
    admission.run_start_token = "original-owner"
    admission.run_started_at = NOW
    await db_session.commit()
    await service(db_session).require_run_authority(
        admission_id=admission.id, attempt_id="original-owner"
    )
    await stop(db_session, reservation_context)
    await db_session.commit()
    assert admission.revoked_at is None and admission.run_stop_requested_at == NOW
    for action in (
        service(db_session).require_run_authority,
        service(db_session).require_reflection_authority,
    ):
        with pytest.raises(ExecutionRevokedError):
            await action(admission_id=admission.id, attempt_id="original-owner")
    with pytest.raises(ExecutionRevokedError):
        await service(db_session).resolve_run_continuation(
            conversation_id=reservation_context.conversation_id,
            run_id=reservation_context.spec.originating_run_id,
            responding_user_id=admission.actor_user_id,
        )
    with pytest.raises(ExecutionRevokedError):
        await service(db_session).claim_run_start(
            admission_id=admission.id, attempt_id="replacement", now=NOW
        )


async def test_run_stop_retry_does_not_stop_a_later_run_in_the_same_generation(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    await stop(db_session, reservation_context)
    admitted = await service(db_session).admit_user_message(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=await actor_id(db_session, reservation_context),
        namespace="web",
        source_id="independent-later-input",
        intent=UserMessageIntent(content="new work"),
        snapshot=snapshot(),
        now=NOW + timedelta(seconds=1),
    )
    assert admitted.admission.run_id is not None
    context = replace(
        reservation_context,
        admission_id=admitted.admission.id,
        spec=replace(reservation_context.spec, originating_run_id=admitted.admission.run_id),
    )
    later = await reserve(db_session, context)
    await stop(db_session, reservation_context)
    await db_session.commit()
    assert later.task.stop_requested_at is None
    assert admitted.admission.run_stop_requested_at is None
    assert admitted.admission.execution_generation == 0


async def test_run_stop_rollback_preserves_foreground_and_admission_authority(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await reserve(db_session, reservation_context)
    task_id = item.task.id
    await db_session.commit()
    await stop(db_session, reservation_context)
    await db_session.rollback()
    task = await db_session.get(BackgroundTask, task_id)
    admission = await db_session.get(
        ConversationExecutionAdmission, reservation_context.admission_id
    )
    conversation = await db_session.get(Conversation, reservation_context.conversation_id)
    assert task is not None and task.stop_requested_at is None
    assert admission is not None and admission.run_stop_requested_at is None
    assert conversation is not None and conversation.execution_closed_at is None


@pytest.mark.parametrize("stop_all", [False, True])
async def test_stop_cancels_only_targeted_user_inputs_and_keeps_unsettled_claims(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    stop_all: bool,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    rows = [
        SteeringMessage(
            org_id=service(db_session).org_id,
            workspace_id=service(db_session).workspace_id,
            conversation_id=reservation_context.conversation_id,
            run_id=reservation_context.spec.originating_run_id if index < 4 else "other-run",
            client_steer_id=f"input-{index}",
            content="input",
            sender_user_id=actor,
            hitl_question_id="question",
            source_kind="background_task" if index == 3 else "user_message",
            execution_generation=1 if index == 5 else 0,
            state=state,
            delivery_owner="inflight-owner" if index == 1 else None,
        )
        for index, state in enumerate(
            (
                SteeringMessageState.queued,
                SteeringMessageState.dispatched,
                SteeringMessageState.injected,
                SteeringMessageState.queued,
                SteeringMessageState.queued,
                SteeringMessageState.queued,
            )
        )
    ]
    db_session.add_all(rows)
    await db_session.commit()
    if stop_all:
        receipt = await service(db_session).close_generation(
            conversation_id=reservation_context.conversation_id,
            actor_user_id=actor,
            execution_generation=0,
            now=NOW,
        )
    else:
        receipt = await service(db_session).stop_run(
            conversation_id=reservation_context.conversation_id,
            actor_user_id=actor,
            run_id=reservation_context.spec.originating_run_id,
            now=NOW,
        )
    await db_session.commit()
    assert receipt.cleanup_pending
    assert [row.state.value for row in rows] == [
        "cancelled",
        "cancel_requested",
        "injected",
        "queued",
        "cancelled" if stop_all else "queued",
        "queued",
    ]
    assert rows[1].delivery_owner == "inflight-owner"
