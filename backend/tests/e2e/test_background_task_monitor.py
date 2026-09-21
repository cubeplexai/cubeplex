"""Monitor output uses the task outbox, with durable cursor and flood boundaries."""

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import BackgroundTask, SandboxCommand
from cubeplex.models.background_task import TaskStopReason
from cubeplex.models.sandbox_command import SandboxCommandKind
from cubeplex.sandbox.base import ProcessSnapshot
from cubeplex.services.background_tasks import TaskReservation
from tests.e2e import test_background_task_reservation as reservation_fixtures
from tests.e2e.test_background_task_lifecycle import events
from tests.e2e.test_background_task_reservation import (
    NOW,
    ReservationContext,
    reserve,
    service,
)

reservation_context = reservation_fixtures.reservation_context


async def monitor(session: AsyncSession, context: ReservationContext) -> TaskReservation:
    item = await reserve(
        session, context, details=replace(context.details, kind=SandboxCommandKind.monitor)
    )
    await service(session).renew_owner(
        task_id=item.task.id,
        owner_token=item.task.owner_token,
        now=NOW,
        owner_until=NOW + timedelta(minutes=10),
    )
    await service(session).handoff_task(
        task_id=item.task.id, owner_token=item.task.owner_token, now=NOW
    )
    await session.commit()
    return item


async def observe(
    session: AsyncSession,
    item: TaskReservation,
    *,
    seconds: int,
    cursor: str,
    output: str = "build updated\n",
    confirmed: bool = True,
    terminal: bool = False,
) -> None:
    await service(session).record_observation(
        task_id=item.task.id,
        owner_token=item.task.owner_token,
        now=NOW + timedelta(seconds=seconds),
        snapshot=ProcessSnapshot(
            status="exited" if terminal else "running",
            exit_code=0 if terminal else None,
            new_output=output,
            log_cursor=cursor,
        ),
        log_state="complete" if terminal else "pending",
        expected_log_cursor=item.command.log_cursor,
        confirmed_log_cursor=cursor if confirmed else None,
    )
    await session.commit()


async def test_only_confirmed_output_creates_one_event_per_cursor(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await monitor(db_session, reservation_context)
    await observe(db_session, item, seconds=0, cursor="10", confirmed=False)
    assert await events(db_session, item.task.id) == []
    assert item.command.log_cursor is None
    await observe(db_session, item, seconds=1, cursor="10")
    await observe(db_session, item, seconds=2, cursor="10")
    notices = await events(db_session, item.task.id)
    assert len(notices) == 1 and notices[0].reason == "line"
    assert notices[0].summary == "build updated"
    assert notices[0].execution_generation == item.task.execution_generation
    assert item.command.log_cursor == "10" and item.command.wake_count == 1


async def test_line_rate_limit_does_not_hide_distinct_later_output(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await monitor(db_session, reservation_context)
    for seconds, cursor in ((0, "1"), (5, "2"), (15, "3")):
        await observe(db_session, item, seconds=seconds, cursor=cursor)
    notices = await events(db_session, item.task.id)
    assert len(notices) == 2
    assert len({notice.dedupe_key for notice in notices}) == 2
    assert item.command.wake_count == 2 and item.command.log_cursor == "3"


async def test_line_limit_preserves_one_truthful_exit_event(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await monitor(db_session, reservation_context)
    for index in range(10):
        await observe(db_session, item, seconds=index * 15, cursor=str(index))
    assert item.command.wake_count == 8 and item.command.line_wakes_disabled
    assert item.task.state == "running" and item.task.stop_requested_at is None
    await observe(db_session, item, seconds=150, cursor="11", terminal=True)
    await observe(db_session, item, seconds=151, cursor="11", terminal=True)
    notices = await events(db_session, item.task.id)
    assert len([n for n in notices if n.reason == "line"]) == 8
    assert len([n for n in notices if n.reason == "exit"]) == 1
    assert item.task.state == "succeeded" and item.command.exit_code == 0


async def test_flood_requests_stop_without_faking_process_exit_or_cancelling_result(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await monitor(db_session, reservation_context)
    for seconds, cursor in ((0, "1"), (15, "2"), (31, "3")):
        await observe(db_session, item, seconds=seconds, cursor=cursor, output="a\nb\nc\nd\n")
    assert item.command.line_wakes_disabled
    assert item.task.stop_reason == "output_flood"
    assert item.task.stop_requested_at == NOW + timedelta(seconds=31)
    assert item.task.state == "running" and item.command.exit_code is None
    assert item.task.notifications_cancelled_at is None
    assert not any(n.reason == "exit" for n in await events(db_session, item.task.id))
    await observe(db_session, item, seconds=32, cursor="4", terminal=True)
    assert item.task.state == "succeeded" and item.command.exit_code == 0
    assert any(n.reason == "exit" for n in await events(db_session, item.task.id))


async def test_confirmed_quiet_poll_breaks_a_sustained_flood(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await monitor(db_session, reservation_context)
    await observe(db_session, item, seconds=0, cursor="1", output="a\nb\nc\nd\n")
    await observe(db_session, item, seconds=30, cursor="1", output="")
    await observe(db_session, item, seconds=31, cursor="2", output="a\nb\nc\nd\n")
    assert item.task.stop_requested_at is None
    assert item.command.flood_started_at == NOW + timedelta(seconds=31)


@pytest.mark.parametrize("stop", ["task", "conversation"])
async def test_stop_blocks_new_line_and_exit_notices(
    db_session: AsyncSession, reservation_context: ReservationContext, stop: str
) -> None:
    from cubeplex.models import Conversation

    item = await monitor(db_session, reservation_context)
    if stop == "task":
        await service(db_session).request_task_stop(
            task_id=item.task.id, reason=TaskStopReason.user_stop, now=NOW
        )
    else:
        conversation = await db_session.get(Conversation, item.task.conversation_id)
        assert conversation is not None
        conversation.execution_closed_at = NOW
        await db_session.flush()
    await observe(db_session, item, seconds=0, cursor="1")
    await observe(db_session, item, seconds=1, cursor="2", terminal=True)
    assert await events(db_session, item.task.id) == []
    assert item.command.log_cursor == "2" and item.task.state == "succeeded"


async def test_rolled_back_cursor_and_notice_recover_together(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await monitor(db_session, reservation_context)
    task_id, command_id, token = item.task.id, item.command.id, item.task.owner_token
    for _ in range(2):
        await service(db_session).record_observation(
            task_id=task_id,
            owner_token=token,
            now=NOW,
            snapshot=ProcessSnapshot(status="running", new_output="matched\n", log_cursor="1"),
            log_state="pending",
            confirmed_log_cursor="1",
        )
        assert len(await events(db_session, task_id)) == 1
        await db_session.rollback()
        assert await events(db_session, task_id) == []
        command = await db_session.get(SandboxCommand, command_id)
        task = await db_session.get(BackgroundTask, task_id)
        assert command is not None and task is not None
        assert command.log_cursor is None and command.wake_count == 0
