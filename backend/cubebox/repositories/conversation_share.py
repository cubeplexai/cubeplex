"""Repository for conversation shares — scoped by (workspace, creator)."""

from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models.conversation_share import ConversationShare
from cubebox.repositories.base import ScopedRepository


class ConversationShareRepository(ScopedRepository[ConversationShare]):
    model = ConversationShare

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
        return super()._scoped_select().where(ConversationShare.creator_user_id == self.user_id)

    async def create(
        self,
        *,
        conversation_id: str,
        creator_display_name: str,
        title: str,
        snapshot: dict[str, Any],
        artifacts_snapshot: list[dict[str, Any]],
        is_active: bool = True,
    ) -> ConversationShare:
        share = ConversationShare(
            conversation_id=conversation_id,
            creator_user_id=self.user_id,
            creator_display_name=creator_display_name,
            title=title,
            snapshot=snapshot,
            artifacts_snapshot=artifacts_snapshot,
            is_active=is_active,
        )
        return await self.add(share)

    async def activate(self, share_id: str) -> ConversationShare:
        share = await self.get(share_id)
        if share is None:
            raise ValueError(f"Share {share_id} not found")
        share.is_active = True
        await self.session.commit()
        await self.session.refresh(share)
        return share

    async def get_by_id(self, share_id: str) -> ConversationShare | None:
        return await self.get(share_id)

    async def list_all(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[ConversationShare], int]:
        stmt = (
            self._scoped_select()
            .order_by(desc(ConversationShare.created_at))  # type: ignore[arg-type]
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        items = list(result.scalars().all())

        count_stmt = (
            select(func.count())
            .select_from(ConversationShare)
            .where(
                ConversationShare.workspace_id == self.workspace_id,  # type: ignore[arg-type]
                ConversationShare.creator_user_id == self.user_id,  # type: ignore[arg-type]
            )
        )
        total = (await self.session.execute(count_stmt)).scalar_one()
        return items, total

    async def list_by_conversation(self, conversation_id: str) -> list[ConversationShare]:
        stmt = (
            self._scoped_select()
            .where(
                ConversationShare.conversation_id == conversation_id,
            )
            .order_by(desc(ConversationShare.created_at))  # type: ignore[arg-type]
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def revoke(self, share_id: str) -> ConversationShare | None:
        share = await self.get(share_id)
        if share is None:
            return None
        share.is_active = False
        await self.session.commit()
        await self.session.refresh(share)
        return share
