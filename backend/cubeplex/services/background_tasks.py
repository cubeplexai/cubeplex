"""Durable task reservations, before any provider side effect.

Callers commit the transaction before starting remote work. This service never
commits a partial task/command pair or performs provider I/O under database locks.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from cubeplex.config import MAX_COMMAND_TIMEOUT_SECONDS, get_command_default_timeout_seconds
from cubeplex.models.background_task import INFLIGHT_TASK_STATES, BackgroundTask
from cubeplex.models.conversation import Conversation
from cubeplex.models.membership import Membership
from cubeplex.models.sandbox_command import SandboxCommand, SandboxCommandKind
from cubeplex.models.topic import Topic
from cubeplex.models.user_sandbox import UserSandbox
from cubeplex.repositories.background_task import (
    ConversationExecutionAdmissionRepository,
)
from cubeplex.repositories.conversation import ConversationRepository
from cubeplex.repositories.sandbox_command import MAX_INFLIGHT_COMMANDS, SandboxCommandCapError
from cubeplex.services.background_task_lifecycle import BackgroundTaskLifecycle


class TaskExecutionRevokedError(ValueError):
    """The admission or an ancestor no longer permits new work."""


class TaskEnvironmentChangedError(ValueError):
    """The attachment does not identify the currently reserved execution instance."""


@dataclass(frozen=True)
class TaskSpec:
    originating_run_id: str
    tool_call_id: str
    description: str = ""
    agent_id: str | None = None
    parent_task_id: str | None = None
    notify_on_complete: bool = True


@dataclass(frozen=True)
class CommandExecutionDetails:
    user_sandbox_id: str
    sandbox_instance_id: str
    provider: str
    command: str
    log_path: str
    kind: SandboxCommandKind = SandboxCommandKind.execute
    timeout_seconds: int | None = None
    monitor_deadline_at: datetime | None = None


@dataclass(frozen=True)
class TaskReservation:
    """Only a newly created, committed reservation may start provider work."""

    task: BackgroundTask
    command: SandboxCommand
    created: bool


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("task timestamps must be timezone-aware")


def command_deadline(*, now: datetime, details: CommandExecutionDetails) -> datetime | None:
    _require_aware(now)
    if details.kind == SandboxCommandKind.monitor:
        if details.monitor_deadline_at is not None:
            _require_aware(details.monitor_deadline_at)
        return details.monitor_deadline_at
    seconds = details.timeout_seconds
    if seconds is None:
        seconds = get_command_default_timeout_seconds()
    if type(seconds) is not int or not 0 < seconds <= MAX_COMMAND_TIMEOUT_SECONDS:
        raise ValueError(
            "timeout_seconds must be a positive integer "
            f"not exceeding {MAX_COMMAND_TIMEOUT_SECONDS}"
        )
    try:
        return now + timedelta(seconds=seconds)
    except OverflowError as exc:
        raise ValueError("timeout_seconds exceeds the representable deadline") from exc


class BackgroundTaskService(BackgroundTaskLifecycle):
    def __init__(self, session: AsyncSession, *, org_id: str, workspace_id: str) -> None:
        super().__init__(session, org_id=org_id, workspace_id=workspace_id)
        self.admissions = ConversationExecutionAdmissionRepository(
            session, org_id=org_id, workspace_id=workspace_id
        )

    async def reserve_task(
        self,
        *,
        admission_id: str,
        task_spec: TaskSpec,
        execution_details: CommandExecutionDetails,
        owner_token: str,
        owner_until: datetime,
        now: datetime,
    ) -> TaskReservation:
        _require_aware(now)
        _require_aware(owner_until)
        if not owner_token or owner_until <= now:
            raise ValueError("a reservation requires a live owner lease")
        if not task_spec.tool_call_id:
            raise ValueError("a reservation requires a stable tool_call_id")
        admission = await self.admissions.get(admission_id)
        if admission is None:
            raise LookupError("execution admission not found")

        actor_conversations = ConversationRepository(
            self.session,
            org_id=self.org_id,
            workspace_id=self.workspace_id,
            user_id=admission.actor_user_id,
        ).accessible_id_subquery()
        membership = (
            select(col(Membership.user_id))
            .where(
                col(Membership.user_id) == admission.actor_user_id,
                col(Membership.workspace_id) == self.workspace_id,
            )
            .exists()
        )

        conversation = (
            await self.session.execute(
                select(Conversation)
                .where(
                    col(Conversation.id) == admission.conversation_id,
                    col(Conversation.org_id) == self.org_id,
                    col(Conversation.workspace_id) == self.workspace_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if conversation is None:
            raise LookupError("conversation not found")
        await self.session.refresh(admission, with_for_update=True)
        if (
            conversation.deleted_at is not None
            or conversation.execution_closed_at is not None
            or conversation.execution_generation != admission.execution_generation
            or admission.revoked_at is not None
            or admission.run_stop_requested_at is not None
        ):
            raise TaskExecutionRevokedError("execution admission is no longer valid")
        accessible = await self.session.scalar(
            select(col(Conversation.id)).where(
                col(Conversation.id) == conversation.id,
                col(Conversation.id).in_(actor_conversations),
                membership,
            )
        )
        if accessible is None:
            raise LookupError("conversation not found")
        if admission.run_id is None or admission.run_id != task_spec.originating_run_id:
            raise TaskExecutionRevokedError("run does not own this execution admission")

        sandbox = (
            await self.session.execute(
                select(UserSandbox)
                .where(
                    col(UserSandbox.id) == execution_details.user_sandbox_id,
                    col(UserSandbox.org_id) == self.org_id,
                    col(UserSandbox.workspace_id) == self.workspace_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if sandbox is None:
            raise LookupError("sandbox not found")
        await self._validate_sandbox_scope(conversation, sandbox, admission.actor_user_id)

        existing = (
            await self.session.execute(
                select(BackgroundTask, SandboxCommand)
                .join(SandboxCommand, col(SandboxCommand.task_id) == col(BackgroundTask.id))
                .where(
                    col(BackgroundTask.org_id) == self.org_id,
                    col(BackgroundTask.workspace_id) == self.workspace_id,
                    col(BackgroundTask.admission_id) == admission.id,
                    col(BackgroundTask.originating_run_id) == task_spec.originating_run_id,
                    col(BackgroundTask.tool_call_id) == task_spec.tool_call_id,
                    col(BackgroundTask.agent_id) == task_spec.agent_id,
                )
            )
        ).one_or_none()
        if existing is not None:
            previous_task, previous_command = existing
            if (
                previous_command.user_sandbox_id != execution_details.user_sandbox_id
                or previous_command.sandbox_instance_id != execution_details.sandbox_instance_id
                or previous_command.command != execution_details.command
                or previous_command.provider != execution_details.provider
                or previous_command.kind != execution_details.kind.value
                or previous_task.parent_task_id != task_spec.parent_task_id
                or previous_task.notify_on_complete != task_spec.notify_on_complete
            ):
                raise ValueError("tool_call_id already reserved different work")
            return TaskReservation(task=previous_task, command=previous_command, created=False)
        if (
            sandbox.deleted_at is not None
            or sandbox.status != "running"
            or not execution_details.sandbox_instance_id
            or sandbox.sandbox_id != execution_details.sandbox_instance_id
            or sandbox.provider != execution_details.provider
        ):
            raise TaskEnvironmentChangedError("sandbox attachment no longer matches reservation")

        parent_id = task_spec.parent_task_id
        visited: set[str] = set()
        while parent_id is not None:
            if parent_id in visited:
                raise TaskExecutionRevokedError("invalid cyclic task ancestry")
            visited.add(parent_id)
            parent = await self.tasks.get_locked(parent_id)
            if parent is None or parent.conversation_id != conversation.id:
                raise LookupError("parent task not found")
            if (
                parent.execution_generation != admission.execution_generation
                or parent.stop_requested_at is not None
                or parent.notifications_cancelled_at is not None
                or (parent.deadline_at is not None and parent.deadline_at <= now)
            ):
                raise TaskExecutionRevokedError("parent task no longer permits new work")
            parent_id = parent.parent_task_id

        count_stmt = (
            select(func.count())
            .select_from(SandboxCommand)
            .outerjoin(BackgroundTask, col(SandboxCommand.task_id) == col(BackgroundTask.id))
            .where(
                col(SandboxCommand.org_id) == self.org_id,
                col(SandboxCommand.workspace_id) == self.workspace_id,
                col(SandboxCommand.user_sandbox_id) == sandbox.id,
                or_(
                    and_(
                        col(SandboxCommand.task_id).is_(None),
                        col(SandboxCommand.status).in_(("starting", "running")),
                    ),
                    col(BackgroundTask.state).in_(INFLIGHT_TASK_STATES),
                ),
            )
        )
        if int((await self.session.execute(count_stmt)).scalar_one()) >= MAX_INFLIGHT_COMMANDS:
            raise SandboxCommandCapError(
                f"at most {MAX_INFLIGHT_COMMANDS} running commands per sandbox"
            )

        deadline_at = command_deadline(now=now, details=execution_details)
        task = BackgroundTask(
            org_id=self.org_id,
            workspace_id=self.workspace_id,
            conversation_id=conversation.id,
            admission_id=admission.id,
            parent_task_id=task_spec.parent_task_id,
            kind="command",
            description=task_spec.description,
            originating_run_id=task_spec.originating_run_id,
            tool_call_id=task_spec.tool_call_id,
            agent_id=task_spec.agent_id,
            started_by_user_id=admission.actor_user_id,
            execution_generation=admission.execution_generation,
            notify_on_complete=task_spec.notify_on_complete,
            deadline_at=deadline_at,
            owner_token=owner_token,
            owner_until=owner_until,
            created_at=now,
            updated_at=now,
        )
        self.session.add(task)
        await self.session.flush()
        command = SandboxCommand(
            org_id=self.org_id,
            workspace_id=self.workspace_id,
            task_id=task.id,
            user_sandbox_id=sandbox.id,
            sandbox_instance_id=execution_details.sandbox_instance_id,
            conversation_id=conversation.id,
            run_id=task_spec.originating_run_id,
            tool_call_id=task_spec.tool_call_id,
            started_by_user_id=admission.actor_user_id,
            agent_id=task_spec.agent_id,
            command=execution_details.command,
            description=task_spec.description,
            provider=execution_details.provider,
            log_path=execution_details.log_path,
            kind=execution_details.kind.value,
            created_at=now,
            updated_at=now,
        )
        self.session.add(command)
        await self.session.flush()
        return TaskReservation(task=task, command=command, created=True)

    async def _validate_sandbox_scope(
        self, conversation: Conversation, sandbox: UserSandbox, actor_user_id: str
    ) -> None:
        if conversation.topic_id is None:
            if conversation.is_group_chat:
                expected = ("conversation", conversation.id, conversation.creator_user_id)
            else:
                expected = ("user", actor_user_id, actor_user_id)
        else:
            topic = (
                await self.session.execute(
                    select(Topic).where(
                        col(Topic.id) == conversation.topic_id,
                        col(Topic.org_id) == self.org_id,
                        col(Topic.workspace_id) == self.workspace_id,
                        col(Topic.is_archived).is_(False),
                    )
                )
            ).scalar_one_or_none()
            if topic is None:
                raise LookupError("sandbox topic not found")
            expected = (
                ("topic", topic.id, topic.creator_user_id)
                if topic.sandbox_mode == "dedicated"
                else ("user", topic.creator_user_id, topic.creator_user_id)
            )
        if (sandbox.scope_type, sandbox.scope_id, sandbox.user_id) != expected:
            raise LookupError("sandbox not found for this conversation")
