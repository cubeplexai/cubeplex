"""Read-only progress for run Stop and conversation Stop all controls."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from cubeplex.models.background_task import (
    INFLIGHT_TASK_STATES,
    BackgroundTask,
    BackgroundTaskEvent,
)
from cubeplex.models.conversation import Conversation
from cubeplex.models.conversation_execution import ConversationExecutionAdmission
from cubeplex.models.sandbox_command import SandboxCommand
from cubeplex.models.steering_message import SteeringMessage, SteeringMessageState


@dataclass(frozen=True)
class StopAllStatus:
    requested_at: datetime
    cleanup_pending: bool


@dataclass(frozen=True)
class RunControlStatus:
    run_id: str
    stop_requested_at: datetime | None
    cleanup_pending: bool
    can_stop: bool


class ConversationExecutionStatusService:
    """Project control status without mutating runs, tasks, or delivery."""

    def __init__(self, session: AsyncSession, *, org_id: str, workspace_id: str) -> None:
        self.session = session
        self.org_id = org_id
        self.workspace_id = workspace_id

    async def stop_all_status(self, *, conversation: Conversation) -> StopAllStatus | None:
        if conversation.execution_closed_at is None:
            return None
        generation = conversation.execution_generation
        admission_pending = exists(
            select(col(ConversationExecutionAdmission.id)).where(
                col(ConversationExecutionAdmission.org_id) == self.org_id,
                col(ConversationExecutionAdmission.workspace_id) == self.workspace_id,
                col(ConversationExecutionAdmission.conversation_id) == conversation.id,
                col(ConversationExecutionAdmission.execution_generation) == generation,
                or_(
                    and_(
                        col(ConversationExecutionAdmission.execution_kind) == "run",
                        col(ConversationExecutionAdmission.run_id).is_not(None),
                        col(ConversationExecutionAdmission.run_finished_at).is_(None),
                    ),
                    and_(
                        col(ConversationExecutionAdmission.execution_kind) != "run",
                        col(ConversationExecutionAdmission.direct_started_at).is_not(None),
                        col(ConversationExecutionAdmission.direct_result).is_(None),
                    ),
                ),
            )
        )
        task_pending = exists(
            select(col(BackgroundTask.id)).where(
                col(BackgroundTask.org_id) == self.org_id,
                col(BackgroundTask.workspace_id) == self.workspace_id,
                col(BackgroundTask.conversation_id) == conversation.id,
                col(BackgroundTask.execution_generation) == generation,
                col(BackgroundTask.state).in_(INFLIGHT_TASK_STATES),
            )
        )
        log_pending = exists(
            select(col(SandboxCommand.id))
            .join(BackgroundTask, col(BackgroundTask.id) == col(SandboxCommand.task_id))
            .where(
                col(BackgroundTask.org_id) == self.org_id,
                col(BackgroundTask.workspace_id) == self.workspace_id,
                col(BackgroundTask.conversation_id) == conversation.id,
                col(BackgroundTask.execution_generation) == generation,
                col(SandboxCommand.org_id) == self.org_id,
                col(SandboxCommand.workspace_id) == self.workspace_id,
                col(SandboxCommand.log_state).in_(("pending", "retrying")),
            )
        )
        event_pending = exists(
            select(col(BackgroundTaskEvent.id)).where(
                col(BackgroundTaskEvent.org_id) == self.org_id,
                col(BackgroundTaskEvent.workspace_id) == self.workspace_id,
                col(BackgroundTaskEvent.conversation_id) == conversation.id,
                col(BackgroundTaskEvent.execution_generation) == generation,
                col(BackgroundTaskEvent.state).in_(("pending", "claimed")),
            )
        )
        input_pending = exists(
            select(col(SteeringMessage.id)).where(
                col(SteeringMessage.org_id) == self.org_id,
                col(SteeringMessage.workspace_id) == self.workspace_id,
                col(SteeringMessage.conversation_id) == conversation.id,
                col(SteeringMessage.execution_generation) == generation,
                col(SteeringMessage.state) == SteeringMessageState.cancel_requested,
            )
        )
        pending = await self.session.scalar(
            select(or_(admission_pending, task_pending, log_pending, event_pending, input_pending))
        )
        return StopAllStatus(conversation.execution_closed_at, bool(pending))

    async def run_status(self, *, conversation_id: str, run_id: str) -> RunControlStatus | None:
        admission = await self.session.scalar(
            select(ConversationExecutionAdmission).where(
                col(ConversationExecutionAdmission.org_id) == self.org_id,
                col(ConversationExecutionAdmission.workspace_id) == self.workspace_id,
                col(ConversationExecutionAdmission.conversation_id) == conversation_id,
                col(ConversationExecutionAdmission.run_id) == run_id,
            )
        )
        if admission is None:
            return None
        task_pending = exists(
            select(col(BackgroundTask.id)).where(
                col(BackgroundTask.org_id) == self.org_id,
                col(BackgroundTask.workspace_id) == self.workspace_id,
                col(BackgroundTask.conversation_id) == conversation_id,
                col(BackgroundTask.originating_run_id) == run_id,
                col(BackgroundTask.backgrounded_at).is_(None),
                col(BackgroundTask.state).in_(INFLIGHT_TASK_STATES),
            )
        )
        input_pending = exists(
            select(col(SteeringMessage.id)).where(
                col(SteeringMessage.org_id) == self.org_id,
                col(SteeringMessage.workspace_id) == self.workspace_id,
                col(SteeringMessage.conversation_id) == conversation_id,
                col(SteeringMessage.run_id) == run_id,
                col(SteeringMessage.state) == SteeringMessageState.cancel_requested,
            )
        )
        local_cleanup = bool(await self.session.scalar(select(or_(task_pending, input_pending))))
        stop_requested = admission.run_stop_requested_at is not None
        cleanup_pending = stop_requested and (admission.run_finished_at is None or local_cleanup)
        return RunControlStatus(
            run_id=run_id,
            stop_requested_at=admission.run_stop_requested_at,
            cleanup_pending=cleanup_pending,
            can_stop=not stop_requested and admission.run_finished_at is None,
        )

    async def latest_stopping_run_id(self, *, conversation_id: str) -> str | None:
        """Keep an accepted run Stop visible after its Redis active key clears."""
        return await self.session.scalar(
            select(col(ConversationExecutionAdmission.run_id))
            .where(
                col(ConversationExecutionAdmission.org_id) == self.org_id,
                col(ConversationExecutionAdmission.workspace_id) == self.workspace_id,
                col(ConversationExecutionAdmission.conversation_id) == conversation_id,
                col(ConversationExecutionAdmission.run_id).is_not(None),
                col(ConversationExecutionAdmission.run_stop_requested_at).is_not(None),
                col(ConversationExecutionAdmission.run_finished_at).is_(None),
            )
            .order_by(
                col(ConversationExecutionAdmission.run_stop_requested_at).desc(),
                col(ConversationExecutionAdmission.id).desc(),
            )
            .limit(1)
        )
