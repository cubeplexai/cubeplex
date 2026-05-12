"""Conversation repository — scoped by (workspace_id, creator_user_id).

Conversations are per-user: only the creator can see/mutate their rows.
Org + workspace columns are still persisted via ``OrgScopedMixin`` but
the primary access check is ``creator_user_id``.
"""

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import case, desc, func, select, update
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

    async def create(self, title: str, *, draft: bool = False) -> Conversation:
        conv = Conversation(
            title=title,
            org_id=self.org_id,
            workspace_id=self.workspace_id,
            creator_user_id=self.user_id,
            has_messages=not draft,
        )
        return await self.add(conv)

    async def get_by_id(self, conversation_id: str) -> Conversation | None:
        return await self.get(conversation_id)

    async def list_all(self, *, limit: int = 20, offset: int = 0) -> tuple[list[Conversation], int]:
        stmt = (
            self._scoped_select()
            .where(cast(Any, Conversation.has_messages).is_(True))
            .order_by(
                case(
                    (cast(Any, Conversation.is_pinned).is_(True), 0),
                    else_=1,
                ),
                desc(Conversation.updated_at),  # type: ignore[arg-type]
            )
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
                cast(Any, Conversation.has_messages).is_(True),
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

    async def update_title_if_current(
        self, conversation_id: str, new_title: str, expected_title: str
    ) -> Conversation | None:
        """Update title atomically only if it still equals expected_title.

        Used by auto-title generation to avoid clobbering a concurrent manual
        rename. Compare-and-set happens in SQL (``UPDATE … WHERE title = ?``)
        so a stale identity-map copy in this session cannot pass the guard
        after another transaction has already committed a rename.

        Returns the current row (with whatever title now lives in the DB),
        or ``None`` if the conversation no longer exists.
        """
        now = datetime.now(UTC)
        stmt = (
            update(Conversation)
            .where(
                Conversation.id == conversation_id,  # type: ignore[arg-type]
                Conversation.creator_user_id == self.user_id,  # type: ignore[arg-type]
                Conversation.title == expected_title,  # type: ignore[arg-type]
            )
            .values(title=new_title, updated_at=now)
        )
        await self.session.execute(stmt)
        await self.session.commit()
        # Drop any stale identity-map state so the follow-up read reflects
        # whichever writer won the race.
        self.session.expire_all()
        return await self.get(conversation_id)

    async def update_timestamp(self, conversation_id: str) -> None:
        conv = await self.get(conversation_id)
        if conv:
            conv.updated_at = datetime.now(UTC)
            await self.session.commit()

    async def mark_active(self, conversation_id: str) -> None:
        """Mark the conversation as having user activity.

        Always sets ``has_messages=True`` and bumps ``updated_at`` to now.
        Called both at message-stream start (so the conversation becomes
        visible immediately, even if the stream errors) and at stream end
        (so the timestamp reflects the latest activity for recency
        ordering in ``list_all``).
        """
        conv = await self.get(conversation_id)
        if not conv:
            return
        conv.has_messages = True
        conv.updated_at = datetime.now(UTC)
        await self.session.commit()

    async def set_pin(self, conversation_id: str, is_pinned: bool) -> Conversation | None:
        conv = await self.get(conversation_id)
        if not conv:
            return None
        conv.is_pinned = is_pinned
        await self.session.commit()
        await self.session.refresh(conv)
        return conv

    async def delete_conversation(self, conversation_id: str) -> bool:
        return await self.delete(conversation_id)
