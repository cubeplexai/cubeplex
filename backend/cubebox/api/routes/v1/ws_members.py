"""Workspace member management routes: list / available / add / change-role / remove."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_admin, require_member
from cubebox.db import get_session
from cubebox.models import Membership, Role, User
from cubebox.repositories import MembershipRepository, OrganizationMembershipRepository
from cubebox.services.avatar_store import resolve_avatar_url
from cubebox.utils.time import utc_isoformat

router = APIRouter(prefix="/ws/{workspace_id}/members", tags=["workspace-members"])

ASSIGNABLE_ROLES = {"admin", "member"}


class AddWsMemberRequest(BaseModel):
    user_id: str
    role: str


class ChangeWsRoleRequest(BaseModel):
    role: str


class WsMemberOut(BaseModel):
    user_id: str
    email: str
    display_name: str | None = None
    avatar_url: str | None = None
    avatar_seed: str | None = None
    role: str
    created_at: str


class AvailableOrgMemberOut(BaseModel):
    user_id: str
    email: str
    org_role: str


class AddWsMemberResponse(BaseModel):
    user_id: str
    email: str
    display_name: str | None = None
    role: str


class ChangeWsRoleResponse(BaseModel):
    user_id: str
    role: str


@router.get("", response_model=list[WsMemberOut])
async def list_workspace_members(
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[WsMemberOut]:
    """Workspace member list.

    Open to any workspace member — they need to see each other to invite
    people to conversations, topics, and group chats. Workspace members
    already share message history, sandboxes, and artifacts, so exposing
    the member roster (email, display name, role) doesn't widen access.
    """
    mem_repo = MembershipRepository(session)
    members = await mem_repo.list_workspace_members(ctx.workspace_id)

    user_ids = [m.user_id for m in members]
    if not user_ids:
        return []
    stmt = select(User).where(User.id.in_(user_ids))  # type: ignore[attr-defined]
    users = {u.id: u for u in (await session.execute(stmt)).scalars().all()}

    return [
        WsMemberOut(
            user_id=m.user_id,
            email=users[m.user_id].email if m.user_id in users else "",
            display_name=users[m.user_id].display_name if m.user_id in users else None,
            avatar_url=resolve_avatar_url(
                users[m.user_id].avatar_url, m.user_id, users[m.user_id].updated_at
            )
            if m.user_id in users
            else None,
            avatar_seed=users[m.user_id].avatar_seed if m.user_id in users else None,
            role=m.role,
            created_at=utc_isoformat(m.created_at),
        )
        for m in members
    ]


@router.get("/available", response_model=list[AvailableOrgMemberOut])
async def list_available_org_members(
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[AvailableOrgMemberOut]:
    """Org members who are NOT already in this workspace."""
    om_repo = OrganizationMembershipRepository(session)
    org_members = await om_repo.list_org_members(ctx.org_id)

    mem_repo = MembershipRepository(session)
    ws_members = await mem_repo.list_workspace_members(ctx.workspace_id)
    ws_user_ids = {m.user_id for m in ws_members}

    available = [m for m in org_members if m.user_id not in ws_user_ids]
    if not available:
        return []

    user_ids = [m.user_id for m in available]
    stmt = select(User).where(User.id.in_(user_ids))  # type: ignore[attr-defined]
    users = {u.id: u for u in (await session.execute(stmt)).scalars().all()}

    return [
        AvailableOrgMemberOut(
            user_id=m.user_id,
            email=users[m.user_id].email if m.user_id in users else "",
            org_role=m.role,
        )
        for m in available
    ]


@router.post("", response_model=AddWsMemberResponse, status_code=status.HTTP_201_CREATED)
async def add_workspace_member(
    body: AddWsMemberRequest,
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AddWsMemberResponse:
    if body.role not in ASSIGNABLE_ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="role must be admin or member")

    om_repo = OrganizationMembershipRepository(session)
    org_role = await om_repo.get_role(user_id=body.user_id, org_id=ctx.org_id)
    if org_role is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="User is not a member of this organization"
        )

    mem_repo = MembershipRepository(session)
    existing = await mem_repo.get_role(user_id=body.user_id, workspace_id=ctx.workspace_id)
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Already a workspace member")

    await mem_repo.grant(user_id=body.user_id, workspace_id=ctx.workspace_id, role=Role(body.role))

    target = await session.get(User, body.user_id)
    email = target.email if target else ""
    display_name = target.display_name if target else None
    return AddWsMemberResponse(
        user_id=body.user_id, email=email, display_name=display_name, role=body.role
    )


@router.patch("/{user_id}/role", response_model=ChangeWsRoleResponse)
async def update_workspace_member_role(
    user_id: str,
    body: ChangeWsRoleRequest,
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChangeWsRoleResponse:
    if body.role not in ASSIGNABLE_ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="role must be admin or member")

    mem_repo = MembershipRepository(session)
    current = await mem_repo.get_role(user_id=user_id, workspace_id=ctx.workspace_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not a member of this workspace")

    demoting_self_admin = (
        user_id == ctx.user.id and current == Role.ADMIN and body.role != Role.ADMIN.value
    )
    if demoting_self_admin:
        members = await mem_repo.list_workspace_members(ctx.workspace_id)
        other_admins = [
            m for m in members if m.user_id != ctx.user.id and m.role == Role.ADMIN.value
        ]
        if not other_admins:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Cannot demote the last workspace admin",
            )

    stmt = (
        update(Membership)
        .where(
            Membership.user_id == user_id,  # type: ignore[arg-type]
            Membership.workspace_id == ctx.workspace_id,  # type: ignore[arg-type]
        )
        .values(role=body.role)
    )
    await session.execute(stmt)
    await session.commit()
    return ChangeWsRoleResponse(user_id=user_id, role=body.role)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_workspace_member(
    user_id: str,
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    if user_id == ctx.user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Cannot remove yourself")

    mem_repo = MembershipRepository(session)
    current = await mem_repo.get_role(user_id=user_id, workspace_id=ctx.workspace_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not a member of this workspace")

    stmt = delete(Membership).where(
        Membership.user_id == user_id,  # type: ignore[arg-type]
        Membership.workspace_id == ctx.workspace_id,  # type: ignore[arg-type]
    )
    await session.execute(stmt)
    await session.commit()
