"""Org member management routes: list / add / change-role / remove."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.dependencies import require_org_admin, resolve_current_org_id
from cubebox.db import get_session
from cubebox.models import OrgRole, User
from cubebox.repositories import MembershipRepository, OrganizationMembershipRepository
from cubebox.utils.time import utc_isoformat

router = APIRouter(prefix="/admin/members", tags=["admin-members"])

ASSIGNABLE_ROLES = {"admin", "member"}


class AddOrgMemberRequest(BaseModel):
    email: str
    role: str


class ChangeOrgRoleRequest(BaseModel):
    role: str


class OrgMemberOut(BaseModel):
    user_id: str
    email: str
    display_name: str | None = None
    role: str
    created_at: str


class AddOrgMemberResponse(BaseModel):
    user_id: str
    email: str
    display_name: str | None = None
    role: str


class ChangeOrgRoleResponse(BaseModel):
    user_id: str
    role: str


@router.get("", response_model=list[OrgMemberOut])
async def list_org_members(
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[OrgMemberOut]:
    org_id = await resolve_current_org_id(user, session)
    om_repo = OrganizationMembershipRepository(session)
    members = await om_repo.list_org_members(org_id)

    user_ids = [m.user_id for m in members]
    if not user_ids:
        return []
    stmt = select(User).where(User.id.in_(user_ids))  # type: ignore[attr-defined]
    users = {u.id: u for u in (await session.execute(stmt)).scalars().all()}

    return [
        OrgMemberOut(
            user_id=m.user_id,
            email=users[m.user_id].email if m.user_id in users else "",
            display_name=users[m.user_id].display_name if m.user_id in users else None,
            role=m.role,
            created_at=utc_isoformat(m.created_at),
        )
        for m in members
    ]


@router.post("", response_model=AddOrgMemberResponse, status_code=status.HTTP_201_CREATED)
async def add_org_member(
    body: AddOrgMemberRequest,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AddOrgMemberResponse:
    if body.role not in ASSIGNABLE_ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="role must be admin or member")
    org_id = await resolve_current_org_id(user, session)

    target = (
        await session.execute(select(User).where(User.email == body.email))  # type: ignore[arg-type]
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No user with this email")

    om_repo = OrganizationMembershipRepository(session)
    existing = await om_repo.get_role(user_id=target.id, org_id=org_id)
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Already a member")

    await om_repo.grant(user_id=target.id, org_id=org_id, role=OrgRole(body.role))
    return AddOrgMemberResponse(
        user_id=target.id, email=target.email, display_name=target.display_name, role=body.role
    )


@router.patch("/{user_id}/role", response_model=ChangeOrgRoleResponse)
async def update_org_member_role(
    user_id: str,
    body: ChangeOrgRoleRequest,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChangeOrgRoleResponse:
    if body.role not in ASSIGNABLE_ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="role must be admin or member")
    org_id = await resolve_current_org_id(user, session)

    om_repo = OrganizationMembershipRepository(session)
    current = await om_repo.get_role(user_id=user_id, org_id=org_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not a member of this org")
    if current == OrgRole.OWNER:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Cannot change owner role")

    await om_repo.promote(user_id=user_id, org_id=org_id, role=OrgRole(body.role))
    return ChangeOrgRoleResponse(user_id=user_id, role=body.role)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_org_member(
    user_id: str,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    org_id = await resolve_current_org_id(user, session)

    if user_id == user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Cannot remove yourself")

    om_repo = OrganizationMembershipRepository(session)
    current = await om_repo.get_role(user_id=user_id, org_id=org_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not a member of this org")
    if current == OrgRole.OWNER:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Cannot remove org owner")

    mem_repo = MembershipRepository(session)
    await mem_repo.remove_user_from_org_workspaces(user_id=user_id, org_id=org_id)
    await om_repo.revoke(user_id=user_id, org_id=org_id)
