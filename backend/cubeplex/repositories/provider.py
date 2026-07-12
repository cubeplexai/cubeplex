"""Provider repository — queries visible providers for an org."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.org_provider_override import OrgProviderOverride
from cubeplex.models.provider import Provider


class ProviderRepository:
    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def list_visible(self) -> list[Provider]:
        """Return system providers (not disabled by this org) + this org's own."""
        stmt = (
            select(Provider)
            .outerjoin(
                OrgProviderOverride,
                (Provider.id == OrgProviderOverride.provider_id)
                & (OrgProviderOverride.org_id == self.org_id),  # type: ignore[arg-type]
            )
            .where(
                (Provider.org_id.is_(None))  # type: ignore[union-attr]
                | (Provider.org_id == self.org_id)
            )
            .where(func.coalesce(OrgProviderOverride.enabled, Provider.enabled, True))
            .order_by(Provider.org_id.nullsfirst(), Provider.name)  # type: ignore[union-attr]
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get(self, provider_id: str) -> Provider | None:
        stmt = (
            select(Provider)
            .where(Provider.id == provider_id)  # type: ignore[arg-type]
            .where(
                (Provider.org_id.is_(None))  # type: ignore[union-attr]
                | (Provider.org_id == self.org_id)
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Provider | None:
        stmt = (
            select(Provider)
            .where(
                (Provider.org_id.is_(None))  # type: ignore[union-attr]
                | (Provider.org_id == self.org_id)
            )
            .where(Provider.name == name)  # type: ignore[arg-type]
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Provider | None:
        # Spans the org bucket + the system (org_id NULL) bucket. App-level
        # uniqueness keeps these from colliding, but order org-scoped first and
        # take one row defensively (the DB partial indexes don't forbid a cross-
        # bucket duplicate, so never let this raise MultipleResultsFound).
        stmt = (
            select(Provider)
            .where(
                (Provider.org_id.is_(None))  # type: ignore[union-attr]
                | (Provider.org_id == self.org_id)
            )
            .where(Provider.slug == slug)  # type: ignore[arg-type]
            .order_by(Provider.org_id.is_(None))  # type: ignore[union-attr]  # org-scoped (False) before system (True)
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def add(self, provider: Provider) -> Provider:
        provider.org_id = self.org_id
        self.session.add(provider)
        await self.session.commit()
        await self.session.refresh(provider)
        return provider

    async def update(self, provider: Provider) -> Provider:
        await self.session.commit()
        await self.session.refresh(provider)
        return provider

    async def delete(self, provider: Provider) -> None:
        await self.session.delete(provider)
        await self.session.flush()
