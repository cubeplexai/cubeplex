"""The expand revision preserves legacy execution and notification evidence."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
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
    SteeringMessage,
    User,
    UserSandbox,
)
from cubeplex.scripts.dev.migrate_background_tasks import (
    _load_injected_notice_ids,
    migrate_legacy_commands,
)
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
    checkpointed_completed = SandboxCommand(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_sandbox_id=sandbox.id,
        conversation_id=conv.id,
        run_id="legacy-checkpointed-run",
        tool_call_id="legacy-checkpointed",
        started_by_user_id=user.id,
        command="already reported build",
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
    killed = SandboxCommand(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_sandbox_id=sandbox.id,
        conversation_id=conv.id,
        run_id="legacy-killed-run",
        tool_call_id="legacy-killed",
        started_by_user_id=user.id,
        command="cancelled build",
        kind="execute",
        lifetime="conversation",
        status="killed",
        notice_state="pending",
        notify_on_complete=True,
    )
    unknown_exit = SandboxCommand(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_sandbox_id=sandbox.id,
        conversation_id=conv.id,
        run_id="legacy-unknown-exit-run",
        tool_call_id="legacy-unknown-exit",
        started_by_user_id=user.id,
        command="finished without provider status",
        kind="execute",
        lifetime="conversation",
        status="exited",
        exit_code=None,
        notice_state="pending",
        notify_on_complete=True,
    )
    uncertain_start = SandboxCommand(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_sandbox_id=sandbox.id,
        conversation_id=conv.id,
        run_id="legacy-uncertain-run",
        tool_call_id="legacy-uncertain",
        started_by_user_id=user.id,
        command="possibly started server",
        kind="execute",
        lifetime="conversation",
        status="starting",
        notify_on_complete=True,
    )
    proven_start = SandboxCommand(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_sandbox_id=sandbox.id,
        sandbox_instance_id="legacy-instance-proof",
        conversation_id=conv.id,
        run_id="legacy-proven-run",
        tool_call_id="legacy-proven",
        started_by_user_id=user.id,
        command="known legacy server",
        provider_ref="legacy-process-proof",
        kind="execute",
        lifetime="conversation",
        status="starting",
        notify_on_complete=True,
    )
    handleless_start = SandboxCommand(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_sandbox_id=sandbox.id,
        sandbox_instance_id="legacy-instance-without-handle",
        conversation_id=conv.id,
        run_id="legacy-handleless-run",
        tool_call_id="legacy-handleless",
        started_by_user_id=user.id,
        command="possibly submitted server",
        kind="execute",
        lifetime="conversation",
        status="starting",
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
    db_session.add_all(
        (
            completed,
            checkpointed_completed,
            monitor,
            killed,
            unknown_exit,
            uncertain_start,
            proven_start,
            handleless_start,
            blocked,
        )
    )
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
        state="claimed",
        delivery_steer_id="legacy-injected-steer",
        started_by_user_id=user.id,
    )
    abandoned = SandboxCommandWake(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        command_id=completed.id,
        conversation_id=conv.id,
        reason="completion",
        dedupe_key="completion",
        text_tail="legacy completion retry",
        state="claimed",
        delivery_run_id="legacy-abandoned-run",
        delivery_steer_id="legacy-abandoned-steer",
        started_by_user_id=user.id,
    )
    injected = SteeringMessage(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        conversation_id=conv.id,
        run_id="legacy-notice-run",
        source_kind="background_task",
        client_steer_id="legacy-injected-steer",
        content="legacy task result",
        sender_user_id=user.id,
        state="injected",
    )
    db_session.add_all((delivered, pending, abandoned, injected))
    await db_session.flush()

    injected_notice_ids = await _load_injected_notice_ids(db_session)
    assert injected_notice_ids == {conv.id: frozenset({pending.id})}

    command_ids = (
        completed.id,
        checkpointed_completed.id,
        monitor.id,
        killed.id,
        unknown_exit.id,
        uncertain_start.id,
        proven_start.id,
        handleless_start.id,
        blocked.id,
    )
    checkpointed_notice_ids = {
        conv.id: frozenset((*injected_notice_ids[conv.id], checkpointed_completed.id))
    }
    dry_run = await migrate_legacy_commands(
        db_session,
        apply=False,
        command_ids=command_ids,
    )
    assert {item.command_id for item in dry_run.blockers} == {
        uncertain_start.id,
        handleless_start.id,
        blocked.id,
    }
    assert {item.command_id for item in dry_run.migratable} == {
        completed.id,
        checkpointed_completed.id,
        monitor.id,
        killed.id,
        unknown_exit.id,
        proven_start.id,
    }
    assert completed.task_id is None and monitor.task_id is None

    first = await migrate_legacy_commands(
        db_session,
        apply=True,
        command_ids=command_ids,
        checkpointed_notice_ids=checkpointed_notice_ids,
    )
    await db_session.flush()
    assert first.migrated == 6
    assert (
        blocked.task_id is None
        and uncertain_start.task_id is None
        and handleless_start.task_id is None
    )
    await db_session.refresh(completed)
    await db_session.refresh(monitor)
    assert completed.task_id is not None and monitor.task_id is not None
    assert completed.sandbox_instance_id is None

    completed_task = await db_session.get(BackgroundTask, completed.task_id)
    checkpointed_task = await db_session.get(BackgroundTask, checkpointed_completed.task_id)
    monitor_task = await db_session.get(BackgroundTask, monitor.task_id)
    killed_task = await db_session.get(BackgroundTask, killed.task_id)
    unknown_exit_task = await db_session.get(BackgroundTask, unknown_exit.task_id)
    proven_task = await db_session.get(BackgroundTask, proven_start.task_id)
    assert all(
        task is not None
        for task in (
            completed_task,
            checkpointed_task,
            monitor_task,
            killed_task,
            unknown_exit_task,
            proven_task,
        )
    )
    assert completed_task is not None
    assert checkpointed_task is not None
    assert monitor_task is not None
    assert killed_task is not None
    assert unknown_exit_task is not None
    assert proven_task is not None
    assert completed_task.state == "succeeded"
    assert completed_task.result_readiness == "unavailable"
    assert completed_task.backgrounded_at is not None
    assert monitor_task.notifications_cancelled_at is not None
    assert killed_task.state == "cancelled"
    assert killed_task.notifications_cancelled_at is None
    assert unknown_exit_task.state == "failed"
    assert unknown_exit_task.result_readiness == "unavailable"
    assert proven_task.state == "unknown"
    await db_session.refresh(proven_start)
    assert proven_start.start_requested_at == proven_start.created_at

    events = list(
        (
            await db_session.execute(
                select(BackgroundTaskEvent).where(
                    col(BackgroundTaskEvent.task_id).in_(
                        (
                            completed_task.id,
                            checkpointed_task.id,
                            monitor_task.id,
                            killed_task.id,
                            unknown_exit_task.id,
                        )
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    by_id = {event.id: event for event in events}
    assert by_id[delivered.id].state == "delivered"
    assert by_id[delivered.id].checkpoint_input_id == "legacy-input"
    assert by_id[pending.id].state == "delivered"
    assert by_id[pending.id].discard_reason is None
    completion_events = [event for event in events if event.task_id == completed_task.id]
    assert len(completion_events) == 1
    assert completion_events[0].state == "pending"
    assert completion_events[0].id == abandoned.id
    assert completion_events[0].delivery_run_id is None
    assert completion_events[0].delivery_input_id is None
    checkpointed_events = [event for event in events if event.task_id == checkpointed_task.id]
    assert len(checkpointed_events) == 1
    assert checkpointed_events[0].state == "delivered"
    killed_events = [event for event in events if event.task_id == killed_task.id]
    assert len(killed_events) == 1
    assert killed_events[0].state == "pending"
    unknown_exit_events = [event for event in events if event.task_id == unknown_exit_task.id]
    assert len(unknown_exit_events) == 1
    assert unknown_exit_events[0].state == "pending"

    blocked_status = await inspect_background_task_cutover(db_session)
    assert not blocked_status.ready
    assert blocked_status.unmigrated_commands == baseline_status.unmigrated_commands + 3

    blocked.status = "killed"
    blocked.finished_at = blocked.updated_at
    uncertain_start.status = "killed"
    uncertain_start.finished_at = uncertain_start.updated_at
    handleless_start.status = "killed"
    handleless_start.finished_at = handleless_start.updated_at
    await db_session.flush()
    final = await migrate_legacy_commands(
        db_session,
        apply=True,
        command_ids=(blocked.id, uncertain_start.id, handleless_start.id),
        checkpointed_notice_ids=checkpointed_notice_ids,
    )
    assert final.migrated == 3
    ready_status = await inspect_background_task_cutover(db_session)
    assert ready_status == baseline_status

    by_id[pending.id].state = "pending"
    by_id[pending.id].delivered_at = None
    checkpointed_events[0].state = "pending"
    checkpointed_events[0].delivered_at = None
    completion_events[0].delivery_run_id = "legacy-abandoned-run"
    completion_events[0].delivery_input_id = "legacy-abandoned-steer"
    await db_session.delete(by_id[delivered.id])
    await db_session.flush()

    second = await migrate_legacy_commands(
        db_session,
        apply=True,
        command_ids=command_ids,
        checkpointed_notice_ids=checkpointed_notice_ids,
    )
    await db_session.flush()
    assert second.migrated == 0
    assert second.events_migrated == 4
    assert by_id[pending.id].state == "delivered"
    assert checkpointed_events[0].state == "delivered"
    assert killed_events[0].state == "pending"
    assert completion_events[0].state == "pending"
    assert completion_events[0].delivery_run_id is None
    assert completion_events[0].delivery_input_id is None
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


async def test_legacy_backfill_preserves_original_execution_generation(
    db_session: AsyncSession,
    expanded_lifecycle_schema: None,
) -> None:
    del expanded_lifecycle_schema
    await _ensure_default_user_and_membership()
    user = (
        await db_session.execute(select(User).where(col(User.email) == DEFAULT_TEST_EMAIL))
    ).scalar_one()
    conv = Conversation(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        creator_user_id=user.id,
        title="reopened legacy generation",
        execution_generation=1,
    )
    db_session.add(conv)
    await db_session.flush()
    sandbox = UserSandbox(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_id=user.id,
        scope_type="conversation",
        scope_id=conv.id,
        sandbox_id="replacement-generation-instance",
        image="test",
    )
    db_session.add(sandbox)
    await db_session.flush()
    admission = ConversationExecutionAdmission(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        conversation_id=conv.id,
        actor_user_id=user.id,
        source_kind="user_message",
        source_id="web:legacy-generation",
        execution_generation=0,
        run_id="legacy-admitted-run",
    )
    admitted_command = SandboxCommand(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_sandbox_id=sandbox.id,
        conversation_id=conv.id,
        run_id="legacy-admitted-run",
        tool_call_id="legacy-admitted-command",
        started_by_user_id=user.id,
        command="old admitted work",
        kind="execute",
        lifetime="conversation",
        status="exited",
        exit_code=0,
        notice_state="pending",
        notify_on_complete=True,
    )
    pre_admission_command = SandboxCommand(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_sandbox_id=sandbox.id,
        conversation_id=conv.id,
        run_id="pre-admission-run",
        tool_call_id="pre-admission-command",
        started_by_user_id=user.id,
        command="older work",
        kind="execute",
        lifetime="conversation",
        status="exited",
        exit_code=0,
        notice_state="pending",
        notify_on_complete=True,
    )
    db_session.add_all((admission, admitted_command, pre_admission_command))
    await db_session.flush()

    report = await migrate_legacy_commands(
        db_session,
        apply=True,
        command_ids=(admitted_command.id, pre_admission_command.id),
        checkpointed_notice_ids={},
    )
    assert report.migrated == 2
    admitted_task = await db_session.get(BackgroundTask, admitted_command.task_id)
    pre_admission_task = await db_session.get(BackgroundTask, pre_admission_command.task_id)
    assert admitted_task is not None and pre_admission_task is not None
    assert admitted_task.admission_id == admission.id
    assert admitted_task.execution_generation == 0
    assert pre_admission_task.execution_generation == 0
    assert admitted_task.notifications_cancelled_at is not None
    assert pre_admission_task.notifications_cancelled_at is not None
    events = list(
        await db_session.scalars(
            select(BackgroundTaskEvent).where(
                col(BackgroundTaskEvent.task_id).in_((admitted_task.id, pre_admission_task.id))
            )
        )
    )
    assert len(events) == 2
    assert {event.state for event in events} == {"discarded"}
    await db_session.rollback()


async def test_legacy_backfill_keeps_command_cancellation_evidence_local(
    db_session: AsyncSession,
    expanded_lifecycle_schema: None,
) -> None:
    del expanded_lifecycle_schema
    await _ensure_default_user_and_membership()
    user = (
        await db_session.execute(select(User).where(col(User.email) == DEFAULT_TEST_EMAIL))
    ).scalar_one()
    conv = Conversation(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        creator_user_id=user.id,
        title="legacy command-local cancellation",
    )
    db_session.add(conv)
    await db_session.flush()
    sandbox = UserSandbox(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_id=user.id,
        scope_type="conversation",
        scope_id=conv.id,
        sandbox_id="legacy-command-local-instance",
        image="test",
    )
    db_session.add(sandbox)
    await db_session.flush()
    started = datetime.now(UTC)
    silent = SandboxCommand(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_sandbox_id=sandbox.id,
        sandbox_instance_id="legacy-command-local-instance",
        conversation_id=conv.id,
        run_id="shared-pre-admission-run",
        tool_call_id="silent-command",
        started_by_user_id=user.id,
        command="silent work",
        provider_ref="silent-process-handle",
        kind="execute",
        lifetime="conversation",
        status="running",
        notice_state="none",
        notify_on_complete=False,
        created_at=started,
        updated_at=started,
    )
    notifying = SandboxCommand(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_sandbox_id=sandbox.id,
        conversation_id=conv.id,
        run_id="shared-pre-admission-run",
        tool_call_id="notifying-command",
        started_by_user_id=user.id,
        command="notify when done",
        kind="execute",
        lifetime="conversation",
        status="exited",
        exit_code=0,
        notice_state="pending",
        notify_on_complete=True,
        created_at=started + timedelta(seconds=1),
        updated_at=started + timedelta(seconds=1),
    )
    run_lifetime = SandboxCommand(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_sandbox_id=sandbox.id,
        conversation_id=conv.id,
        run_id="terminal-run-lifetime-command",
        tool_call_id="run-lifetime-command",
        started_by_user_id=user.id,
        command="legacy default background work",
        kind="execute",
        lifetime="run",
        status="exited",
        exit_code=0,
        notice_state="pending",
        notify_on_complete=True,
        created_at=started + timedelta(seconds=2),
        updated_at=started + timedelta(seconds=2),
    )
    db_session.add_all((silent, notifying, run_lifetime))
    await db_session.flush()

    report = await migrate_legacy_commands(
        db_session,
        apply=True,
        command_ids=(silent.id, notifying.id, run_lifetime.id),
        checkpointed_notice_ids={},
    )
    assert report.migrated == 3
    silent_task = await db_session.get(BackgroundTask, silent.task_id)
    notifying_task = await db_session.get(BackgroundTask, notifying.task_id)
    run_lifetime_task = await db_session.get(BackgroundTask, run_lifetime.task_id)
    assert silent_task is not None
    assert notifying_task is not None
    assert run_lifetime_task is not None
    assert silent_task.admission_id != notifying_task.admission_id
    silent_admission = await db_session.get(
        ConversationExecutionAdmission, silent_task.admission_id
    )
    assert silent_admission is not None
    assert silent_admission.revoked_at is None
    assert notifying_task.notifications_cancelled_at is None
    assert run_lifetime_task.notifications_cancelled_at is None
    events = list(
        await db_session.scalars(
            select(BackgroundTaskEvent).where(
                col(BackgroundTaskEvent.task_id).in_((notifying_task.id, run_lifetime_task.id))
            )
        )
    )
    assert len(events) == 2
    assert {event.state for event in events} == {"pending"}
    await db_session.rollback()
