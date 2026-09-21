"""Owner fencing and durable Stop protect real tasks across worker attempts."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from cubeplex.models import BackgroundTask, BackgroundTaskEvent, Conversation, SandboxCommand
from cubeplex.models.background_task import TaskStopReason
from cubeplex.sandbox.base import ProcessSnapshot
from cubeplex.services.background_task_lifecycle import ForegroundResultEvidence
from cubeplex.services.background_tasks import BackgroundTaskService
from tests.e2e import test_background_task_reservation as reservation_fixtures
from tests.e2e.test_background_task_reservation import (
    NOW,
    ReservationContext,
    reserve,
    service,
)

reservation_context = reservation_fixtures.reservation_context


async def events(session: AsyncSession, task_id: str) -> list[BackgroundTaskEvent]:
    return list(
        (
            await session.execute(
                select(BackgroundTaskEvent).where(col(BackgroundTaskEvent.task_id) == task_id)
            )
        ).scalars()
    )


async def test_expired_owner_cannot_replace_new_observation(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await reserve(db_session, reservation_context)
    task_id = item.task.id
    old_token = item.task.owner_token
    await db_session.commit()
    moment = NOW + timedelta(seconds=31)
    assert await service(db_session).claim_task(
        task_id=item.task.id,
        owner_token="replacement",
        now=moment,
        owner_until=moment + timedelta(seconds=45),
    )
    await db_session.commit()
    with pytest.raises(ValueError, match="owner"):
        await service(db_session).record_observation(
            task_id=item.task.id,
            owner_token=old_token,
            now=moment,
            snapshot=ProcessSnapshot(status="exited", exit_code=0),
            log_state="complete",
        )
    await db_session.rollback()
    saved = await db_session.get(BackgroundTask, task_id)
    assert saved is not None and saved.owner_token == "replacement"
    assert saved.state == "starting"


async def test_start_once_and_late_receipt_survives_stop_and_takeover(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await reserve(db_session, reservation_context)
    task_id, command_id, token = item.task.id, item.command.id, item.task.owner_token
    await db_session.commit()
    assert await service(db_session).begin_start(task_id=task_id, owner_token=token, now=NOW)
    await db_session.commit()
    assert not await service(db_session).begin_start(task_id=task_id, owner_token=token, now=NOW)
    await service(db_session).request_task_stop(
        task_id=task_id,
        reason=TaskStopReason.user_stop,
        now=NOW,
    )
    moment = NOW + timedelta(seconds=31)
    assert await service(db_session).claim_task(
        task_id=task_id,
        owner_token="replacement",
        now=moment,
        owner_until=moment + timedelta(seconds=45),
    )
    await db_session.commit()
    await service(db_session).register_start_receipt(
        task_id=task_id,
        start_token=token,
        sandbox_instance_id=reservation_context.details.sandbox_instance_id,
        provider_ref="late-original-handle",
        now=moment,
    )
    await db_session.commit()
    saved = await db_session.get(BackgroundTask, task_id)
    command = await db_session.get(SandboxCommand, command_id)
    assert saved is not None and command is not None
    assert command.provider_ref == "late-original-handle"
    assert saved.owner_token == "replacement"
    assert saved.stop_requested_at == NOW
    assert saved.notifications_cancelled_at == NOW
    assert saved.state == "starting"


async def test_single_stop_includes_descendants_but_not_siblings(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    parent = await reserve(db_session, reservation_context)
    child = await reserve(
        db_session,
        reservation_context,
        spec=replace(reservation_context.spec, parent_task_id=parent.task.id),
    )
    sibling = await reserve(db_session, reservation_context)
    await db_session.commit()
    stopped = await service(db_session).request_task_stop(
        task_id=parent.task.id,
        reason=TaskStopReason.user_stop,
        now=NOW,
    )
    await db_session.commit()
    assert set(stopped) == {parent.task.id, child.task.id}
    assert parent.task.state == child.task.state == "starting"
    assert parent.task.notifications_cancelled_at == child.task.notifications_cancelled_at == NOW
    assert sibling.task.stop_requested_at is None
    await service(db_session).record_observation(
        task_id=parent.task.id,
        owner_token=parent.task.owner_token,
        now=NOW,
        snapshot=ProcessSnapshot(status="exited", exit_code=0),
        log_state="complete",
    )
    await service(db_session).handoff_task(
        task_id=parent.task.id,
        owner_token=parent.task.owner_token,
        now=NOW,
    )
    await db_session.commit()
    assert parent.task.state == "succeeded"
    assert await events(db_session, parent.task.id) == []


async def test_background_handoff_and_terminal_observation_have_one_outbox_entry(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await reserve(db_session, reservation_context)
    token = item.task.owner_token
    await service(db_session).handoff_task(task_id=item.task.id, owner_token=token, now=NOW)
    for _ in range(2):
        await service(db_session).record_observation(
            task_id=item.task.id,
            owner_token=token,
            now=NOW,
            snapshot=ProcessSnapshot(status="exited", exit_code=7),
            log_state="retrying",
        )
    await db_session.commit()
    notices = await events(db_session, item.task.id)
    assert item.task.state == "failed" and item.command.exit_code == 7
    assert item.command.log_state == "retrying"
    assert len(notices) == 1 and notices[0].state == "pending"
    assert notices[0].reason == "completion"
    assert notices[0].execution_generation == item.task.execution_generation
    assert notices[0].task_id == item.task.id


async def test_deadline_requests_stop_without_suppressing_result(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await reserve(db_session, reservation_context)
    await service(db_session).request_task_stop(
        task_id=item.task.id,
        reason=TaskStopReason.deadline,
        now=NOW,
    )
    await db_session.commit()
    assert item.task.stop_requested_at == NOW
    assert item.task.notifications_cancelled_at is None
    await service(db_session).handoff_task(
        task_id=item.task.id,
        owner_token=item.task.owner_token,
        now=NOW,
    )
    await service(db_session).record_observation(
        task_id=item.task.id,
        owner_token=item.task.owner_token,
        now=NOW,
        snapshot=ProcessSnapshot(status="killed", exit_code=-9),
        log_state="complete",
    )
    await db_session.commit()
    assert len(await events(db_session, item.task.id)) == 1
    assert item.task.stop_reason == "deadline"


async def test_terminal_fact_does_not_regress_on_later_running_snapshot(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await reserve(db_session, reservation_context)
    for snapshot in (
        ProcessSnapshot(status="exited", exit_code=0),
        ProcessSnapshot(status="running"),
    ):
        await service(db_session).record_observation(
            task_id=item.task.id,
            owner_token=item.task.owner_token,
            now=NOW,
            snapshot=snapshot,
            log_state="retrying",
        )
    await db_session.commit()
    assert item.task.state == "succeeded" and item.command.exit_code == 0
    assert item.task.finished_at == NOW


async def test_closed_generation_blocks_start_but_keeps_late_handle(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await reserve(db_session, reservation_context)
    conversation = await db_session.get(Conversation, reservation_context.conversation_id)
    assert conversation is not None
    conversation.execution_closed_at = NOW
    await db_session.commit()
    assert not await service(db_session).begin_start(
        task_id=item.task.id,
        owner_token=item.task.owner_token,
        now=NOW,
    )
    assert item.command.provider_ref is None


async def test_two_workers_compete_for_one_expired_claim(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
) -> None:
    item = await reserve(db_session, reservation_context)
    task_id = item.task.id
    await db_session.commit()
    assert not await service(db_session).claim_task(
        task_id=task_id, owner_token="early", now=NOW, owner_until=NOW + timedelta(minutes=1)
    )
    await db_session.commit()
    barrier = asyncio.Barrier(2)
    moment = NOW + timedelta(seconds=31)

    async def claim(token: str) -> bool:
        async with session_factory() as session:
            await barrier.wait()
            result = await service(session).claim_task(
                task_id=task_id,
                owner_token=token,
                now=moment,
                owner_until=moment + timedelta(minutes=1),
            )
            await session.commit()
            return result

    assert sorted(await asyncio.gather(claim("worker-a"), claim("worker-b"))) == [False, True]


async def test_stop_discards_unsubmitted_notice_but_keeps_checkpoint_reconciliation(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await reserve(db_session, reservation_context)
    notices = [
        BackgroundTaskEvent(
            org_id=item.task.org_id,
            workspace_id=item.task.workspace_id,
            task_id=item.task.id,
            conversation_id=item.task.conversation_id,
            execution_generation=item.task.execution_generation,
            reason="line",
            dedupe_key=key,
            state=state,
            delivery_attempt_id=attempt,
        )
        for key, state, attempt in (
            ("unsubmitted", "pending", None),
            ("in-flight", "claimed", "attempt-a"),
            ("delivered", "delivered", "attempt-b"),
        )
    ]
    db_session.add_all(notices)
    await db_session.commit()
    await service(db_session).request_task_stop(
        task_id=item.task.id,
        reason=TaskStopReason.user_stop,
        now=NOW,
    )
    await db_session.commit()
    assert [notice.state for notice in notices] == ["discarded", "claimed", "delivered"]
    assert notices[1].delivery_attempt_id == "attempt-a"
    assert item.task.notifications_cancelled_at == NOW


async def test_terminal_state_and_outbox_rollback_together(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await reserve(db_session, reservation_context)
    task_id, token = item.task.id, item.task.owner_token
    await service(db_session).handoff_task(task_id=task_id, owner_token=token, now=NOW)
    await db_session.commit()
    await service(db_session).record_observation(
        task_id=task_id,
        owner_token=token,
        now=NOW,
        snapshot=ProcessSnapshot(status="exited", exit_code=0),
        log_state="complete",
    )
    assert len(await events(db_session, task_id)) == 1
    await db_session.rollback()
    task = await db_session.get(BackgroundTask, task_id)
    assert task is not None and task.state == "starting"
    assert await events(db_session, task_id) == []


async def test_foreground_checkpoint_prevents_duplicate_completion_and_reclaim(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await reserve(db_session, reservation_context)
    token = item.task.owner_token
    await service(db_session).record_observation(
        task_id=item.task.id,
        owner_token=token,
        now=NOW,
        snapshot=ProcessSnapshot(status="exited", exit_code=0),
        log_state="complete",
    )
    with pytest.raises(ValueError, match="checkpoint"):
        await service(db_session).record_foreground_delivery(
            task_id=item.task.id,
            owner_token=token,
            now=NOW,
            evidence=ForegroundResultEvidence(
                run_id="other-run", tool_call_id=item.task.tool_call_id
            ),
        )
    await service(db_session).record_foreground_delivery(
        task_id=item.task.id,
        owner_token=token,
        now=NOW,
        evidence=ForegroundResultEvidence(
            run_id=item.task.originating_run_id,
            tool_call_id=item.task.tool_call_id,
        ),
    )
    await db_session.commit()
    with pytest.raises(ValueError, match="foreground"):
        await service(db_session).handoff_task(task_id=item.task.id, owner_token=token, now=NOW)
    assert await events(db_session, item.task.id) == []
    assert not await service(db_session).claim_task(
        task_id=item.task.id,
        owner_token="later",
        now=NOW + timedelta(minutes=1),
        owner_until=NOW + timedelta(minutes=2),
    )


async def test_only_confirmed_cursor_advances_and_stale_observation_is_rejected(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await reserve(db_session, reservation_context)
    snapshot = ProcessSnapshot(status="running", new_output="line", log_cursor="12")
    await service(db_session).record_observation(
        task_id=item.task.id,
        owner_token=item.task.owner_token,
        now=NOW,
        snapshot=snapshot,
        log_state="retrying",
    )
    assert item.command.log_cursor is None
    await service(db_session).record_observation(
        task_id=item.task.id,
        owner_token=item.task.owner_token,
        now=NOW,
        snapshot=snapshot,
        log_state="pending",
        confirmed_log_cursor="12",
    )
    await db_session.commit()
    with pytest.raises(ValueError, match="cursor"):
        await service(db_session).record_observation(
            task_id=item.task.id,
            owner_token=item.task.owner_token,
            now=NOW,
            snapshot=ProcessSnapshot(status="exited", exit_code=0),
            log_state="complete",
            expected_log_cursor=None,
            confirmed_log_cursor="24",
        )
    assert item.command.log_cursor == "12" and item.task.state == "running"


@pytest.mark.parametrize("scope_field", ["org_id", "workspace_id"])
async def test_lifecycle_writes_cannot_cross_scope(
    db_session: AsyncSession, reservation_context: ReservationContext, scope_field: str
) -> None:
    item = await reserve(db_session, reservation_context)
    await db_session.commit()
    scope = {"org_id": item.task.org_id, "workspace_id": item.task.workspace_id}
    scope[scope_field] = "not-this-scope"
    outsider = BackgroundTaskService(db_session, **scope)
    with pytest.raises(LookupError, match="task not found"):
        await outsider.request_task_stop(
            task_id=item.task.id, reason=TaskStopReason.user_stop, now=NOW
        )
    assert item.task.stop_requested_at is None


async def test_transient_observation_failure_remains_inflight_and_preserves_terminal_fact(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await reserve(db_session, reservation_context)
    token = item.task.owner_token
    await service(db_session).record_observation_failure(
        task_id=item.task.id,
        owner_token=token,
        now=NOW,
        message="provider unavailable",
    )
    assert item.task.state == "unknown" and item.task.finished_at is None
    await service(db_session).record_observation(
        task_id=item.task.id,
        owner_token=token,
        now=NOW,
        snapshot=ProcessSnapshot(status="exited", exit_code=0),
        log_state="complete",
    )
    await service(db_session).record_observation_failure(
        task_id=item.task.id,
        owner_token=token,
        now=NOW,
        message="provider unavailable",
    )
    assert item.task.state == "succeeded" and item.command.exit_code == 0
