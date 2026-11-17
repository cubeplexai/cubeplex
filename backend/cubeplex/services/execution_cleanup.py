"""Dependency-ordered removal of durable execution state after cleanup."""

from collections.abc import Sequence

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from cubeplex.models.background_task import BackgroundTask, BackgroundTaskEvent
from cubeplex.models.conversation import Conversation
from cubeplex.models.conversation_execution import ConversationExecutionAdmission
from cubeplex.models.im_connector import IMRunQueueItem
from cubeplex.models.sandbox_command import SandboxCommand, SandboxCommandWake
from cubeplex.models.steering_message import SteeringMessage
from cubeplex.models.trigger import TriggerEvent
from cubeplex.models.user_sandbox import UserSandbox


async def purge_workspace_execution_state(
    session: AsyncSession,
    *,
    workspace_id: str,
) -> None:
    """Delete one workspace's lifecycle rows only after cleanup is terminal."""
    for model in (
        SandboxCommandWake,
        TriggerEvent,
        IMRunQueueItem,
        SteeringMessage,
        BackgroundTaskEvent,
        SandboxCommand,
        BackgroundTask,
        ConversationExecutionAdmission,
    ):
        await session.execute(delete(model).where(col(model.workspace_id) == workspace_id))


async def purge_user_execution_state(
    session: AsyncSession,
    *,
    user_id: str,
    extra_workspace_ids: Sequence[str] = (),
) -> None:
    """Delete actor-owned work and all work in conversations or sandboxes the account owns."""
    owned_conversation_ids = select(col(Conversation.id)).where(
        col(Conversation.creator_user_id) == user_id
    )
    sandbox_predicates = [col(UserSandbox.user_id) == user_id]
    if extra_workspace_ids:
        sandbox_predicates.append(col(UserSandbox.workspace_id).in_(list(extra_workspace_ids)))
    owned_sandbox_ids = select(col(UserSandbox.id)).where(or_(*sandbox_predicates))
    sandbox_task_ids = select(col(SandboxCommand.task_id)).where(
        col(SandboxCommand.user_sandbox_id).in_(owned_sandbox_ids),
        col(SandboxCommand.task_id).is_not(None),
    )
    admission_ids = select(col(ConversationExecutionAdmission.id)).where(
        or_(
            col(ConversationExecutionAdmission.actor_user_id) == user_id,
            col(ConversationExecutionAdmission.conversation_id).in_(owned_conversation_ids),
        )
    )
    task_ids = select(col(BackgroundTask.id)).where(
        or_(
            col(BackgroundTask.admission_id).in_(admission_ids),
            col(BackgroundTask.conversation_id).in_(owned_conversation_ids),
            col(BackgroundTask.started_by_user_id) == user_id,
            col(BackgroundTask.id).in_(sandbox_task_ids),
        )
    )
    command_ids = select(col(SandboxCommand.id)).where(
        or_(
            col(SandboxCommand.task_id).in_(task_ids),
            col(SandboxCommand.conversation_id).in_(owned_conversation_ids),
            col(SandboxCommand.user_sandbox_id).in_(owned_sandbox_ids),
        )
    )
    event_ids = select(col(BackgroundTaskEvent.id)).where(
        col(BackgroundTaskEvent.task_id).in_(task_ids)
    )
    await session.execute(
        delete(SandboxCommandWake).where(col(SandboxCommandWake.command_id).in_(command_ids))
    )
    await session.execute(
        delete(TriggerEvent).where(col(TriggerEvent.execution_admission_id).in_(admission_ids))
    )
    await session.execute(
        delete(IMRunQueueItem).where(
            or_(
                col(IMRunQueueItem.execution_admission_id).in_(admission_ids),
                col(IMRunQueueItem.conversation_id).in_(owned_conversation_ids),
                col(IMRunQueueItem.actor_user_id) == user_id,
            )
        )
    )
    await session.execute(
        delete(SteeringMessage).where(col(SteeringMessage.notice_id).in_(event_ids))
    )
    await session.execute(
        delete(BackgroundTaskEvent).where(col(BackgroundTaskEvent.task_id).in_(task_ids))
    )
    await session.execute(delete(SandboxCommand).where(col(SandboxCommand.id).in_(command_ids)))
    await session.execute(delete(BackgroundTask).where(col(BackgroundTask.id).in_(task_ids)))
    await session.execute(
        delete(ConversationExecutionAdmission).where(
            col(ConversationExecutionAdmission.id).in_(admission_ids)
        )
    )
