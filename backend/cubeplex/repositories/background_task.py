"""Scoped lifecycle reads; transactions are owned by the calling service/worker."""

from sqlalchemy import select
from sqlmodel import col

from cubeplex.models.background_task import BackgroundTask, BackgroundTaskEvent
from cubeplex.models.conversation_execution import ConversationExecutionAdmission
from cubeplex.repositories.base import ScopedRepository


class BackgroundTaskRepository(ScopedRepository[BackgroundTask]):
    model = BackgroundTask

    async def get_locked(self, task_id: str) -> BackgroundTask | None:
        result = await self.session.execute(
            self._scoped_select()
            .where(col(BackgroundTask.id) == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()


class BackgroundTaskEventRepository(ScopedRepository[BackgroundTaskEvent]):
    model = BackgroundTaskEvent


class ConversationExecutionAdmissionRepository(ScopedRepository[ConversationExecutionAdmission]):
    model = ConversationExecutionAdmission

    async def get_source(
        self, *, source_kind: str, source_id: str
    ) -> ConversationExecutionAdmission | None:
        result = await self.session.execute(
            select(ConversationExecutionAdmission).where(
                col(ConversationExecutionAdmission.org_id) == self.org_id,
                col(ConversationExecutionAdmission.workspace_id) == self.workspace_id,
                col(ConversationExecutionAdmission.source_kind) == source_kind,
                col(ConversationExecutionAdmission.source_id) == source_id,
            )
        )
        return result.scalar_one_or_none()
