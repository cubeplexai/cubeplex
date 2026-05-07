"""OrganizationMembership repository — User × Organization × OrgRole."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import OrganizationMembership, OrgRole


class OrganizationMembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def grant(self, *, user_id: str, org_id: str, role: OrgRole) -> OrganizationMembership:
        m = OrganizationMembership(user_id=user_id, org_id=org_id, role=role.value)
        self.session.add(m)
        await self.session.commit()
        return m

    async def get_role(self, *, user_id: str, org_id: str) -> OrgRole | None:
        stmt = select(OrganizationMembership).where(
            OrganizationMembership.user_id == user_id,  # type: ignore[arg-type]
            OrganizationMembership.org_id == org_id,  # type: ignore[arg-type]
        )
        m = (await self.session.execute(stmt)).scalar_one_or_none()
        return OrgRole(m.role) if m else None

    async def is_admin(self, *, user_id: str, org_id: str) -> bool:
        role = await self.get_role(user_id=user_id, org_id=org_id)
        return role in (OrgRole.OWNER, OrgRole.ADMIN)

    async def list_org_members(self, org_id: str) -> list[OrganizationMembership]:
        stmt = select(OrganizationMembership).where(
            OrganizationMembership.org_id == org_id  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def promote(
        self, *, user_id: str, org_id: str, role: OrgRole
    ) -> OrganizationMembership | None:
        """Update an existing member's role. Returns updated row or None."""
        stmt = select(OrganizationMembership).where(
            OrganizationMembership.user_id == user_id,  # type: ignore[arg-type]
            OrganizationMembership.org_id == org_id,  # type: ignore[arg-type]
        )
        m = (await self.session.execute(stmt)).scalar_one_or_none()
        if m is None:
            return None
        m.role = role.value
        await self.session.commit()
        return m

    async def revoke(self, *, user_id: str, org_id: str) -> bool:
        stmt = select(OrganizationMembership).where(
            OrganizationMembership.user_id == user_id,  # type: ignore[arg-type]
            OrganizationMembership.org_id == org_id,  # type: ignore[arg-type]
        )
        m = (await self.session.execute(stmt)).scalar_one_or_none()
        if m is None:
            return False
        await self.session.delete(m)
        await self.session.commit()
        return True
