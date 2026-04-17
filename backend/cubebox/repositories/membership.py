"""Membership repository — User × Workspace × role."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import Membership, Role


class MembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def grant(self, *, user_id: str, workspace_id: str, role: Role) -> Membership:
        m = Membership(user_id=user_id, workspace_id=workspace_id, role=role.value)
        self.session.add(m)
        await self.session.commit()
        return m

    async def get_role(self, *, user_id: str, workspace_id: str) -> Role | None:
        stmt = select(Membership).where(
            Membership.user_id == user_id,  # type: ignore[arg-type]
            Membership.workspace_id == workspace_id,  # type: ignore[arg-type]
        )
        m = (await self.session.execute(stmt)).scalar_one_or_none()
        return Role(m.role) if m else None

    async def list_user_workspaces(self, user_id: str) -> list[Membership]:
        stmt = select(Membership).where(Membership.user_id == user_id)  # type: ignore[arg-type]
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_workspace_members(self, workspace_id: str) -> list[Membership]:
        stmt = select(Membership).where(Membership.workspace_id == workspace_id)  # type: ignore[arg-type]
        return list((await self.session.execute(stmt)).scalars().all())
