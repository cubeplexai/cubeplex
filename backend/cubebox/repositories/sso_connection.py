"""SSO connection repository — org-scoped, one connection per org."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models.sso_connection import SSOConnection


class SSOConnectionRepository:
    """Repository for the per-org SSO connection.

    Each organization has at most one SSO connection. Operations are
    scoped to ``org_id`` so the same session can host repositories for
    different orgs without leaking rows across tenants.
    """

    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(self) -> SSOConnection | None:
        """Get the SSO connection for this org (at most one)."""
        stmt = select(SSOConnection).where(
            SSOConnection.org_id == self.org_id  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_id(self, sso_id: str) -> SSOConnection | None:
        stmt = select(SSOConnection).where(
            SSOConnection.id == sso_id,  # type: ignore[arg-type]
            SSOConnection.org_id == self.org_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add(self, conn: SSOConnection) -> SSOConnection:
        conn.org_id = self.org_id
        self.session.add(conn)
        await self.session.commit()
        await self.session.refresh(conn)
        return conn

    async def update(self, conn: SSOConnection) -> SSOConnection:
        conn.updated_at = datetime.now(UTC)
        self.session.add(conn)
        await self.session.commit()
        await self.session.refresh(conn)
        return conn

    async def delete(self, sso_id: str) -> bool:
        conn = await self.get_by_id(sso_id)
        if conn is None:
            return False
        await self.session.delete(conn)
        await self.session.commit()
        return True
