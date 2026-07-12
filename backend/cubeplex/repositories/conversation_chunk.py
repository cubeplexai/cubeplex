"""Repository for conversation_chunks (scoped by org/workspace/user)."""

import logging
from typing import Any

from psycopg.errors import UniqueViolation
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.conversation_chunk import ConversationChunk
from cubeplex.repositories.base import ScopedRepository

logger = logging.getLogger(__name__)


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

        A duplicate-key IntegrityError on commit means a concurrent worker
        wrote chunks for the same conversation between our DELETE and INSERT.
        That's harmless — the data is identical or a superset — so we swallow
        it instead of crashing the worker loop.
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
        try:
            await self.session.commit()
        except IntegrityError as exc:
            if isinstance(exc.orig, UniqueViolation):
                await self.session.rollback()
                logger.warning(
                    "replace_for_conversation skipped for %s: concurrent worker wrote "
                    "chunks first (UniqueViolation on ix_chunks_conversation). "
                    "The existing chunks are identical or a superset.",
                    conversation_id,
                )
                return
            raise

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
