"""Repository for UserEvent — list/insert/mark_read."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from cubebox.models.user_event import UserEvent


class UserEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, ev: UserEvent) -> UserEvent:
        self.session.add(ev)
        await self.session.flush()
        return ev

    async def list_for_user(
        self,
        user_id: str,
        *,
        since_id: str | None,
        limit: int = 100,
    ) -> list[UserEvent]:
        stmt = select(UserEvent).where(UserEvent.user_id == user_id)
        if since_id is not None:
            stmt = stmt.where(UserEvent.id > since_id)  # public IDs are time-sortable
        stmt = stmt.order_by(UserEvent.created_at).limit(limit)  # type: ignore[arg-type]
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_read(self, ev_id: str, user_id: str) -> UserEvent | None:
        stmt = select(UserEvent).where(UserEvent.id == ev_id, UserEvent.user_id == user_id)
        row = (await self.session.execute(stmt)).scalars().first()
        if row is None:
            return None
        row.read_at = datetime.now(UTC)
        await self.session.flush()
        return row
