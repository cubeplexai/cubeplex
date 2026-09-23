"""Plan and backfill pre-cutover sandbox commands into durable tasks.

The caller owns the transaction. Dry-run is the default at the CLI layer;
``migrate_legacy_commands`` only mutates rows when ``apply=True``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Collection, Mapping
from dataclasses import asdict, dataclass
from typing import Literal

from sqlalchemy import or_, select
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
from cubeplex.models.steering_message import SteeringMessage, SteeringMessageState

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


async def load_checkpointed_notice_ids(
    session: AsyncSession,
) -> dict[str, frozenset[str]]:
    """Read legacy delivery proof before deciding whether an event is claimable."""
    from cubeplex.agents.checkpointer import init_checkpointer

    conversation_ids = set(
        await session.scalars(select(col(SandboxCommand.conversation_id)).distinct())
    )
    result: dict[str, frozenset[str]] = {}
    async with init_checkpointer(min_pool_size=1, max_pool_size=1) as checkpointer:
        for conversation_id in conversation_ids:
            checkpoint = await checkpointer.load(conversation_id)
            notice_ids: set[str] = set()
            if checkpoint is not None:
                for message in checkpoint.messages:
                    metadata = getattr(message, "metadata", None)
                    notice_id = metadata.get("notice_id") if isinstance(metadata, dict) else None
                    if isinstance(notice_id, str):
                        notice_ids.add(notice_id)
            result[conversation_id] = frozenset(notice_ids)
    injected_notice_ids = await _load_injected_notice_ids(session)
    mutable = {conversation_id: set(ids) for conversation_id, ids in result.items()}
    for conversation_id, injected_ids in injected_notice_ids.items():
        mutable.setdefault(conversation_id, set()).update(injected_ids)
    return {conversation_id: frozenset(ids) for conversation_id, ids in mutable.items()}


async def _load_injected_notice_ids(
    session: AsyncSession,
) -> dict[str, frozenset[str]]:
    """Read durable steering rows that prove a legacy wake was injected."""
    injected_wakes = await session.execute(
        select(
            col(SandboxCommandWake.conversation_id),
            col(SandboxCommandWake.id),
        )
        .join(
            SteeringMessage,
            (col(SteeringMessage.conversation_id) == col(SandboxCommandWake.conversation_id))
            & (col(SteeringMessage.client_steer_id) == col(SandboxCommandWake.delivery_steer_id)),
        )
        .where(col(SteeringMessage.state) == SteeringMessageState.injected.value)
    )
    mutable: dict[str, set[str]] = {}
    for conversation_id, wake_id in injected_wakes:
        mutable.setdefault(conversation_id, set()).add(wake_id)
    return {conversation_id: frozenset(ids) for conversation_id, ids in mutable.items()}


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
    if command.status in _ACTIVE and not command.sandbox_instance_id:
        return LegacyCommandPlan(
            command_id=command.id,
            action="block",
            reason="active legacy command lacks original sandbox instance evidence",
        )
    if command.status in _ACTIVE and not command.provider_ref:
        return LegacyCommandPlan(
            command_id=command.id,
            action="block",
            reason="active legacy command lacks a recoverable provider process handle",
        )
    return LegacyCommandPlan(
        command_id=command.id,
        action="migrate",
        reason=(
            "active command will remain unknown with its original instance evidence preserved"
            if command.status in _ACTIVE
            else "terminal command evidence can be preserved"
        ),
    )


def _task_state(command: SandboxCommand) -> BackgroundTaskState:
    if command.status == SandboxCommandStatus.killed.value:
        return BackgroundTaskState.cancelled
    if command.status == SandboxCommandStatus.exited.value:
        if command.exit_code is None:
            return BackgroundTaskState.failed
        return (
            BackgroundTaskState.succeeded if command.exit_code == 0 else BackgroundTaskState.failed
        )
    return BackgroundTaskState.unknown


def _notifications_cancelled(
    command: SandboxCommand,
    conversation: Conversation,
    admission: ConversationExecutionAdmission,
) -> bool:
    return bool(
        not command.notify_on_complete
        or command.kind == SandboxCommandKind.monitor.value
        or command.status in (SandboxCommandStatus.not_started.value,)
        or conversation.deleted_at is not None
        or conversation.execution_closed_at is not None
        or admission.execution_generation != conversation.execution_generation
        or admission.revoked_at is not None
        or admission.run_stop_requested_at is not None
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
        if command.exit_code is None:
            return "Legacy command exited, but its status was unavailable."
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
    run_admission = await session.scalar(
        select(ConversationExecutionAdmission)
        .where(
            col(ConversationExecutionAdmission.org_id) == command.org_id,
            col(ConversationExecutionAdmission.workspace_id) == command.workspace_id,
            col(ConversationExecutionAdmission.conversation_id) == command.conversation_id,
            col(ConversationExecutionAdmission.actor_user_id) == command.started_by_user_id,
            col(ConversationExecutionAdmission.run_id) == command.run_id,
            or_(
                col(ConversationExecutionAdmission.source_kind)
                != ExecutionSourceKind.background_task.value,
                col(ConversationExecutionAdmission.source_id).not_like("legacy-command:%"),
            ),
        )
        .order_by(col(ConversationExecutionAdmission.id))
        .limit(1)
    )
    if run_admission is not None:
        return run_admission
    terminal = command.finished_at or command.updated_at
    admission = ConversationExecutionAdmission(
        org_id=command.org_id,
        workspace_id=command.workspace_id,
        conversation_id=command.conversation_id,
        actor_user_id=command.started_by_user_id,
        source_kind=ExecutionSourceKind.background_task.value,
        source_id=source_id,
        execution_generation=0,
        run_id=command.run_id,
        run_start_requested_at=command.created_at,
        run_started_at=command.created_at,
        run_finished_at=terminal if command.status not in _ACTIVE else None,
        run_terminal_status=("completed" if command.status not in _ACTIVE else None),
        run_terminal_at=terminal if command.status not in _ACTIVE else None,
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
    checkpointed_notice_ids: Collection[str],
) -> tuple[BackgroundTaskEventState, str | None]:
    if (
        wake.state == SandboxCommandWakeState.delivered.value
        or wake.id in checkpointed_notice_ids
        or (wake.reason == "completion" and command.id in checkpointed_notice_ids)
    ):
        return BackgroundTaskEventState.delivered, None
    if command.kind == SandboxCommandKind.monitor.value:
        return BackgroundTaskEventState.discarded, "legacy_monitor_subscription"
    if cancelled:
        return BackgroundTaskEventState.discarded, "legacy_notification_revoked"
    return BackgroundTaskEventState.pending, None


def _reconcile_event_state(
    event: BackgroundTaskEvent,
    *,
    state: BackgroundTaskEventState,
    discard_reason: str | None,
) -> bool:
    if (
        state == BackgroundTaskEventState.pending
        and event.state
        in (
            BackgroundTaskEventState.pending.value,
            BackgroundTaskEventState.claimed.value,
        )
        and event.delivery_attempt_id is None
    ):
        changed = (
            any(
                value is not None
                for value in (
                    event.discard_reason,
                    event.owner_token,
                    event.owner_until,
                    event.delivery_run_id,
                    event.delivery_input_id,
                    event.checkpoint_run_id,
                    event.checkpoint_input_id,
                    event.delivered_at,
                )
            )
            or event.state != BackgroundTaskEventState.pending.value
        )
        if not changed:
            return False
        event.state = BackgroundTaskEventState.pending.value
        event.discard_reason = None
        event.owner_token = None
        event.owner_until = None
        event.delivery_run_id = None
        event.delivery_input_id = None
        event.checkpoint_run_id = None
        event.checkpoint_input_id = None
        event.delivered_at = None
        event.revision += 1
        return True
    if state == BackgroundTaskEventState.delivered:
        if event.state == BackgroundTaskEventState.delivered.value:
            return False
        event.state = BackgroundTaskEventState.delivered.value
        event.discard_reason = None
        event.owner_token = None
        event.owner_until = None
        event.delivery_attempt_id = None
        event.delivered_at = event.delivered_at or event.updated_at
        event.revision += 1
        return True
    if state == BackgroundTaskEventState.discarded and event.state in (
        BackgroundTaskEventState.pending.value,
        BackgroundTaskEventState.claimed.value,
    ):
        event.state = BackgroundTaskEventState.discarded.value
        event.discard_reason = discard_reason
        event.owner_token = None
        event.owner_until = None
        event.delivery_attempt_id = None
        event.revision += 1
        return True
    return False


async def _copy_wakes(
    session: AsyncSession,
    *,
    command: SandboxCommand,
    task: BackgroundTask,
    cancelled: bool,
    checkpointed_notice_ids: Collection[str],
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
        state, discard_reason = _event_state(
            command,
            wake,
            cancelled=cancelled,
            checkpointed_notice_ids=checkpointed_notice_ids,
        )
        existing = await session.get(BackgroundTaskEvent, wake.id)
        if existing is not None:
            if existing.task_id != task.id:
                raise RuntimeError(f"legacy notice id collision: {wake.id}")
            copied += int(
                _reconcile_event_state(
                    existing,
                    state=state,
                    discard_reason=discard_reason,
                )
            )
            continue
        delivered = state == BackgroundTaskEventState.delivered
        claimable = state == BackgroundTaskEventState.pending
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
                delivery_run_id=None if claimable else wake.delivery_run_id,
                delivery_input_id=None if claimable else wake.delivery_steer_id,
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
    checkpointed_notice_ids: Collection[str],
) -> int:
    if (
        command.kind != SandboxCommandKind.execute.value
        or command.status in _ACTIVE
        or command.notice_state != SandboxCommandNoticeState.pending.value
    ):
        return 0
    wake_id = await session.scalar(
        select(col(SandboxCommandWake.id)).where(col(SandboxCommandWake.command_id) == command.id)
    )
    if wake_id is not None:
        return 0
    delivered = command.id in checkpointed_notice_ids
    if delivered:
        state = BackgroundTaskEventState.delivered
        discard_reason = None
    elif cancelled:
        state = BackgroundTaskEventState.discarded
        discard_reason = "legacy_notification_revoked"
    else:
        state = BackgroundTaskEventState.pending
        discard_reason = None
    existing = await session.scalar(
        select(col(BackgroundTaskEvent.id)).where(
            col(BackgroundTaskEvent.task_id) == task.id,
            col(BackgroundTaskEvent.dedupe_key) == "completion",
        )
    )
    if existing is not None:
        event = await session.get(BackgroundTaskEvent, existing)
        assert event is not None
        return int(
            _reconcile_event_state(
                event,
                state=state,
                discard_reason=discard_reason,
            )
        )
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
            discard_reason=discard_reason,
            delivered_at=command.updated_at if delivered else None,
            created_at=command.updated_at,
            updated_at=command.updated_at,
        )
    )
    return 1


async def _migrate_one(
    session: AsyncSession,
    command: SandboxCommand,
    conversation: Conversation,
    checkpointed_notice_ids: Collection[str],
) -> int:
    admission = await _admission_for(session, command, conversation)
    state = _task_state(command)
    cancelled = _notifications_cancelled(command, conversation, admission)
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
        execution_generation=admission.execution_generation,
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
        checkpointed_notice_ids=checkpointed_notice_ids,
    )
    completion = await _copy_missing_completion(
        session,
        command=command,
        task=task,
        cancelled=cancelled,
        checkpointed_notice_ids=checkpointed_notice_ids,
    )
    if command.status in _ACTIVE and command.start_requested_at is None:
        command.start_requested_at = command.created_at
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
    checkpointed_notice_ids: Mapping[str, Collection[str]] | None = None,
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
        checkpointed = checkpointed_notice_ids or {}
        by_id = {command.id: (command, conversation) for command, conversation in rows}
        for item in migratable:
            command, conversation = by_id[item.command_id]
            events_migrated += await _migrate_one(
                session,
                command,
                conversation,
                checkpointed.get(command.conversation_id, frozenset()),
            )
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
            admission = await session.get(ConversationExecutionAdmission, task.admission_id)
            if admission is None:
                raise RuntimeError(f"background task admission is missing: {task.id}")
            cancelled = _notifications_cancelled(command, conversation, admission)
            copied = await _copy_wakes(
                session,
                command=command,
                task=task,
                cancelled=cancelled,
                checkpointed_notice_ids=checkpointed.get(command.conversation_id, frozenset()),
            )
            events_migrated += copied
            events_migrated += await _copy_missing_completion(
                session,
                command=command,
                task=task,
                cancelled=cancelled,
                checkpointed_notice_ids=checkpointed.get(command.conversation_id, frozenset()),
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
        checkpointed_notice_ids = await load_checkpointed_notice_ids(session) if apply else None
        report = await migrate_legacy_commands(
            session,
            apply=apply,
            checkpointed_notice_ids=checkpointed_notice_ids,
        )
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
