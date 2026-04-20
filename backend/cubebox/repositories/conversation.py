"""Conversation repository — scoped by (workspace_id, creator_user_id).

Conversations are per-user: only the creator can see/mutate their rows.
Org + workspace columns are still persisted via ``OrgScopedMixin`` but
the primary access check is ``creator_user_id``.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import Conversation
from cubebox.repositories.base import ScopedRepository


class ConversationRepository(ScopedRepository[Conversation]):
    model = Conversation

    def __init__(
        self,
        session: AsyncSession,
        *,
        org_id: str,
        workspace_id: str,
        user_id: str,
    ) -> None:
        super().__init__(session, org_id=org_id, workspace_id=workspace_id)
        self.user_id = user_id

    def _scoped_select(self) -> Any:
        return (
            super()
            ._scoped_select()
            .where(
                Conversation.creator_user_id == self.user_id,
            )
        )

    async def create(self, title: str) -> Conversation:
        conv = Conversation(
            title=title,
            org_id=self.org_id,
            workspace_id=self.workspace_id,
            creator_user_id=self.user_id,
        )
        return await self.add(conv)

    async def get_by_id(self, conversation_id: str) -> Conversation | None:
        return await self.get(conversation_id)

    async def list_all(self, *, limit: int = 20, offset: int = 0) -> tuple[list[Conversation], int]:
        stmt = (
            self._scoped_select()
            .order_by(desc(Conversation.updated_at))  # type: ignore[arg-type]
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        items = list(result.scalars().all())

        count_stmt = (
            select(func.count())
            .select_from(Conversation)
            .where(
                Conversation.workspace_id == self.workspace_id,  # type: ignore[arg-type]
                Conversation.creator_user_id == self.user_id,  # type: ignore[arg-type]
            )
        )
        total = (await self.session.execute(count_stmt)).scalar_one()
        return items, total

    async def update_title(self, conversation_id: str, title: str) -> Conversation | None:
        conv = await self.get(conversation_id)
        if not conv:
            return None
        conv.title = title
        conv.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(conv)
        return conv

    async def update_timestamp(self, conversation_id: str) -> None:
        conv = await self.get(conversation_id)
        if conv:
            conv.updated_at = datetime.now(UTC)
            await self.session.commit()

    async def delete_conversation(self, conversation_id: str) -> bool:
        return await self.delete(conversation_id)
