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
        # ORDER BY the same collated id used for the cursor — ordering by
        # created_at instead would let pagination skip or re-emit rows when
        # id-order and time-order diverge (same-ms IDs from different
        # processes, clock adjustments, etc.).
        stmt = stmt.order_by(collate(UserEvent.id, "C")).limit(limit)  # type: ignore[arg-type]
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_unread_for_user(
        self,
        user_id: str,
        *,
        after_id: str | None = None,
        limit: int = 200,
    ) -> list[UserEvent]:
        """Return unread events for this user in collated-id order.

        Used by the SSE replay path when the client has no cursor (first
        connection, fresh device, post-logout re-login). Uses the partial
        index ix_user_events_unread for efficient lookup.

        ``after_id`` is the pagination cursor — pass it to walk through a
        large backlog page by page (each page bounded by ``limit``). Default
        ``None`` starts from the beginning.

        Order matches the cursor (COLLATE "C" on id) so pagination is
        gap-free across pages even if id-order diverges from time-order.
        Since our public IDs encode millisecond timestamps in the high bits,
        id-order is essentially temporal order.
        """
        stmt = select(UserEvent).where(
            UserEvent.user_id == user_id,
            UserEvent.read_at.is_(None),  # type: ignore[union-attr]
        )
        if after_id is not None:
            # COLLATE "C": same reason as list_for_user — force byte-order
            # comparison so our base62 IDs paginate in temporal order.
            stmt = stmt.where(collate(UserEvent.id, "C") > after_id)  # type: ignore[arg-type]
        stmt = stmt.order_by(collate(UserEvent.id, "C")).limit(limit)  # type: ignore[arg-type]
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
