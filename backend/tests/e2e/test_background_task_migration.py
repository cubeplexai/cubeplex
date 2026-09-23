"""The expand revision preserves legacy execution and notification evidence."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.config import Config
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from alembic import command as alembic_command
from cubeplex.config import config
from cubeplex.models import (
    BackgroundTask,
    BackgroundTaskEvent,
    Conversation,
    ConversationExecutionAdmission,
    SandboxCommand,
    SandboxCommandWake,
    User,
    UserSandbox,
)
from cubeplex.scripts.dev.migrate_background_tasks import migrate_legacy_commands
from cubeplex.services.background_task_cutover import inspect_background_task_cutover
from tests.e2e.conftest import (
    DEFAULT_ORG_ID,
    DEFAULT_TEST_EMAIL,
    DEFAULT_WS_ID,
    _ensure_default_user_and_membership,
)


def _migrate(target: str, *, downgrade: bool = False) -> None:
    backend = Path(__file__).parents[2]
    settings = Config(str(backend / "alembic.ini"))
    settings.set_main_option("script_location", str(backend / "alembic"))
    if downgrade:
        alembic_command.downgrade(settings, target)
    else:
        alembic_command.upgrade(settings, target)


@pytest_asyncio.fixture
async def expanded_lifecycle_schema(db_session: AsyncSession) -> AsyncIterator[None]:
    await db_session.commit()
    await asyncio.to_thread(_migrate, "76a2d219d682", downgrade=True)
    try:
        yield
    finally:
        await db_session.rollback()
        await asyncio.to_thread(_migrate, "head")


async def test_expand_preserves_legacy_handles_notices_and_unknown_instance(
    db_session: AsyncSession,
) -> None:
    assert str(config.get("database.name")).startswith("cubeplex_test")
    for model in (BackgroundTask, ConversationExecutionAdmission):
        if await db_session.scalar(select(func.count()).select_from(model)) != 0:
            pytest.skip("expand round-trip requires empty lifecycle tables")
    await db_session.commit()
    await _ensure_default_user_and_membership()
    user = (
        await db_session.execute(select(User).where(col(User.email) == DEFAULT_TEST_EMAIL))
    ).scalar_one()
    conv = Conversation(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        creator_user_id=user.id,
        title="legacy lifecycle migration evidence",
    )
    db_session.add(conv)
    await db_session.flush()
    sandbox = UserSandbox(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_id=user.id,
        scope_type="conversation",
        scope_id=conv.id,
        sandbox_id="replacement-not-proof-of-original-instance",
        image="test",
    )
    db_session.add(sandbox)
    await db_session.flush()
    legacy = SandboxCommand(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_sandbox_id=sandbox.id,
        conversation_id=conv.id,
        run_id="legacy-run",
        started_by_user_id=user.id,
        command="watch build",
        provider_ref="original-process-handle",
        log_cursor="original-log-cursor",
        kind="monitor",
        lifetime="conversation",
        status="running",
    )
    db_session.add(legacy)
    await db_session.flush()
    wake = SandboxCommandWake(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        command_id=legacy.id,
        conversation_id=conv.id,
        reason="line",
        dedupe_key=f"{legacy.id}:line:1",
        state="delivered",
        delivery_run_id="notification-run",
        delivery_steer_id="original-input-id",
        started_by_user_id=user.id,
    )
    db_session.add(wake)
    await db_session.commit()
    conv_id, sandbox_id, command_id, wake_id = conv.id, sandbox.id, legacy.id, wake.id
    db_session.expunge_all()
    try:
        await asyncio.to_thread(_migrate, "b2141c3f7682", downgrade=True)
        before = (
            await db_session.execute(
                text(
                    "SELECT provider_ref, log_cursor, lifetime, status "
                    "FROM sandbox_commands WHERE id = :id"
                ),
                {"id": command_id},
            )
        ).one()
        assert before == (
            "original-process-handle",
            "original-log-cursor",
            "conversation",
            "running",
        )
        await db_session.commit()
        await asyncio.to_thread(_migrate, "76a2d219d682")
        restored = await db_session.get(SandboxCommand, command_id)
        restored_wake = await db_session.get(SandboxCommandWake, wake_id)
        assert restored is not None and restored_wake is not None
        assert restored.provider_ref == "original-process-handle"
        assert restored.log_cursor == "original-log-cursor"
        assert restored.task_id is None and restored.sandbox_instance_id is None
        assert restored.start_token is None and restored.start_requested_at is None
        assert restored.monitor_outcome is None, "legacy output is not a one-shot result"
        assert restored_wake.state == "delivered"
        assert restored_wake.delivery_run_id == "notification-run"
        assert restored_wake.delivery_steer_id == "original-input-id"
        assert await db_session.scalar(select(func.count()).select_from(BackgroundTask)) == 0
    finally:
        await db_session.rollback()
        await asyncio.to_thread(_migrate, "76a2d219d682")
        for model, record_id in (
            (SandboxCommandWake, wake_id),
            (SandboxCommand, command_id),
            (UserSandbox, sandbox_id),
            (Conversation, conv_id),
        ):
            await db_session.execute(delete(model).where(col(model.id) == record_id))
        await db_session.commit()
        await asyncio.to_thread(_migrate, "head")


async def test_legacy_backfill_is_idempotent_and_does_not_reinterpret_monitors(
    db_session: AsyncSession,
    expanded_lifecycle_schema: None,
) -> None:
    del expanded_lifecycle_schema
    await _ensure_default_user_and_membership()
    baseline_status = await inspect_background_task_cutover(db_session)
    user = (
        await db_session.execute(select(User).where(col(User.email) == DEFAULT_TEST_EMAIL))
    ).scalar_one()
    conv = Conversation(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        creator_user_id=user.id,
        title="legacy task backfill",
    )
    db_session.add(conv)
    await db_session.flush()
    sandbox = UserSandbox(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_id=user.id,
        scope_type="conversation",
        scope_id=conv.id,
        sandbox_id="current-instance-is-not-legacy-proof",
        image="test",
    )
    db_session.add(sandbox)
    await db_session.flush()

    completed = SandboxCommand(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_sandbox_id=sandbox.id,
        conversation_id=conv.id,
        run_id="legacy-completed-run",
        tool_call_id="legacy-execute",
        started_by_user_id=user.id,
        command="build project",
        description="old background build",
        provider_ref="old-process",
        log_path=".cubeplex/legacy-build.log",
        kind="execute",
        lifetime="conversation",
        status="exited",
        exit_code=0,
        notice_state="pending",
        notify_on_complete=True,
    )
    monitor = SandboxCommand(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_sandbox_id=sandbox.id,
        conversation_id=conv.id,
        run_id="legacy-monitor-run",
        tool_call_id="legacy-monitor",
        started_by_user_id=user.id,
        command="until healthy",
        description="old repeating monitor",
        provider_ref="old-monitor-process",
        log_path=".cubeplex/legacy-monitor.log",
        kind="monitor",
        lifetime="conversation",
        status="exited",
        exit_code=0,
        notice_state="delivered",
        notify_on_complete=True,
    )
    blocked = SandboxCommand(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_sandbox_id=sandbox.id,
        conversation_id=conv.id,
        run_id="legacy-live-run",
        tool_call_id="legacy-live",
        started_by_user_id=user.id,
        command="still running",
        kind="execute",
        lifetime="run",
        status="running",
    )
    db_session.add_all((completed, monitor, blocked))
    await db_session.flush()
    delivered = SandboxCommandWake(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        command_id=monitor.id,
        conversation_id=conv.id,
        reason="line",
        dedupe_key=f"{monitor.id}:line:1",
        text_tail="first match",
        state="delivered",
        delivery_run_id="legacy-notice-run",
        delivery_steer_id="legacy-input",
        started_by_user_id=user.id,
    )
    pending = SandboxCommandWake(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        command_id=monitor.id,
        conversation_id=conv.id,
        reason="exit",
        dedupe_key=f"{monitor.id}:exit",
        text_tail="later exit",
        state="pending",
        started_by_user_id=user.id,
    )
    db_session.add_all((delivered, pending))
    await db_session.flush()

    command_ids = (completed.id, monitor.id, blocked.id)
    dry_run = await migrate_legacy_commands(
        db_session,
        apply=False,
        command_ids=command_ids,
    )
    assert {item.command_id for item in dry_run.blockers} == {blocked.id}
    assert {item.command_id for item in dry_run.migratable} == {completed.id, monitor.id}
    assert completed.task_id is None and monitor.task_id is None

    first = await migrate_legacy_commands(
        db_session,
        apply=True,
        command_ids=command_ids,
    )
    await db_session.flush()
    assert first.migrated == 2
    assert blocked.task_id is None
    await db_session.refresh(completed)
    await db_session.refresh(monitor)
    assert completed.task_id is not None and monitor.task_id is not None
    assert completed.sandbox_instance_id is None

    completed_task = await db_session.get(BackgroundTask, completed.task_id)
    monitor_task = await db_session.get(BackgroundTask, monitor.task_id)
    assert completed_task is not None and monitor_task is not None
    assert completed_task.state == "succeeded"
    assert completed_task.result_readiness == "unavailable"
    assert completed_task.backgrounded_at is not None
    assert monitor_task.notifications_cancelled_at is not None

    events = list(
        (
            await db_session.execute(
                select(BackgroundTaskEvent).where(
                    col(BackgroundTaskEvent.task_id).in_((completed_task.id, monitor_task.id))
                )
            )
        )
        .scalars()
        .all()
    )
    by_id = {event.id: event for event in events}
    assert by_id[delivered.id].state == "delivered"
    assert by_id[delivered.id].checkpoint_input_id == "legacy-input"
    assert by_id[pending.id].state == "discarded"
    assert by_id[pending.id].discard_reason == "legacy_monitor_subscription"
    completion_events = [event for event in events if event.task_id == completed_task.id]
    assert len(completion_events) == 1
    assert completion_events[0].state == "pending"

    blocked_status = await inspect_background_task_cutover(db_session)
    assert not blocked_status.ready
    assert blocked_status.unmigrated_commands == baseline_status.unmigrated_commands + 1

    blocked.status = "killed"
    blocked.finished_at = blocked.updated_at
    await db_session.flush()
    final = await migrate_legacy_commands(
        db_session,
        apply=True,
        command_ids=(blocked.id,),
    )
    assert final.migrated == 1
    ready_status = await inspect_background_task_cutover(db_session)
    assert ready_status == baseline_status

    await db_session.delete(by_id[pending.id])
    await db_session.delete(completion_events[0])
    await db_session.flush()

    second = await migrate_legacy_commands(
        db_session,
        apply=True,
        command_ids=command_ids,
    )
    await db_session.flush()
    assert second.migrated == 0
    assert second.events_migrated == 2
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(BackgroundTask)
            .where(col(BackgroundTask.id).in_((completed_task.id, monitor_task.id)))
        )
        == 2
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(BackgroundTaskEvent)
            .where(col(BackgroundTaskEvent.task_id) == completed_task.id)
        )
        == 1
    )
    await db_session.rollback()
