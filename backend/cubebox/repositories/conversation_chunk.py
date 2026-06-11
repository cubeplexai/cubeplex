"""Repository for conversation_chunks (scoped by org/workspace/user)."""

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models.conversation_chunk import ConversationChunk
from cubebox.repositories.base import ScopedRepository


class ConversationChunkRepository(ScopedRepository[ConversationChunk]):
    model = ConversationChunk

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
        return super()._scoped_select().where(ConversationChunk.creator_user_id == self.user_id)

    async def replace_for_conversation(
        self,
        *,
        conversation_id: str,
        chunks: list[ConversationChunk],
    ) -> None:
        """Atomic rebuild: drop existing chunks for the conversation and insert new ones.

        DELETE is scope-filtered so a wrong-scope call cannot wipe another
        user's chunks; INSERT force-sets scope columns so they cannot be
        leaked across workspaces.
        """
        await self.session.execute(
            delete(ConversationChunk).where(
                ConversationChunk.conversation_id == conversation_id,  # type: ignore[arg-type]
                ConversationChunk.org_id == self.org_id,  # type: ignore[arg-type]
                ConversationChunk.workspace_id == self.workspace_id,  # type: ignore[arg-type]
                ConversationChunk.creator_user_id == self.user_id,  # type: ignore[arg-type]
            )
        )
        for c in chunks:
            c.org_id = self.org_id
            c.workspace_id = self.workspace_id
            c.creator_user_id = self.user_id
            c.conversation_id = conversation_id
            self.session.add(c)
        await self.session.commit()

    async def get_by_ids(self, ids: list[str]) -> list[ConversationChunk]:
        if not ids:
            return []
        stmt = self._scoped_select().where(
            ConversationChunk.id.in_(ids)  # type: ignore[attr-defined]
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_for_conversation(self, conversation_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(ConversationChunk)
            .where(
                ConversationChunk.conversation_id == conversation_id,  # type: ignore[arg-type]
                ConversationChunk.org_id == self.org_id,  # type: ignore[arg-type]
                ConversationChunk.workspace_id == self.workspace_id,  # type: ignore[arg-type]
                ConversationChunk.creator_user_id == self.user_id,  # type: ignore[arg-type]
            )
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())
