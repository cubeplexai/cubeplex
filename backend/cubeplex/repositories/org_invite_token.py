"""Org invite token repository — single-use + time-limited."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import OrgInviteToken


class OrgInviteEmailMismatch(Exception):
    """The token is still valid, but this account is not the invited address."""


class OrgInviteTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def issue(
        self,
        *,
        org_id: str,
        role: str,
        created_by: str,
        email: str | None = None,
    ) -> OrgInviteToken:
        tok = OrgInviteToken(org_id=org_id, role=role, created_by=created_by, email=email)
        self.session.add(tok)
        await self.session.commit()
        await self.session.refresh(tok)
        return tok

    async def consume(self, token: str, *, acceptor_email: str) -> OrgInviteToken | None:
        """Mark the token used. None if expired, used, or missing.

        An addressed invite is left unused when ``acceptor_email`` does not
        match, and ``OrgInviteEmailMismatch`` is raised instead.
        """
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
        invited = (tok.email or "").strip().lower()
        if invited and invited != acceptor_email.strip().lower():
            await self.session.rollback()
            raise OrgInviteEmailMismatch
        tok.used_at = now
        await self.session.commit()
        await self.session.refresh(tok)
        return tok
