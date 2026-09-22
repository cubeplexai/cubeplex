"""A monitor waits once; output and retries never create additional wakeups."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubeplex.models import BackgroundTask, Conversation, SandboxCommand
from cubeplex.models.background_task import TaskStopReason
from cubeplex.models.sandbox_command import SandboxCommandKind
from cubeplex.sandbox.base import ProcessSnapshot
from cubeplex.services.background_task_lifecycle import LogState
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


async def monitor(
    session: AsyncSession, context: ReservationContext, *, expires: bool = False
) -> TaskReservation:
    item = await reserve(
        session,
        context,
        details=replace(
            context.details,
            kind=SandboxCommandKind.monitor,
            monitor_deadline_at=NOW + timedelta(seconds=10) if expires else None,
        ),
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
    exit_code: int = 0,
    log_state: LogState | None = None,
) -> None:
    await service(session).record_observation(
        task_id=item.task.id,
        owner_token=item.task.owner_token,
        now=NOW + timedelta(seconds=seconds),
        snapshot=ProcessSnapshot(
            status="exited" if terminal else "running",
            exit_code=exit_code if terminal else None,
            new_output=output,
            log_cursor=cursor,
        ),
        log_state=log_state or ("complete" if terminal else "pending"),
        expected_log_cursor=item.command.log_cursor,
        confirmed_log_cursor=cursor if confirmed else None,
    )
    await session.commit()


async def test_monitor_output_is_log_only_until_the_script_finishes(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await monitor(db_session, reservation_context)
    for seconds, cursor in ((0, "1"), (15, "2"), (31, "3")):
        await observe(db_session, item, seconds=seconds, cursor=cursor, output="a\nb\nc\nd\n")
    assert await events(db_session, item.task.id) == []
    assert item.task.stop_requested_at is None
    assert item.command.log_cursor == "3"
    assert item.command.monitor_outcome is None
    assert item.task.result_readiness == "pending"


@pytest.mark.parametrize(("exit_code", "outcome"), [(0, "matched"), (7, "failed")])
@pytest.mark.parametrize("expires", [True, False])
async def test_terminal_monitor_has_one_result_including_persistent(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    exit_code: int,
    outcome: str,
    expires: bool,
) -> None:
    item = await monitor(db_session, reservation_context, expires=expires)
    await observe(db_session, item, seconds=0, cursor="1", confirmed=False)
    assert item.command.log_cursor is None
    await observe(db_session, item, seconds=1, cursor="1")
    await observe(db_session, item, seconds=2, cursor="2", terminal=True, exit_code=exit_code)
    first = (await events(db_session, item.task.id))[0]
    await observe(db_session, item, seconds=3, cursor="2", terminal=True, exit_code=exit_code)
    notices = await events(db_session, item.task.id)
    assert [notice.id for notice in notices] == [first.id]
    assert first.reason == first.dedupe_key == "monitor_result"
    assert first.result_ref == item.command.log_path
    assert first.execution_generation == item.task.execution_generation
    assert item.command.monitor_outcome == outcome
    assert item.command.exit_code == exit_code
    assert item.task.result_readiness == "ready"
    assert outcome in first.summary
    assert item.task.stop_requested_at is None


async def test_deadline_freezes_timeout_before_cancellation_is_confirmed(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await monitor(db_session, reservation_context, expires=True)
    observed_task = await reserve(db_session, reservation_context)
    await observe(db_session, item, seconds=0, cursor="1")
    await service(db_session).prepare_observation(
        task_id=item.task.id, owner_token=item.task.owner_token, now=NOW + timedelta(seconds=10)
    )
    await db_session.commit()
    first = (await events(db_session, item.task.id))[0]
    original_summary = first.summary
    assert item.command.monitor_outcome == "timed_out"
    assert item.task.state == "running" and item.command.exit_code is None
    assert item.task.stop_reason == "deadline" and item.task.notifications_cancelled_at is None
    assert item.task.result_readiness == "pending"
    await service(db_session).record_observation_failure(
        task_id=item.task.id,
        owner_token=item.task.owner_token,
        now=NOW + timedelta(seconds=11),
        message="temporary provider outage",
    )
    await db_session.commit()
    assert item.task.result_summary == original_summary
    await observe(db_session, item, seconds=12, cursor="2", terminal=True)
    assert item.command.monitor_outcome == "timed_out"
    assert item.task.state == "succeeded" and item.command.exit_code == 0
    assert item.task.result_readiness == "ready"
    assert item.task.result_summary == original_summary
    assert [notice.id for notice in await events(db_session, item.task.id)] == [first.id]
    assert observed_task.task.stop_requested_at is None


async def test_completion_before_deadline_does_not_become_timeout(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await monitor(db_session, reservation_context, expires=True)
    await observe(db_session, item, seconds=9, cursor="1", terminal=True)
    await service(db_session).prepare_observation(
        task_id=item.task.id, owner_token=item.task.owner_token, now=NOW + timedelta(seconds=10)
    )
    await db_session.commit()
    assert item.command.monitor_outcome == "matched"
    assert item.task.stop_requested_at is None
    assert len(await events(db_session, item.task.id)) == 1


async def test_observation_failure_is_not_a_monitor_result(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await monitor(db_session, reservation_context)
    await service(db_session).record_observation_failure(
        task_id=item.task.id,
        owner_token=item.task.owner_token,
        now=NOW,
        message="temporary provider outage",
    )
    await db_session.commit()
    assert item.task.state == "unknown"
    assert item.command.monitor_outcome is None
    assert item.task.result_readiness == "pending"
    assert await events(db_session, item.task.id) == []


@pytest.mark.parametrize("finish_first", [True, False])
@pytest.mark.parametrize("stop", ["task", "conversation"])
async def test_stop_cancels_only_unsent_result_and_preserves_facts(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    finish_first: bool,
    stop: str,
) -> None:
    item = await monitor(db_session, reservation_context)
    if finish_first:
        await observe(db_session, item, seconds=0, cursor="1", terminal=True)
    if stop == "task":
        await service(db_session).request_task_stop(
            task_id=item.task.id, reason=TaskStopReason.user_stop, now=NOW
        )
    else:
        conversation = await db_session.get(Conversation, item.task.conversation_id)
        assert conversation is not None
        conversation.execution_closed_at = NOW
        await service(db_session).prepare_observation(
            task_id=item.task.id, owner_token=item.task.owner_token, now=NOW
        )
    await observe(db_session, item, seconds=1, cursor="2", terminal=True)
    notices = await events(db_session, item.task.id)
    assert all(notice.state == "discarded" for notice in notices)
    assert len(notices) == int(finish_first)
    assert item.command.log_cursor == "2" and item.task.state == "succeeded"
    assert item.command.monitor_outcome == "matched"


async def test_rolled_back_result_cursor_and_notice_recover_together(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    item = await monitor(db_session, reservation_context)
    task_id, command_id, token = item.task.id, item.command.id, item.task.owner_token
    for _ in range(2):
        await service(db_session).record_observation(
            task_id=task_id,
            owner_token=token,
            now=NOW,
            snapshot=ProcessSnapshot(status="exited", exit_code=0, log_cursor="1"),
            log_state="complete",
            confirmed_log_cursor="1",
        )
        assert len(await events(db_session, task_id)) == 1
        await db_session.rollback()
        assert await events(db_session, task_id) == []
        command = await db_session.get(SandboxCommand, command_id)
        task = await db_session.get(BackgroundTask, task_id)
        assert command is not None and task is not None
        assert command.log_cursor is None and command.monitor_outcome is None
        assert task.state == "starting" and task.result_readiness == "pending"


async def test_restarted_owner_unlocks_original_result_when_logs_arrive(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
) -> None:
    item = await monitor(db_session, reservation_context)
    await observe(db_session, item, seconds=0, cursor="1", terminal=True, log_state="retrying")
    first = (await events(db_session, item.task.id))[0]
    assert item.task.result_readiness == "pending"
    await db_session.commit()
    async with session_factory() as restarted:
        moment = NOW + timedelta(minutes=11)
        assert await service(restarted).claim_task(
            task_id=item.task.id,
            owner_token="replacement",
            now=moment,
            owner_until=moment + timedelta(seconds=45),
        )
        await service(restarted).record_observation(
            task_id=item.task.id,
            owner_token="replacement",
            now=moment,
            snapshot=ProcessSnapshot(status="exited", exit_code=0, log_cursor="2"),
            log_state="complete",
            expected_log_cursor="1",
            confirmed_log_cursor="2",
        )
        await restarted.commit()
        saved = await service(restarted).tasks.get(item.task.id)
        assert saved is not None and saved.result_readiness == "ready"
        assert saved.result_summary == first.summary
        assert [notice.id for notice in await events(restarted, item.task.id)] == [first.id]


@pytest.mark.parametrize("stop_first", [True, False])
async def test_stop_and_result_transactions_are_serialized(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
    stop_first: bool,
) -> None:
    item = await monitor(db_session, reservation_context)
    first_written, second_started = asyncio.Event(), asyncio.Event()

    async def write(session: AsyncSession, stop: bool) -> None:
        if stop:
            await service(session).request_task_stop(
                task_id=item.task.id, reason=TaskStopReason.user_stop, now=NOW
            )
        else:
            await service(session).record_observation(
                task_id=item.task.id,
                owner_token=item.task.owner_token,
                now=NOW,
                snapshot=ProcessSnapshot(status="exited", exit_code=0),
                log_state="complete",
            )

    async def first_writer() -> None:
        async with session_factory() as session:
            await write(session, stop_first)
            first_written.set()
            await second_started.wait()
            await session.commit()

    async def second_writer() -> None:
        await first_written.wait()
        async with session_factory() as session:
            second_started.set()
            await write(session, not stop_first)
            await session.commit()

    async with asyncio.timeout(10):
        await asyncio.gather(first_writer(), second_writer())
    await db_session.refresh(item.task)
    await db_session.refresh(item.command)
    notices = await events(db_session, item.task.id)
    assert item.task.state == "succeeded" and item.command.exit_code == 0
    assert item.task.notifications_cancelled_at == NOW
    assert all(notice.state == "discarded" for notice in notices)
    assert len(notices) == int(not stop_first)
