"""Repository for conversation shares — standalone, auth handled by routes."""

from typing import Any

from sqlalchemy import desc, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models.conversation_share import ConversationShare, ShareScope


class ConversationShareRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        org_id: str,
        workspace_id: str,
        conversation_id: str,
        creator_user_id: str,
        creator_display_name: str,
        title: str,
        scope: ShareScope,
        snapshot: dict[str, Any],
        artifacts_snapshot: list[dict[str, Any]],
        is_active: bool = True,
    ) -> ConversationShare:
        share = ConversationShare(
            org_id=org_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            creator_user_id=creator_user_id,
            creator_display_name=creator_display_name,
            title=title,
            scope=scope,
            snapshot=snapshot,
            artifacts_snapshot=artifacts_snapshot,
            is_active=is_active,
        )
        self.session.add(share)
        await self.session.commit()
        await self.session.refresh(share)
        return share

    async def get_active(self, share_id: str) -> ConversationShare | None:
        stmt = select(ConversationShare).where(
            ConversationShare.id == share_id,  # type: ignore[arg-type]
            ConversationShare.is_active == true(),  # type: ignore[arg-type]
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def activate(self, share_id: str) -> ConversationShare:
        stmt = select(ConversationShare).where(
            ConversationShare.id == share_id,  # type: ignore[arg-type]
        )
        result = await self.session.execute(stmt)
        share = result.scalar_one_or_none()
        if share is None:
            raise ValueError(f"Share {share_id} not found")
        share.is_active = True
        await self.session.commit()
        await self.session.refresh(share)
        return share

    async def revoke(self, share_id: str, user_id: str) -> ConversationShare | None:
        stmt = select(ConversationShare).where(
            ConversationShare.id == share_id,  # type: ignore[arg-type]
            ConversationShare.creator_user_id == user_id,  # type: ignore[arg-type]
        )
        result = await self.session.execute(stmt)
        share = result.scalar_one_or_none()
        if share is None:
            return None
        share.is_active = False
        await self.session.commit()
        await self.session.refresh(share)
        return share

    async def list_by_creator(
        self,
        user_id: str,
        *,
        workspace_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ConversationShare], int]:
        base = select(ConversationShare).where(
            ConversationShare.creator_user_id == user_id,  # type: ignore[arg-type]
        )
        if workspace_id is not None:
            base = base.where(
                ConversationShare.workspace_id == workspace_id,  # type: ignore[arg-type]
            )
        stmt = (
            base.order_by(desc(ConversationShare.created_at))  # type: ignore[arg-type]
            .limit(limit)
            .offset(offset)
        )
        items = list((await self.session.execute(stmt)).scalars().all())

        count_base = (
            select(func.count())
            .select_from(ConversationShare)
            .where(
                ConversationShare.creator_user_id == user_id,  # type: ignore[arg-type]
            )
        )
        if workspace_id is not None:
            count_base = count_base.where(
                ConversationShare.workspace_id == workspace_id,  # type: ignore[arg-type]
            )
        total: int = (await self.session.execute(count_base)).scalar_one()
        return items, total

    async def list_by_conversation(
        self, conversation_id: str, user_id: str
    ) -> list[ConversationShare]:
        stmt = (
            select(ConversationShare)
            .where(
                ConversationShare.conversation_id == conversation_id,  # type: ignore[arg-type]
                ConversationShare.creator_user_id == user_id,  # type: ignore[arg-type]
            )
            .order_by(desc(ConversationShare.created_at))  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())
