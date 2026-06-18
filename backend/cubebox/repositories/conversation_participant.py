"""Conversation participant repository — append-only membership."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import update

from cubebox.models.conversation import Conversation
from cubebox.models.conversation_participant import ConversationParticipant
from cubebox.repositories.base import ScopedRepository


class ConversationParticipantRepository(ScopedRepository[ConversationParticipant]):
    model = ConversationParticipant

    def __init__(
        self,
        session: AsyncSession,
        *,
        org_id: str,
        workspace_id: str,
    ) -> None:
        super().__init__(session, org_id=org_id, workspace_id=workspace_id)

    async def list_user_ids(self, conversation_id: str) -> list[str]:
        stmt = select(cast(Any, ConversationParticipant.user_id)).where(
            cast(Any, ConversationParticipant.conversation_id) == conversation_id,
        )
        return [str(uid) for uid in (await self.session.execute(stmt)).scalars().all()]

    async def is_participant(self, conversation_id: str, user_id: str) -> bool:
        stmt = select(func.count()).where(
            cast(Any, ConversationParticipant.conversation_id) == conversation_id,
            cast(Any, ConversationParticipant.user_id) == user_id,
        )
        return bool((await self.session.execute(stmt)).scalar_one() > 0)

    async def ensure_participant(
        self, conversation_id: str, user_id: str
    ) -> ConversationParticipant | None:
        """Append the row idempotently. Maintains Conversation.is_group_chat."""
        if await self.is_participant(conversation_id, user_id):
            return None
        row = ConversationParticipant(
            org_id=self.org_id,
            workspace_id=self.workspace_id,
            conversation_id=conversation_id,
            user_id=user_id,
        )
        self.session.add(row)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            return None

        # Compute the new count inside the same UPDATE statement so two
        # concurrent inserts cannot interleave with a stale snapshot
        # count from a separate SELECT. The scalar subquery sees every
        # row visible to the UPDATE's MVCC snapshot, including the row
        # we just flushed.
        count_subq = (
            select(func.count() > 1)
            .where(cast(Any, ConversationParticipant.conversation_id) == conversation_id)
            .scalar_subquery()
        )
        await self.session.execute(
            update(Conversation)
            .where(cast(Any, Conversation.id) == conversation_id)
            .values(is_group_chat=count_subq)
        )
        return row

    async def add_many(
        self, conversation_id: str, user_ids: list[str]
    ) -> list[ConversationParticipant]:
        """Append multiple participants idempotently. Returns the rows actually inserted."""
        added: list[ConversationParticipant] = []
        for uid in user_ids:
            row = await self.ensure_participant(conversation_id, uid)
            if row is not None:
                added.append(row)
        return added
