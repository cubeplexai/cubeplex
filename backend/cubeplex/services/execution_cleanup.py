"""Dependency-ordered removal of durable execution state after cleanup."""

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from cubeplex.models.background_task import BackgroundTask, BackgroundTaskEvent
from cubeplex.models.conversation import Conversation
from cubeplex.models.conversation_execution import ConversationExecutionAdmission
from cubeplex.models.im_connector import IMRunQueueItem
from cubeplex.models.sandbox_command import SandboxCommand, SandboxCommandWake
from cubeplex.models.trigger import TriggerEvent


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
) -> None:
    """Delete actor-owned work and all work in conversations the account owns."""
    owned_conversation_ids = select(col(Conversation.id)).where(
        col(Conversation.creator_user_id) == user_id
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
        )
    )
    command_ids = select(col(SandboxCommand.id)).where(
        or_(
            col(SandboxCommand.task_id).in_(task_ids),
            col(SandboxCommand.conversation_id).in_(owned_conversation_ids),
        )
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
        delete(BackgroundTaskEvent).where(col(BackgroundTaskEvent.task_id).in_(task_ids))
    )
    await session.execute(delete(SandboxCommand).where(col(SandboxCommand.id).in_(command_ids)))
    await session.execute(delete(BackgroundTask).where(col(BackgroundTask.id).in_(task_ids)))
    await session.execute(
        delete(ConversationExecutionAdmission).where(
            col(ConversationExecutionAdmission.id).in_(admission_ids)
        )
    )
