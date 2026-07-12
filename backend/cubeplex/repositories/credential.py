"""Credential repository — org-scoped or system (org_id NULL), no workspace dimension."""

from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import Credential


class CredentialRepository:
    """Repository for vault credentials.

    Pass ``org_id=None`` to operate on system-level credentials (e.g. seeded
    provider api keys).
    """

    def __init__(self, session: AsyncSession, *, org_id: str | None) -> None:
        self.session = session
        self.org_id = org_id

    def _scope(self, stmt: Select[tuple[Credential]]) -> Select[tuple[Credential]]:
        if self.org_id is None:
            return stmt.where(Credential.org_id.is_(None))  # type: ignore[union-attr]
        return stmt.where(Credential.org_id == self.org_id)  # type: ignore[arg-type]

    async def get(self, credential_id: str) -> Credential | None:
        stmt = self._scope(
            select(Credential).where(Credential.id == credential_id)  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_kind_name(self, *, kind: str, name: str) -> Credential | None:
        stmt = self._scope(
            select(Credential).where(
                Credential.kind == kind,  # type: ignore[arg-type]
                Credential.name == name,  # type: ignore[arg-type]
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add(self, cred: Credential) -> Credential:
        cred.org_id = self.org_id
        self.session.add(cred)
        await self.session.commit()
        await self.session.refresh(cred)
        return cred

    async def update(self, cred: Credential) -> Credential:
        cred.updated_at = datetime.now(UTC)
        self.session.add(cred)
        await self.session.commit()
        await self.session.refresh(cred)
        return cred

    async def delete(self, credential_id: str) -> None:
        cred = await self.get(credential_id)
        if cred is None:
            return
        await self.session.delete(cred)
        await self.session.commit()
