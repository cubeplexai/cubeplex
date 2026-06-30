"""Org invite token repository — single-use + time-limited."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import OrgInviteToken


class OrgInviteTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def issue(self, *, org_id: str, role: str, created_by: str) -> OrgInviteToken:
        tok = OrgInviteToken(org_id=org_id, role=role, created_by=created_by)
        self.session.add(tok)
        await self.session.commit()
        await self.session.refresh(tok)
        return tok

    async def consume(self, token: str) -> OrgInviteToken | None:
        """Atomically mark token used. None if expired/used/missing."""
        stmt = (
            select(OrgInviteToken)
            .where(OrgInviteToken.token == token)  # type: ignore[arg-type]
            .with_for_update()
        )
        tok = (await self.session.execute(stmt)).scalar_one_or_none()
        if tok is None:
            return None
        now = datetime.now(UTC)
        expires_at = tok.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if tok.used_at is not None or expires_at < now:
            return None
        tok.used_at = now
        await self.session.commit()
        await self.session.refresh(tok)
        return tok
