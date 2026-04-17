"""Conversation repository — scoped by (org_id, workspace_id)."""

from datetime import UTC, datetime

from sqlalchemy import desc, func, select

from cubebox.models import Conversation
from cubebox.repositories.base import ScopedRepository


class ConversationRepository(ScopedRepository[Conversation]):
    model = Conversation

    async def create(self, title: str) -> Conversation:
        conv = Conversation(
            title=title,
            org_id=self.org_id,
            workspace_id=self.workspace_id,
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
                Conversation.org_id == self.org_id,  # type: ignore[arg-type]
                Conversation.workspace_id == self.workspace_id,  # type: ignore[arg-type]
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
