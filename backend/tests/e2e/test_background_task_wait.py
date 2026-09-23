"""Host validation for CubeLoop Todo background-task declarations."""

from dataclasses import replace
from datetime import timedelta

import pytest
from cubeloop.agent.types import AgentContext
from cubeloop.middleware.todo import TaskWaitBinding
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from cubeplex.models import Conversation
from cubeplex.models.background_task import TaskStopReason
from cubeplex.models.sandbox_command import SandboxCommand
from cubeplex.services.background_task_wait import BackgroundTaskWaitValidator
from tests.e2e import test_background_task_reservation as reservation_fixtures
from tests.e2e.conftest import DEFAULT_ORG_ID, DEFAULT_WS_ID
from tests.e2e.test_background_task_reservation import (
    NOW,
    ReservationContext,
    reserve,
    service,
)

reservation_context = reservation_fixtures.reservation_context


def _context(run_id: str) -> AgentContext:
    return AgentContext(system_prompt="", messages=[], run_id=run_id)


async def _background_task(
    session: AsyncSession,
    reservation_context: ReservationContext,
    *,
    notify_on_complete: bool = True,
) -> str:
    owner = "wait-validator-owner"
    item = await reserve(
        session,
        reservation_context,
        spec=replace(
            reservation_context.spec,
            tool_call_id=f"wait-{notify_on_complete}",
            notify_on_complete=notify_on_complete,
        ),
    )
    item.task.owner_token = owner
    item.task.owner_until = NOW + timedelta(minutes=1)
    assert await service(session).begin_start(
        task_id=item.task.id,
        owner_token=owner,
        now=NOW,
    )
    await service(session).register_start_receipt(
        task_id=item.task.id,
        start_token=owner,
        sandbox_instance_id=reservation_context.details.sandbox_instance_id,
        provider_ref=f"provider-{item.task.id}",
        now=NOW,
    )
    await service(session).handoff_task(
        task_id=item.task.id,
        owner_token=owner,
        now=NOW,
    )
    await session.commit()
    return item.task.id


async def _validator(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    reservation_context: ReservationContext,
) -> BackgroundTaskWaitValidator:
    conversation = await session.get(Conversation, reservation_context.conversation_id)
    assert conversation is not None
    return BackgroundTaskWaitValidator(
        session_factory,
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        conversation_id=reservation_context.conversation_id,
        execution_generation=conversation.execution_generation,
        run_id=reservation_context.spec.originating_run_id,
    )


async def test_background_task_wait_accepts_observable_handoff(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
) -> None:
    task_id = await _background_task(db_session, reservation_context)
    validator = await _validator(session_factory, db_session, reservation_context)

    result = await validator(
        [task_id],
        _context(reservation_context.spec.originating_run_id),
        None,
    )

    assert result.status == "valid", result.reason
    assert result.validation["conversation_id"] == reservation_context.conversation_id
    assert result.validation["execution_generation"] == 0
    assert result.validation["task_revisions"] == {task_id: 3}


@pytest.mark.parametrize("case", ["foreground", "silent", "no_handle", "wrong_run"])
async def test_background_task_wait_rejects_tasks_without_deliverable_result(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
    case: str,
) -> None:
    if case == "foreground":
        item = await reserve(db_session, reservation_context)
        await db_session.commit()
        task_id = item.task.id
    else:
        task_id = await _background_task(
            db_session,
            reservation_context,
            notify_on_complete=case != "silent",
        )
        if case == "no_handle":
            command = await db_session.scalar(
                select(SandboxCommand).where(col(SandboxCommand.task_id) == task_id)
            )
            assert command is not None
            command.provider_ref = None
            await db_session.commit()
    validator = await _validator(session_factory, db_session, reservation_context)
    run_id = (
        "run-someone-else" if case == "wrong_run" else reservation_context.spec.originating_run_id
    )

    result = await validator([task_id], _context(run_id), None)

    assert result.status == "invalid"
    assert result.reason


async def test_cancelled_wait_only_closes_a_prior_valid_binding(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
) -> None:
    task_id = await _background_task(db_session, reservation_context)
    validator = await _validator(session_factory, db_session, reservation_context)
    ctx = _context(reservation_context.spec.originating_run_id)
    initial = await validator([task_id], ctx, None)
    assert initial.status == "valid", initial.reason
    await service(db_session).request_task_stop(
        task_id=task_id,
        reason=TaskStopReason.user_stop,
        now=NOW + timedelta(seconds=1),
    )
    await db_session.commit()

    first_declaration = await validator([task_id], ctx, None)
    binding = TaskWaitBinding(
        task_ids=[task_id],
        todos=[{"content": "wait for build", "status": "in_progress"}],
        run_id=ctx.run_id,
        input_boundary="test-boundary",
        validation=initial.validation,
    )
    recheck = await validator([task_id], ctx, binding)

    assert first_declaration.status == "invalid"
    assert recheck.status == "cancelled"
    assert "stopped" in recheck.reason
