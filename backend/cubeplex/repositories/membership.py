"""Membership repository — User × Workspace × role."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import Membership, Role, Workspace


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

    async def remove_user_from_org_workspaces(self, *, user_id: str, org_id: str) -> int:
        """Delete all workspace memberships for a user within an org. Returns count deleted."""
        from typing import Any, cast

        from sqlalchemy import delete
        from sqlalchemy.engine import CursorResult

        ws_ids_subq = (
            select(cast(Any, Workspace.id))
            .where(Workspace.org_id == org_id)  # type: ignore[arg-type]
            .scalar_subquery()
        )
        stmt = delete(Membership).where(
            Membership.user_id == user_id,  # type: ignore[arg-type]
            cast(Any, Membership.workspace_id).in_(ws_ids_subq),
        )
        result = cast(CursorResult[tuple[()]], await self.session.execute(stmt))
        return result.rowcount

    async def user_has_role_in_org(
        self,
        *,
        user_id: str,
        org_id: str,
        role: Role,
    ) -> bool:
        """True if `user_id` has `role` in any workspace belonging to `org_id`.

        v1: "org admin" = admin in any workspace of the org. M2 uses this as the
        gate for /admin/* until a real org-level role concept is introduced.
        """
        stmt = (
            select(Membership)
            .join(Workspace, Workspace.id == Membership.workspace_id)  # type: ignore[arg-type]
            .where(
                Workspace.org_id == org_id,  # type: ignore[arg-type]
                Membership.user_id == user_id,  # type: ignore[arg-type]
                Membership.role == role.value,  # type: ignore[arg-type]
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first() is not None
