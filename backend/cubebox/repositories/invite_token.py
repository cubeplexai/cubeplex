"""Invite token repository — single-use + time-limited."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import InviteToken


class InviteTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def issue(self, *, workspace_id: str, role: str, created_by: str) -> InviteToken:
        tok = InviteToken(workspace_id=workspace_id, role=role, created_by=created_by)
        self.session.add(tok)
        await self.session.commit()
        await self.session.refresh(tok)
        return tok

    async def consume(self, token: str) -> InviteToken | None:
        """Atomically mark token as used. Returns the token if successful, None if expired/used/missing."""
        stmt = select(InviteToken).where(InviteToken.token == token).with_for_update()  # type: ignore[arg-type]
        tok = (await self.session.execute(stmt)).scalar_one_or_none()
        if tok is None:
            return None
        now = datetime.now(UTC)
        # `timestamp without time zone` columns drop tz on round-trip — coerce to UTC-aware before comparing.
        expires_at = tok.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if tok.used_at is not None or expires_at < now:
            return None
        tok.used_at = now
        await self.session.commit()
        await self.session.refresh(tok)
        return tok
