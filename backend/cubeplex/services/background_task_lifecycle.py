"""Scoped lifecycle transitions; callers commit before any provider I/O."""

from dataclasses import dataclass
from datetime import datetime
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
    TaskResultReadiness,
    TaskStopReason,
)
from cubeplex.models.conversation import Conversation
from cubeplex.models.conversation_execution import ConversationExecutionAdmission
from cubeplex.models.membership import Membership
from cubeplex.models.sandbox_command import MonitorOutcome, SandboxCommand
from cubeplex.models.user_sandbox import UserSandbox
from cubeplex.repositories.background_task import BackgroundTaskRepository
from cubeplex.repositories.conversation import ConversationRepository
from cubeplex.sandbox.base import ProcessSnapshot

LogState = Literal["pending", "retrying", "complete", "unavailable"]
USER_STOP_REASONS = frozenset(
    {
        TaskStopReason.user_stop,
        TaskStopReason.run_stop,
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


def command_result_readiness(*, state: str, log_state: str) -> TaskResultReadiness:
    if state not in TERMINAL_TASK_STATES:
        return TaskResultReadiness.pending
    if log_state == "complete":
        return TaskResultReadiness.ready
    if log_state == "unavailable":
        return TaskResultReadiness.unavailable
    return TaskResultReadiness.pending


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
                select(
                    col(BackgroundTask.conversation_id),
                    col(BackgroundTask.admission_id),
                    col(SandboxCommand.user_sandbox_id),
                )
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
        admission = await self.session.scalar(
            select(ConversationExecutionAdmission)
            .where(
                col(ConversationExecutionAdmission.id) == header.admission_id,
                col(ConversationExecutionAdmission.org_id) == self.org_id,
                col(ConversationExecutionAdmission.workspace_id) == self.workspace_id,
                col(ConversationExecutionAdmission.conversation_id) == header.conversation_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if admission is None:
            raise LookupError("execution admission not found")
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

    async def _execution_stop_reason(
        self, conversation: Conversation, task: BackgroundTask
    ) -> TaskStopReason | None:
        if conversation.deleted_at is not None:
            return TaskStopReason.conversation_deleted
        if (
            conversation.execution_closed_at is not None
            or conversation.execution_generation != task.execution_generation
        ):
            return TaskStopReason.conversation_stop
        admission = await self.session.get(ConversationExecutionAdmission, task.admission_id)
        if admission is None or admission.revoked_at is not None:
            return TaskStopReason.user_stop
        if task.backgrounded_at is None and admission.run_stop_requested_at is not None:
            return TaskStopReason.run_stop
        return None

    @staticmethod
    def needs_management(task: BackgroundTask, command: SandboxCommand) -> bool:
        return (
            task.state in INFLIGHT_TASK_STATES
            or command.log_state in ("pending", "retrying")
            or (
                task.backgrounded_at is None
                and task.foreground_result_delivered_at is None
                and task.notifications_cancelled_at is None
            )
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
            await self._execution_stop_reason(conversation, task) is not None
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
        reason = await self._execution_stop_reason(conversation, task)
        if reason is None and (
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
            task.finished_at = command.finished_at = now
            if command.monitor_outcome is None:
                task.result_summary = "command was never submitted; it was not restarted"
        command.status = "not_started"
        command.log_state = "complete"
        task.revision += 1
        await self._ensure_completion(conversation, task, command, now)
        await self.session.flush()

    async def record_missing_command_instance(
        self, *, task_id: str, owner_token: str, now: datetime
    ) -> None:
        conversation, _, task, command = await self._lock_command_task(task_id)
        self._require_owner(task, owner_token, now)
        if command.sandbox_instance_id is not None or task.state not in TERMINAL_TASK_STATES:
            raise ValueError("missing instance cannot settle an unfinished command")
        if command.log_state in ("pending", "retrying"):
            command.log_state = "unavailable"
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
            if command.monitor_outcome is None:
                task.result_summary = "original sandbox was destroyed; process exit code is unknown"
        if command.log_state != "complete":
            command.log_state = "unavailable"
            task.result_unavailable_reason = (
                "original sandbox was destroyed before final output could be collected"
            )
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
            if (
                task.notifications_cancelled_at is not None
                or await self._execution_stop_reason(conversation, task) is not None
            ):
                raise ValueError("stopped foreground work cannot be handed to the background")
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
            or task.result_readiness == TaskResultReadiness.pending
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
        return await self._request_task_stop(
            task_id=task_id,
            reason=reason,
            now=now,
            include_descendants=True,
        )

    async def request_tasks_stop(
        self, *, task_ids: list[str], reason: TaskStopReason, now: datetime
    ) -> list[str]:
        """Stop only the selected tasks, without crossing an environment boundary."""
        selected: set[str] = set()
        for task_id in dict.fromkeys(task_ids):
            selected.update(
                await self._request_task_stop(
                    task_id=task_id,
                    reason=reason,
                    now=now,
                    include_descendants=False,
                )
            )
        return sorted(selected)

    async def _request_task_stop(
        self,
        *,
        task_id: str,
        reason: TaskStopReason,
        now: datetime,
        include_descendants: bool,
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
        if include_descendants:
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
        commands = (
            await self.session.execute(
                select(SandboxCommand)
                .where(
                    col(SandboxCommand.org_id) == self.org_id,
                    col(SandboxCommand.workspace_id) == self.workspace_id,
                    col(SandboxCommand.task_id).in_(selected),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalars()
        by_task = {task.id: task for task in rows}
        for command in commands:
            assert command.task_id is not None
            await self._ensure_completion(conversation, by_task[command.task_id], command, now)
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
                if command.monitor_outcome is None:
                    task.result_summary = (
                        f"{command.kind} {task.state}; exit_code={snapshot.exit_code}"
                        + (f"; stop_reason={task.stop_reason}" if task.stop_reason else "")
                    )
        task.last_observed_at = now
        if command.log_state not in ("complete", "unavailable"):
            command.log_state = log_state
        if confirmed_log_cursor is not None:
            command.log_cursor = confirmed_log_cursor
        task.revision += 1
        await self._ensure_completion(conversation, task, command, now)
        await self.session.flush()

    async def record_observation_failure(
        self, *, task_id: str, owner_token: str, now: datetime, message: str
    ) -> None:
        _, _, task, command = await self._lock_command_task(task_id)
        self._require_owner(task, owner_token, now)
        if task.state not in TERMINAL_TASK_STATES:
            task.state = BackgroundTaskState.unknown.value
            if command.monitor_outcome is None:
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
        task.result_readiness = command_result_readiness(
            state=task.state, log_state=command.log_state
        ).value
        if task.result_readiness == TaskResultReadiness.unavailable:
            task.result_unavailable_reason = (
                task.result_unavailable_reason or "final command output could not be recovered"
            )
        if command.kind == "monitor" and command.monitor_outcome is None:
            outcome: MonitorOutcome | None = None
            if task.stop_reason == TaskStopReason.deadline:
                outcome = MonitorOutcome.timed_out
            elif task.state in TERMINAL_TASK_STATES:
                outcome = (
                    MonitorOutcome.matched
                    if task.state == BackgroundTaskState.succeeded
                    else MonitorOutcome.failed
                )
            if outcome is not None:
                command.monitor_outcome = outcome.value
                task.result_ref = command.log_path or None
                task.result_summary = f"monitor {outcome.value}; {task.result_summary}".rstrip("; ")
        has_result = (
            command.monitor_outcome is not None
            if command.kind == "monitor"
            else task.state in TERMINAL_TASK_STATES
        )
        if (
            not has_result
            or task.backgrounded_at is None
            or task.foreground_result_delivered_at is not None
            or not task.notify_on_complete
            or task.notifications_cancelled_at is not None
            or await self._execution_stop_reason(conversation, task) is not None
        ):
            return
        reason = "monitor_result" if command.kind == "monitor" else "completion"
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
