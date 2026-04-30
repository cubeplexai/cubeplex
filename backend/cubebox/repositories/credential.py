"""Credential repository — org-scoped, no workspace dimension."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import Credential


class CredentialRepository:
    """Repository for vault credentials scoped by organization."""

    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(self, credential_id: str) -> Credential | None:
        stmt = select(Credential).where(
            Credential.id == credential_id,  # type: ignore[arg-type]
            Credential.org_id == self.org_id,  # type: ignore[arg-type]
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
