"""Scoped lifecycle transitions; callers commit before any provider I/O."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from cubeplex.models.background_task import (
    INFLIGHT_TASK_STATES,
    TERMINAL_TASK_STATES,
    BackgroundTask,
    BackgroundTaskEvent,
    BackgroundTaskState,
    TaskStopReason,
)
from cubeplex.models.conversation import Conversation
from cubeplex.models.membership import Membership
from cubeplex.models.sandbox_command import SandboxCommand
from cubeplex.models.user_sandbox import UserSandbox
from cubeplex.repositories.background_task import BackgroundTaskRepository
from cubeplex.repositories.conversation import ConversationRepository
from cubeplex.sandbox.base import ProcessSnapshot

LogState = Literal["pending", "retrying", "complete", "unavailable"]
USER_STOP_REASONS = frozenset(
    {
        TaskStopReason.user_stop,
        TaskStopReason.conversation_stop,
        TaskStopReason.conversation_deleted,
    }
)


class TaskOwnerLostError(ValueError):
    """A stale worker cannot write an observation or start new work."""


@dataclass(frozen=True)
class ForegroundResultEvidence:
    """The host supplies this only after finding the final result in a checkpoint."""

    run_id: str
    tool_call_id: str
    agent_id: str | None = None


def command_state(snapshot: ProcessSnapshot) -> BackgroundTaskState:
    if snapshot.status == "running":
        return BackgroundTaskState.running
    if snapshot.status == "killed":
        return BackgroundTaskState.cancelled
    if snapshot.exit_code is None:
        return BackgroundTaskState.unknown
    return BackgroundTaskState.succeeded if snapshot.exit_code == 0 else BackgroundTaskState.failed


def require_aware(moment: datetime) -> None:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("task timestamps must be timezone-aware")


class BackgroundTaskLifecycle:
    def __init__(self, session: AsyncSession, *, org_id: str, workspace_id: str) -> None:
        self.session = session
        self.org_id = org_id
        self.workspace_id = workspace_id
        self.tasks = BackgroundTaskRepository(session, org_id=org_id, workspace_id=workspace_id)

    async def _lock_command_task(
        self, task_id: str
    ) -> tuple[Conversation, UserSandbox, BackgroundTask, SandboxCommand]:
        header = (
            await self.session.execute(
                select(col(BackgroundTask.conversation_id), col(SandboxCommand.user_sandbox_id))
                .join(SandboxCommand, col(SandboxCommand.task_id) == col(BackgroundTask.id))
                .where(
                    col(BackgroundTask.id) == task_id,
                    col(BackgroundTask.org_id) == self.org_id,
                    col(BackgroundTask.workspace_id) == self.workspace_id,
                    col(SandboxCommand.org_id) == self.org_id,
                    col(SandboxCommand.workspace_id) == self.workspace_id,
                )
            )
        ).one_or_none()
        if header is None:
            raise LookupError("task not found")
        # Cleanup must still lock deleted conversations and replaced sandbox rows.
        conversation = (
            await self.session.execute(
                select(Conversation)
                .where(
                    col(Conversation.id) == header.conversation_id,
                    col(Conversation.org_id) == self.org_id,
                    col(Conversation.workspace_id) == self.workspace_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        sandbox = (
            await self.session.execute(
                select(UserSandbox)
                .where(
                    col(UserSandbox.id) == header.user_sandbox_id,
                    col(UserSandbox.org_id) == self.org_id,
                    col(UserSandbox.workspace_id) == self.workspace_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        task = await self.tasks.get_locked(task_id)
        command = (
            await self.session.execute(
                select(SandboxCommand)
                .where(
                    col(SandboxCommand.task_id) == task_id,
                    col(SandboxCommand.org_id) == self.org_id,
                    col(SandboxCommand.workspace_id) == self.workspace_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if conversation is None or sandbox is None or task is None or command is None:
            raise LookupError("task not found")
        return conversation, sandbox, task, command

    @staticmethod
    def _require_owner(task: BackgroundTask, owner_token: str, now: datetime) -> None:
        require_aware(now)
        if (
            not owner_token
            or task.owner_token != owner_token
            or task.owner_until is None
            or task.owner_until <= now
        ):
            raise TaskOwnerLostError("task owner lease is no longer valid")

    @staticmethod
    def _execution_open(conversation: Conversation, task: BackgroundTask) -> bool:
        return (
            conversation.deleted_at is None
            and conversation.execution_closed_at is None
            and conversation.execution_generation == task.execution_generation
        )

    @staticmethod
    def needs_management(task: BackgroundTask, command: SandboxCommand) -> bool:
        return (
            task.state in INFLIGHT_TASK_STATES
            or command.log_state in ("pending", "retrying")
            or (task.backgrounded_at is None and task.foreground_result_delivered_at is None)
        )

    async def claim_task(
        self, *, task_id: str, owner_token: str, now: datetime, owner_until: datetime
    ) -> bool:
        require_aware(now)
        require_aware(owner_until)
        if not owner_token or len(owner_token) > 64 or owner_until <= now:
            raise ValueError("a claim requires a live owner lease")
        _, _, task, command = await self._lock_command_task(task_id)
        if task.owner_until is not None and task.owner_until > now:
            return False
        if not self.needs_management(task, command):
            return False
        task.owner_token = owner_token
        task.owner_until = owner_until
        task.revision += 1
        await self.session.flush()
        return True

    async def renew_owner(
        self, *, task_id: str, owner_token: str, now: datetime, owner_until: datetime
    ) -> None:
        require_aware(owner_until)
        _, sandbox, task, command = await self._lock_command_task(task_id)
        self._require_owner(task, owner_token, now)
        if owner_until <= now:
            raise ValueError("owner lease must end after now")
        task.owner_until = owner_until
        if sandbox.sandbox_id == command.sandbox_instance_id and (
            sandbox.in_use_until is None or sandbox.in_use_until < owner_until
        ):
            sandbox.in_use_until = owner_until
        await self.session.flush()

    async def begin_start(self, *, task_id: str, owner_token: str, now: datetime) -> bool:
        conversation, sandbox, task, command = await self._lock_command_task(task_id)
        self._require_owner(task, owner_token, now)
        if (
            not self._execution_open(conversation, task)
            or task.stop_requested_at is not None
            or (task.deadline_at is not None and task.deadline_at <= now)
            or task.state not in INFLIGHT_TASK_STATES
            or command.start_requested_at is not None
            or command.provider_ref is not None
        ):
            return False
        if await self._ancestor_stop_reason(task, now) is not None:
            return False
        if (
            sandbox.deleted_at is not None
            or sandbox.status != "running"
            or sandbox.sandbox_id != command.sandbox_instance_id
            or sandbox.provider != command.provider
            or not command.sandbox_instance_id
        ):
            return False
        accessible = ConversationRepository(
            self.session,
            org_id=self.org_id,
            workspace_id=self.workspace_id,
            user_id=task.started_by_user_id,
        ).accessible_id_subquery()
        member = (
            select(col(Membership.user_id))
            .where(
                col(Membership.user_id) == task.started_by_user_id,
                col(Membership.workspace_id) == self.workspace_id,
            )
            .exists()
        )
        allowed = await self.session.scalar(
            select(col(Conversation.id)).where(
                col(Conversation.id) == task.conversation_id,
                col(Conversation.id).in_(accessible),
                member,
            )
        )
        if allowed is None:
            return False
        command.start_token = owner_token
        command.start_requested_at = now
        task.revision += 1
        await self.session.flush()
        return True

    async def _ancestor_stop_reason(
        self, task: BackgroundTask, now: datetime
    ) -> TaskStopReason | None:
        parent_id = task.parent_task_id
        visited = {task.id}
        while parent_id is not None:
            if parent_id in visited:
                raise ValueError("invalid cyclic task ancestry")
            visited.add(parent_id)
            parent = await self.tasks.get_locked(parent_id)
            if parent is None or parent.conversation_id != task.conversation_id:
                raise LookupError("parent task not found")
            if parent.execution_generation != task.execution_generation:
                return TaskStopReason.conversation_stop
            if parent.notifications_cancelled_at is not None:
                return TaskStopReason.user_stop
            if parent.stop_requested_at is not None:
                return TaskStopReason(parent.stop_reason or TaskStopReason.user_stop)
            if parent.deadline_at is not None and parent.deadline_at <= now:
                return TaskStopReason.deadline
            parent_id = parent.parent_task_id
        return None

    async def prepare_observation(
        self, *, task_id: str, owner_token: str, now: datetime
    ) -> tuple[BackgroundTask, SandboxCommand]:
        conversation, _, task, command = await self._lock_command_task(task_id)
        self._require_owner(task, owner_token, now)
        reason: TaskStopReason | None = None
        if conversation.deleted_at is not None:
            reason = TaskStopReason.conversation_deleted
        elif not self._execution_open(conversation, task):
            reason = TaskStopReason.conversation_stop
        elif (
            task.state in INFLIGHT_TASK_STATES
            and task.deadline_at is not None
            and task.deadline_at <= now
        ):
            reason = TaskStopReason.deadline
        if reason is None:
            reason = await self._ancestor_stop_reason(task, now)
        if reason is not None:
            await self.request_task_stop(task_id=task_id, reason=reason, now=now)
        return task, command

    async def defer_owner(
        self, *, task_id: str, owner_token: str, now: datetime, retry_at: datetime
    ) -> None:
        require_aware(retry_at)
        _, _, task, _ = await self._lock_command_task(task_id)
        self._require_owner(task, owner_token, now)
        if retry_at <= now:
            raise ValueError("retry must be in the future")
        task.owner_token = None
        task.owner_until = retry_at
        await self.session.flush()

    async def record_not_started(self, *, task_id: str, owner_token: str, now: datetime) -> None:
        conversation, _, task, command = await self._lock_command_task(task_id)
        self._require_owner(task, owner_token, now)
        if command.start_requested_at is not None or command.provider_ref is not None:
            raise ValueError("a submitted start cannot be declared unstarted")
        if task.state not in TERMINAL_TASK_STATES:
            task.state = "cancelled" if task.stop_requested_at is not None else "failed"
            command.status = "not_started"
            task.finished_at = command.finished_at = now
            task.result_summary = "command was never submitted; it was not restarted"
            command.log_state = "complete"
            task.revision += 1
        await self._ensure_completion(conversation, task, command, now)
        await self.session.flush()

    async def record_environment_gone(
        self, *, task_id: str, owner_token: str, sandbox_instance_id: str, now: datetime
    ) -> None:
        conversation, _, task, command = await self._lock_command_task(task_id)
        self._require_owner(task, owner_token, now)
        if not sandbox_instance_id or command.sandbox_instance_id != sandbox_instance_id:
            raise ValueError("environment proof does not identify the original instance")
        if task.state not in TERMINAL_TASK_STATES:
            task.state = "cancelled" if task.stop_requested_at is not None else "failed"
            task.finished_at = command.finished_at = now
            command.status = "killed"
            task.result_ref = command.log_path or None
            task.result_summary = "original sandbox was destroyed; process exit code is unknown"
        if command.log_state != "complete":
            command.log_state = "unavailable"
        task.last_observed_at = now
        task.revision += 1
        await self._ensure_completion(conversation, task, command, now)
        await self.session.flush()

    async def register_start_receipt(
        self,
        *,
        task_id: str,
        start_token: str,
        sandbox_instance_id: str,
        provider_ref: str,
        now: datetime,
    ) -> None:
        require_aware(now)
        _, _, task, command = await self._lock_command_task(task_id)
        if (
            not start_token
            or command.start_token != start_token
            or command.start_requested_at is None
            or command.sandbox_instance_id != sandbox_instance_id
            or not provider_ref
            or len(provider_ref) > 255
        ):
            raise ValueError("receipt does not match the original start")
        if command.provider_ref is not None and command.provider_ref != provider_ref:
            raise ValueError("start receipt conflicts with the original provider handle")
        # A late receipt may add its handle, never restore authority or erase a Stop.
        command.provider_ref = provider_ref
        task.revision += 1
        await self.session.flush()

    async def handoff_task(self, *, task_id: str, owner_token: str, now: datetime) -> None:
        conversation, _, task, command = await self._lock_command_task(task_id)
        self._require_owner(task, owner_token, now)
        if task.foreground_result_delivered_at is not None:
            raise ValueError("final result was already delivered in the foreground")
        if task.backgrounded_at is None:
            task.backgrounded_at = now
            task.revision += 1
        await self._ensure_completion(conversation, task, command, now)
        await self.session.flush()

    async def record_foreground_delivery(
        self, *, task_id: str, owner_token: str, evidence: ForegroundResultEvidence, now: datetime
    ) -> None:
        _, _, task, command = await self._lock_command_task(task_id)
        self._require_owner(task, owner_token, now)
        if (
            task.backgrounded_at is not None
            or task.state not in TERMINAL_TASK_STATES
            or command.log_state not in ("complete", "unavailable")
            or (task.originating_run_id, task.tool_call_id, task.agent_id)
            != (evidence.run_id, evidence.tool_call_id, evidence.agent_id)
        ):
            raise ValueError("checkpoint does not prove this task's final foreground result")
        task.foreground_result_delivered_at = task.foreground_result_delivered_at or now
        task.revision += 1
        await self.session.flush()

    async def request_task_stop(
        self, *, task_id: str, reason: TaskStopReason, now: datetime
    ) -> list[str]:
        require_aware(now)
        conversation, _, _, _ = await self._lock_command_task(task_id)
        rows = list(
            (
                await self.session.execute(
                    select(BackgroundTask)
                    .where(
                        col(BackgroundTask.org_id) == self.org_id,
                        col(BackgroundTask.workspace_id) == self.workspace_id,
                        col(BackgroundTask.conversation_id) == conversation.id,
                    )
                    .order_by(col(BackgroundTask.id))
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalars()
        )
        selected = {task_id}
        while True:
            children = {row.id for row in rows if row.parent_task_id in selected}
            if children <= selected:
                break
            selected.update(children)
        for task in rows:
            if task.id not in selected:
                continue
            task.stop_requested_at = task.stop_requested_at or now
            if task.stop_reason is None or reason in USER_STOP_REASONS:
                task.stop_reason = reason.value
            if reason in USER_STOP_REASONS:
                task.notifications_cancelled_at = task.notifications_cancelled_at or now
            task.revision += 1
        if reason in USER_STOP_REASONS:
            notices = (
                await self.session.execute(
                    select(BackgroundTaskEvent)
                    .where(
                        col(BackgroundTaskEvent.org_id) == self.org_id,
                        col(BackgroundTaskEvent.workspace_id) == self.workspace_id,
                        col(BackgroundTaskEvent.task_id).in_(selected),
                        col(BackgroundTaskEvent.state) == "pending",
                        col(BackgroundTaskEvent.delivery_attempt_id).is_(None),
                    )
                    .with_for_update()
                )
            ).scalars()
            for notice in notices:
                notice.state = "discarded"
                notice.discard_reason = reason.value
                notice.revision += 1
        await self.session.flush()
        return sorted(selected)

    async def record_observation(
        self,
        *,
        task_id: str,
        owner_token: str,
        snapshot: ProcessSnapshot,
        log_state: LogState,
        now: datetime,
        expected_log_cursor: str | None = None,
        confirmed_log_cursor: str | None = None,
    ) -> None:
        conversation, _, task, command = await self._lock_command_task(task_id)
        self._require_owner(task, owner_token, now)
        if command.log_cursor != expected_log_cursor:
            raise TaskOwnerLostError("owner observation has a stale log cursor")
        if task.last_observed_at is not None and task.last_observed_at > now:
            raise TaskOwnerLostError("owner observation is older than the stored fact")
        if task.state not in TERMINAL_TASK_STATES:
            task.state = command_state(snapshot).value
            command.status = snapshot.status
            command.exit_code = snapshot.exit_code
            if task.state in TERMINAL_TASK_STATES:
                task.finished_at = now
                command.finished_at = now
                task.result_ref = command.log_path or None
                task.result_summary = (
                    f"{command.kind} {task.state}; exit_code={snapshot.exit_code}"
                    + (f"; stop_reason={task.stop_reason}" if task.stop_reason else "")
                )
        task.last_observed_at = now
        if command.log_state not in ("complete", "unavailable"):
            command.log_state = log_state
        if confirmed_log_cursor is not None:
            if command.kind == "monitor" and command.log_cursor != confirmed_log_cursor:
                await self._record_monitor_output(
                    conversation, task, command, snapshot.new_output, confirmed_log_cursor, now
                )
            elif command.kind == "monitor" and not snapshot.new_output.strip():
                command.flood_started_at = None
            command.log_cursor = confirmed_log_cursor
        task.revision += 1
        await self._ensure_completion(conversation, task, command, now)
        await self.session.flush()

    async def _record_monitor_output(
        self,
        conversation: Conversation,
        task: BackgroundTask,
        command: SandboxCommand,
        output: str,
        cursor: str,
        now: datetime,
    ) -> None:
        if (
            task.state != BackgroundTaskState.running
            or task.backgrounded_at is None
            or task.stop_requested_at is not None
            or task.notifications_cancelled_at is not None
            or not task.notify_on_complete
            or not self._execution_open(conversation, task)
        ):
            return
        lines = [line for line in output.splitlines() if line.strip()]
        if not lines:
            command.flood_started_at = None
            return
        # Preserve the monitor policy: one notice per 15s, eight total, three drops
        # disable line notices; sustained flooding for 30s requests process Stop.
        flood = False
        if not command.line_wakes_disabled:
            last = await self.session.scalar(
                select(col(BackgroundTaskEvent.created_at))
                .where(
                    col(BackgroundTaskEvent.org_id) == self.org_id,
                    col(BackgroundTaskEvent.workspace_id) == self.workspace_id,
                    col(BackgroundTaskEvent.task_id) == task.id,
                    col(BackgroundTaskEvent.reason) == "line",
                )
                .order_by(col(BackgroundTaskEvent.created_at).desc())
                .limit(1)
            )
            if last is None or (now - last).total_seconds() >= 15:
                self.session.add(
                    BackgroundTaskEvent(
                        org_id=self.org_id,
                        workspace_id=self.workspace_id,
                        task_id=task.id,
                        conversation_id=task.conversation_id,
                        execution_generation=task.execution_generation,
                        reason="line",
                        dedupe_key="line:" + sha256(cursor.encode()).hexdigest(),
                        summary=lines[-1][-4000:],
                        result_ref=command.log_path or None,
                        created_at=now,
                        updated_at=now,
                    )
                )
                command.wake_count += 1
                command.wake_drops = len(lines) - 1
                command.flood_started_at = now if command.wake_drops else None
                if command.wake_count >= 8 or command.wake_drops >= 3:
                    command.line_wakes_disabled = True
            else:
                command.wake_drops += len(lines)
                command.flood_started_at = command.flood_started_at or now
                flood = (now - command.flood_started_at).total_seconds() >= 30
                if command.wake_drops >= 3:
                    command.line_wakes_disabled = True
        elif len(lines) >= 3:
            command.wake_drops += len(lines)
            command.flood_started_at = command.flood_started_at or now
            flood = (now - command.flood_started_at).total_seconds() >= 30
        else:
            command.flood_started_at = None
        if flood:
            await self.request_task_stop(
                task_id=task.id, reason=TaskStopReason.output_flood, now=now
            )

    async def record_observation_failure(
        self, *, task_id: str, owner_token: str, now: datetime, message: str
    ) -> None:
        _, _, task, _ = await self._lock_command_task(task_id)
        self._require_owner(task, owner_token, now)
        if task.state not in TERMINAL_TASK_STATES:
            task.state = BackgroundTaskState.unknown.value
            task.result_summary = message[:4000]
            task.revision += 1
        await self.session.flush()

    async def _ensure_completion(
        self,
        conversation: Conversation,
        task: BackgroundTask,
        command: SandboxCommand,
        now: datetime,
    ) -> None:
        if (
            task.state not in TERMINAL_TASK_STATES
            or task.backgrounded_at is None
            or task.foreground_result_delivered_at is not None
            or not task.notify_on_complete
            or task.notifications_cancelled_at is not None
            or not self._execution_open(conversation, task)
        ):
            return
        reason = "exit" if command.kind == "monitor" else "completion"
        existing = await self.session.scalar(
            select(col(BackgroundTaskEvent.id)).where(
                col(BackgroundTaskEvent.task_id) == task.id,
                col(BackgroundTaskEvent.dedupe_key) == reason,
                col(BackgroundTaskEvent.org_id) == self.org_id,
                col(BackgroundTaskEvent.workspace_id) == self.workspace_id,
            )
        )
        if existing is None:
            self.session.add(
                BackgroundTaskEvent(
                    org_id=self.org_id,
                    workspace_id=self.workspace_id,
                    task_id=task.id,
                    conversation_id=task.conversation_id,
                    execution_generation=task.execution_generation,
                    reason=reason,
                    dedupe_key=reason,
                    summary=task.result_summary,
                    result_ref=task.result_ref,
                    created_at=now,
                    updated_at=now,
                )
            )
