"""OrgProviderOverride repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.org_provider_override import OrgProviderOverride


class OrgProviderOverrideRepository:
    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(self, provider_id: str) -> OrgProviderOverride | None:
        stmt = select(OrgProviderOverride).where(
            OrgProviderOverride.org_id == self.org_id,  # type: ignore[arg-type]
            OrgProviderOverride.provider_id == provider_id,  # type: ignore[arg-type]
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def set(self, provider_id: str, enabled: bool) -> OrgProviderOverride:
        existing = await self.get(provider_id)
        if existing:
            existing.enabled = enabled
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        override = OrgProviderOverride(org_id=self.org_id, provider_id=provider_id, enabled=enabled)
        self.session.add(override)
        await self.session.commit()
        await self.session.refresh(override)
        return override

    async def delete(self, provider_id: str) -> None:
        existing = await self.get(provider_id)
        if existing:
            await self.session.delete(existing)
            await self.session.flush()
