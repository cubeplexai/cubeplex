"""Organization repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import Organization


class OrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, name: str, slug: str) -> Organization:
        org = Organization(name=name, slug=slug)
        self.session.add(org)
        await self.session.commit()
        await self.session.refresh(org)
        return org

    async def get(self, org_id: str) -> Organization | None:
        stmt = select(Organization).where(Organization.id == org_id)  # type: ignore[arg-type]
        return (await self.session.execute(stmt)).scalar_one_or_none()
