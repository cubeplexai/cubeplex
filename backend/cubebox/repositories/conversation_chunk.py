"""Repository for conversation_chunks."""

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models.conversation_chunk import ConversationChunk


class ConversationChunkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def replace_for_conversation(
        self,
        *,
        org_id: str,
        workspace_id: str,
        creator_user_id: str,
        conversation_id: str,
        chunks: list[ConversationChunk],
    ) -> None:
        """Atomic rebuild: drop existing chunks for the conversation and insert new ones."""
        await self.session.execute(
            delete(ConversationChunk).where(
                ConversationChunk.conversation_id == conversation_id  # type: ignore[arg-type]
            )
        )
        for c in chunks:
            c.org_id = org_id
            c.workspace_id = workspace_id
            c.creator_user_id = creator_user_id
            c.conversation_id = conversation_id
            self.session.add(c)
        await self.session.commit()

    async def get_by_ids(self, ids: list[str]) -> list[ConversationChunk]:
        if not ids:
            return []
        stmt = select(ConversationChunk).where(
            ConversationChunk.id.in_(ids)  # type: ignore[attr-defined]
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_for_conversation(self, conversation_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(ConversationChunk)
            .where(
                ConversationChunk.conversation_id == conversation_id  # type: ignore[arg-type]
            )
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())
