"""OrganizationMembership repository — User × Organization × OrgRole."""

from typing import cast

from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import OrganizationMembership, OrgRole


class OrganizationMembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def grant(self, *, user_id: str, org_id: str, role: OrgRole) -> OrganizationMembership:
        m = OrganizationMembership(user_id=user_id, org_id=org_id, role=role.value)
        self.session.add(m)
        await self.session.commit()
        await self.session.refresh(m)
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
        stmt = (
            update(OrganizationMembership)
            .where(
                OrganizationMembership.user_id == user_id,  # type: ignore[arg-type]
                OrganizationMembership.org_id == org_id,  # type: ignore[arg-type]
            )
            .values(role=role.value)
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[tuple[()]], await self.session.execute(stmt))
        await self.session.commit()
        if result.rowcount == 0:
            return None
        # SELECT the updated row to return it
        sel = select(OrganizationMembership).where(
            OrganizationMembership.user_id == user_id,  # type: ignore[arg-type]
            OrganizationMembership.org_id == org_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(sel)).scalar_one_or_none()

    async def revoke(self, *, user_id: str, org_id: str) -> bool:
        stmt = delete(OrganizationMembership).where(
            OrganizationMembership.user_id == user_id,  # type: ignore[arg-type]
            OrganizationMembership.org_id == org_id,  # type: ignore[arg-type]
        )
        result = cast(CursorResult[tuple[()]], await self.session.execute(stmt))
        await self.session.commit()
        return result.rowcount > 0
