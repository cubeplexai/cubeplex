"""Repository for UserEvent — list/insert/mark_read."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import collate
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
            # COLLATE "C": force byte-order comparison. Default locale-aware
            # collations (e.g. en_US.UTF-8) can put lowercase 's' < uppercase
            # 'T', which breaks our base62 IDs' temporal-sortability. Public
            # IDs use the alphabet [0-9A-Za-z] which lex-sorts correctly
            # ONLY under ASCII byte order — i.e. the C collation.
            stmt = stmt.where(collate(UserEvent.id, "C") > since_id)  # type: ignore[arg-type]
        stmt = stmt.order_by(UserEvent.created_at).limit(limit)  # type: ignore[arg-type]
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_unread_for_user(
        self,
        user_id: str,
        *,
        limit: int = 200,
    ) -> list[UserEvent]:
        """Return unread events for this user in chronological order.

        Used by the SSE replay path when the client has no cursor (first
        connection, cleared localStorage, fresh device). Uses the partial
        index ix_user_events_unread for efficient lookup.
        """
        stmt = (
            select(UserEvent)
            .where(UserEvent.user_id == user_id, UserEvent.read_at.is_(None))  # type: ignore[union-attr]
            .order_by(UserEvent.created_at)  # type: ignore[arg-type]
            .limit(limit)
        )
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
