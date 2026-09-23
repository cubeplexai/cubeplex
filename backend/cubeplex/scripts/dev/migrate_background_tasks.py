"""Plan and backfill pre-cutover sandbox commands into durable tasks.

The caller owns the transaction. Dry-run is the default at the CLI layer;
``migrate_legacy_commands`` only mutates rows when ``apply=True``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from cubeplex.models.background_task import (
    BackgroundTask,
    BackgroundTaskEvent,
    BackgroundTaskEventState,
    BackgroundTaskState,
    TaskResultReadiness,
    TaskStopReason,
)
from cubeplex.models.conversation import Conversation
from cubeplex.models.conversation_execution import (
    ConversationExecutionAdmission,
    ExecutionSourceKind,
)
from cubeplex.models.sandbox_command import (
    SandboxCommand,
    SandboxCommandKind,
    SandboxCommandLifetime,
    SandboxCommandNoticeState,
    SandboxCommandStatus,
    SandboxCommandWake,
    SandboxCommandWakeState,
)

_ACTIVE = frozenset((SandboxCommandStatus.starting.value, SandboxCommandStatus.running.value))


@dataclass(frozen=True)
class LegacyCommandPlan:
    command_id: str
    action: Literal["migrate", "block"]
    reason: str


@dataclass(frozen=True)
class LegacyMigrationReport:
    migratable: tuple[LegacyCommandPlan, ...]
    blockers: tuple[LegacyCommandPlan, ...]
    migrated: int = 0
    events_migrated: int = 0


_MIGRATION_LOCK_ID = 0x435042475441534B


def _plan(command: SandboxCommand) -> LegacyCommandPlan:
    if command.status in _ACTIVE and command.kind == SandboxCommandKind.monitor.value:
        return LegacyCommandPlan(
            command_id=command.id,
            action="block",
            reason="active legacy monitor must finish or be explicitly stopped",
        )
    if command.status in _ACTIVE and command.lifetime == SandboxCommandLifetime.run.value:
        return LegacyCommandPlan(
            command_id=command.id,
            action="block",
            reason="active run-lifetime command must finish or be explicitly stopped",
        )
    return LegacyCommandPlan(
        command_id=command.id,
        action="migrate",
        reason=(
            "active command will remain unknown without original instance evidence"
            if command.status in _ACTIVE
            else "terminal command evidence can be preserved"
        ),
    )


def _task_state(command: SandboxCommand) -> BackgroundTaskState:
    if command.status == SandboxCommandStatus.killed.value:
        return BackgroundTaskState.cancelled
    if command.status == SandboxCommandStatus.exited.value:
        if command.exit_code is None:
            return BackgroundTaskState.unknown
        return (
            BackgroundTaskState.succeeded if command.exit_code == 0 else BackgroundTaskState.failed
        )
    return BackgroundTaskState.unknown


def _notifications_cancelled(
    command: SandboxCommand,
    conversation: Conversation,
) -> bool:
    return bool(
        not command.notify_on_complete
        or command.kind == SandboxCommandKind.monitor.value
        or command.lifetime == SandboxCommandLifetime.run.value
        or command.status == SandboxCommandStatus.killed.value
        or conversation.deleted_at is not None
        or conversation.execution_closed_at is not None
    )


def _result_readiness(command: SandboxCommand, state: BackgroundTaskState) -> TaskResultReadiness:
    if state not in (
        BackgroundTaskState.succeeded,
        BackgroundTaskState.failed,
        BackgroundTaskState.cancelled,
    ):
        return TaskResultReadiness.pending
    if command.log_state == "complete":
        return TaskResultReadiness.ready
    return TaskResultReadiness.unavailable


def _summary(command: SandboxCommand, state: BackgroundTaskState) -> str:
    if state == BackgroundTaskState.succeeded:
        return "Legacy command completed successfully."
    if state == BackgroundTaskState.failed:
        return f"Legacy command exited with status {command.exit_code}."
    if state == BackgroundTaskState.cancelled:
        return "Legacy command was stopped."
    return "Legacy command state could not be verified during cutover."


async def _admission_for(
    session: AsyncSession,
    command: SandboxCommand,
    conversation: Conversation,
) -> ConversationExecutionAdmission:
    source_id = f"legacy-command:{command.id}"
    existing = (
        await session.execute(
            select(ConversationExecutionAdmission).where(
                col(ConversationExecutionAdmission.org_id) == command.org_id,
                col(ConversationExecutionAdmission.workspace_id) == command.workspace_id,
                col(ConversationExecutionAdmission.source_kind)
                == ExecutionSourceKind.background_task.value,
                col(ConversationExecutionAdmission.source_id) == source_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    terminal = command.finished_at or command.updated_at
    admission = ConversationExecutionAdmission(
        org_id=command.org_id,
        workspace_id=command.workspace_id,
        conversation_id=command.conversation_id,
        actor_user_id=command.started_by_user_id,
        source_kind=ExecutionSourceKind.background_task.value,
        source_id=source_id,
        execution_generation=conversation.execution_generation,
        run_id=command.run_id,
        run_start_requested_at=command.created_at,
        run_started_at=command.created_at,
        run_finished_at=terminal if command.status not in _ACTIVE else None,
        run_terminal_status=("completed" if command.status not in _ACTIVE else None),
        run_terminal_at=terminal if command.status not in _ACTIVE else None,
        revoked_at=(terminal if _notifications_cancelled(command, conversation) else None),
        created_at=command.created_at,
        updated_at=command.updated_at,
    )
    session.add(admission)
    await session.flush()
    return admission


def _event_state(
    command: SandboxCommand,
    wake: SandboxCommandWake,
    *,
    cancelled: bool,
) -> tuple[BackgroundTaskEventState, str | None]:
    if wake.state == SandboxCommandWakeState.delivered.value:
        return BackgroundTaskEventState.delivered, None
    if command.kind == SandboxCommandKind.monitor.value:
        return BackgroundTaskEventState.discarded, "legacy_monitor_subscription"
    if cancelled:
        return BackgroundTaskEventState.discarded, "legacy_notification_revoked"
    return BackgroundTaskEventState.pending, None


async def _copy_wakes(
    session: AsyncSession,
    *,
    command: SandboxCommand,
    task: BackgroundTask,
    cancelled: bool,
) -> int:
    wakes = list(
        (
            await session.execute(
                select(SandboxCommandWake)
                .where(col(SandboxCommandWake.command_id) == command.id)
                .order_by(col(SandboxCommandWake.created_at), col(SandboxCommandWake.id))
            )
        )
        .scalars()
        .all()
    )
    copied = 0
    for wake in wakes:
        existing = await session.get(BackgroundTaskEvent, wake.id)
        if existing is not None:
            if existing.task_id != task.id:
                raise RuntimeError(f"legacy notice id collision: {wake.id}")
            continue
        state, discard_reason = _event_state(command, wake, cancelled=cancelled)
        delivered = state == BackgroundTaskEventState.delivered
        session.add(
            BackgroundTaskEvent(
                id=wake.id,
                org_id=wake.org_id,
                workspace_id=wake.workspace_id,
                task_id=task.id,
                conversation_id=wake.conversation_id,
                execution_generation=task.execution_generation,
                reason=wake.reason,
                dedupe_key=wake.dedupe_key,
                summary=wake.text_tail,
                result_ref=task.result_ref,
                state=state.value,
                discard_reason=discard_reason,
                delivery_run_id=wake.delivery_run_id,
                delivery_input_id=wake.delivery_steer_id,
                checkpoint_run_id=wake.delivery_run_id if delivered else None,
                checkpoint_input_id=wake.delivery_steer_id if delivered else None,
                delivered_at=wake.updated_at if delivered else None,
                created_at=wake.created_at,
                updated_at=wake.updated_at,
            )
        )
        copied += 1
    return copied


async def _copy_missing_completion(
    session: AsyncSession,
    *,
    command: SandboxCommand,
    task: BackgroundTask,
    cancelled: bool,
) -> int:
    if (
        command.kind != SandboxCommandKind.execute.value
        or command.status != SandboxCommandStatus.exited.value
        or command.notice_state != SandboxCommandNoticeState.pending.value
    ):
        return 0
    wake_id = await session.scalar(
        select(col(SandboxCommandWake.id)).where(col(SandboxCommandWake.command_id) == command.id)
    )
    if wake_id is not None:
        return 0
    existing = await session.scalar(
        select(col(BackgroundTaskEvent.id)).where(
            col(BackgroundTaskEvent.task_id) == task.id,
            col(BackgroundTaskEvent.dedupe_key) == "completion",
        )
    )
    if existing is not None:
        return 0
    state = BackgroundTaskEventState.discarded if cancelled else BackgroundTaskEventState.pending
    session.add(
        BackgroundTaskEvent(
            org_id=command.org_id,
            workspace_id=command.workspace_id,
            task_id=task.id,
            conversation_id=command.conversation_id,
            execution_generation=task.execution_generation,
            reason="completion",
            dedupe_key="completion",
            summary=task.result_summary,
            result_ref=task.result_ref,
            state=state.value,
            discard_reason="legacy_notification_revoked" if cancelled else None,
            created_at=command.updated_at,
            updated_at=command.updated_at,
        )
    )
    return 1


async def _migrate_one(
    session: AsyncSession,
    command: SandboxCommand,
    conversation: Conversation,
) -> int:
    admission = await _admission_for(session, command, conversation)
    state = _task_state(command)
    cancelled = _notifications_cancelled(command, conversation)
    terminal = command.finished_at or command.updated_at
    readiness = _result_readiness(command, state)
    stopped = (
        command.status == SandboxCommandStatus.killed.value or conversation.deleted_at is not None
    )
    task = BackgroundTask(
        org_id=command.org_id,
        workspace_id=command.workspace_id,
        conversation_id=command.conversation_id,
        admission_id=admission.id,
        kind="command",
        description=command.description,
        originating_run_id=command.run_id,
        tool_call_id=command.tool_call_id or f"legacy:{command.id}",
        agent_id=command.agent_id,
        started_by_user_id=command.started_by_user_id,
        execution_generation=conversation.execution_generation,
        state=state.value,
        notify_on_complete=command.notify_on_complete,
        deadline_at=command.monitor_deadline_at,
        stop_requested_at=terminal if stopped else None,
        stop_reason=(
            TaskStopReason.conversation_deleted.value
            if conversation.deleted_at is not None
            else TaskStopReason.user_stop.value
            if stopped
            else None
        ),
        notifications_cancelled_at=terminal if cancelled else None,
        backgrounded_at=(
            command.created_at
            if command.lifetime == SandboxCommandLifetime.conversation.value
            else None
        ),
        last_observed_at=command.updated_at,
        finished_at=terminal if state != BackgroundTaskState.unknown else None,
        result_ref=command.log_path or None,
        result_readiness=readiness.value,
        result_unavailable_reason=(
            "legacy command log was not durably confirmed"
            if readiness == TaskResultReadiness.unavailable
            else None
        ),
        result_summary=_summary(command, state),
        created_at=command.created_at,
        updated_at=command.updated_at,
    )
    session.add(task)
    await session.flush()
    copied_wakes = await _copy_wakes(
        session,
        command=command,
        task=task,
        cancelled=cancelled,
    )
    completion = await _copy_missing_completion(
        session,
        command=command,
        task=task,
        cancelled=cancelled,
    )
    command.task_id = task.id
    command.owner_id = None
    command.owner_until = None
    await session.flush()
    return copied_wakes + completion


async def migrate_legacy_commands(
    session: AsyncSession,
    *,
    apply: bool,
    command_ids: tuple[str, ...] | None = None,
    limit: int | None = None,
) -> LegacyMigrationReport:
    """Plan or backfill legacy rows without committing the caller's transaction."""
    statement = (
        select(SandboxCommand, Conversation)
        .join(Conversation, col(Conversation.id) == col(SandboxCommand.conversation_id))
        .where(col(SandboxCommand.task_id).is_(None))
        .order_by(col(SandboxCommand.created_at), col(SandboxCommand.id))
    )
    if command_ids is not None:
        statement = statement.where(col(SandboxCommand.id).in_(command_ids))
    if apply:
        statement = statement.with_for_update(of=SandboxCommand, skip_locked=True)
    if limit is not None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        statement = statement.limit(limit)
    rows = list((await session.execute(statement)).all())
    plans = tuple(_plan(command) for command, _conversation in rows)
    migratable = tuple(item for item in plans if item.action == "migrate")
    blockers = tuple(item for item in plans if item.action == "block")
    migrated = 0
    events_migrated = 0
    if apply:
        by_id = {command.id: (command, conversation) for command, conversation in rows}
        for item in migratable:
            command, conversation = by_id[item.command_id]
            events_migrated += await _migrate_one(session, command, conversation)
            migrated += 1
        managed_statement = (
            select(SandboxCommand, Conversation, BackgroundTask)
            .join(Conversation, col(Conversation.id) == col(SandboxCommand.conversation_id))
            .join(BackgroundTask, col(BackgroundTask.id) == col(SandboxCommand.task_id))
            .order_by(col(SandboxCommand.created_at), col(SandboxCommand.id))
        )
        if command_ids is not None:
            managed_statement = managed_statement.where(col(SandboxCommand.id).in_(command_ids))
        managed_rows = list((await session.execute(managed_statement)).all())
        for command, conversation, task in managed_rows:
            cancelled = _notifications_cancelled(command, conversation)
            copied = await _copy_wakes(
                session,
                command=command,
                task=task,
                cancelled=cancelled,
            )
            events_migrated += copied
            events_migrated += await _copy_missing_completion(
                session,
                command=command,
                task=task,
                cancelled=cancelled,
            )
        await session.flush()
    return LegacyMigrationReport(
        migratable=migratable,
        blockers=blockers,
        migrated=migrated,
        events_migrated=events_migrated,
    )


async def _main_async(*, apply: bool) -> int:
    from sqlalchemy import text

    from cubeplex.db.engine import async_session_maker

    async with async_session_maker() as session:
        if apply:
            acquired = bool(
                await session.scalar(
                    text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
                    {"lock_id": _MIGRATION_LOCK_ID},
                )
            )
            if not acquired:
                print("another background-task migration owns the database lock")
                return 2
        report = await migrate_legacy_commands(session, apply=apply)
        output = {
            "mode": "apply" if apply else "dry-run",
            "migrated": report.migrated,
            "events_migrated": report.events_migrated,
            "migratable": [asdict(item) for item in report.migratable],
            "blockers": [asdict(item) for item in report.blockers],
        }
        print(json.dumps(output, indent=2, sort_keys=True))
        if report.blockers:
            if apply:
                await session.rollback()
                print("no changes committed: resolve every blocker, then rerun --apply")
            return 2
        if apply:
            await session.commit()
            print("background-task backfill committed")
        else:
            await session.rollback()
            print("dry-run only; rerun with --apply after stopping old writers")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill pre-cutover sandbox commands into background tasks."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="commit the backfill; the default is a read-only dry-run",
    )
    args = parser.parse_args()
    return asyncio.run(_main_async(apply=args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
