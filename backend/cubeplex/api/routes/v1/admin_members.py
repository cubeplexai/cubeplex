"""Org member management routes: list / add / change-role / remove."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from cubeplex.api.schemas.execution import AccessRemovalResponse
from cubeplex.auth.dependencies import require_org_admin, resolve_current_org_id
from cubeplex.db import get_session
from cubeplex.models import Membership, OrgRole, User, Workspace
from cubeplex.repositories import MembershipRepository, OrganizationMembershipRepository
from cubeplex.services.conversation_execution import ConversationExecutionService
from cubeplex.services.execution_signals import signal_stopped_runs
from cubeplex.utils.time import utc_isoformat

router = APIRouter(prefix="/admin/members", tags=["admin-members"])

ASSIGNABLE_ROLES = {"admin", "member"}


class ChangeOrgRoleRequest(BaseModel):
    role: str


class OrgMemberOut(BaseModel):
    user_id: str
    email: str
    display_name: str | None = None
    role: str
    created_at: str


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


@router.delete("/{user_id}", response_model=AccessRemovalResponse)
async def remove_org_member(
    user_id: str,
    raw_request: Request,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AccessRemovalResponse:
    org_id = await resolve_current_org_id(user, session)

    if user_id == user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Cannot remove yourself")

    om_repo = OrganizationMembershipRepository(session)
    current = await om_repo.get_role(user_id=user_id, org_id=org_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not a member of this org")
    if current == OrgRole.OWNER:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Cannot remove org owner")

    workspace_ids = tuple(
        await session.scalars(
            select(col(Membership.workspace_id))
            .join(Workspace, col(Workspace.id) == col(Membership.workspace_id))
            .where(
                col(Membership.user_id) == user_id,
                col(Workspace.org_id) == org_id,
            )
            .order_by(col(Membership.workspace_id))
        )
    )
    mem_repo = MembershipRepository(session)
    await mem_repo.remove_user_from_org_workspaces(user_id=user_id, org_id=org_id)
    now = datetime.now(UTC)
    revocations = []
    for workspace_id in workspace_ids:
        revocations.append(
            (
                workspace_id,
                await ConversationExecutionService(
                    session, org_id=org_id, workspace_id=workspace_id
                ).revoke_actor_access(actor_user_id=user_id, now=now),
            )
        )
    await om_repo.revoke(user_id=user_id, org_id=org_id)
    await session.commit()
    for workspace_id, revoked in revocations:
        for conversation_id, run_ids in revoked.conversation_runs:
            await signal_stopped_runs(
                raw_request.app.state.run_manager,
                conversation_id=conversation_id,
                run_ids=run_ids,
                user_id=user_id,
                org_id=org_id,
                workspace_id=workspace_id,
            )
    return AccessRemovalResponse(
        removed=True,
        cleanup_pending=any(revoked.cleanup_pending for _, revoked in revocations),
    )
