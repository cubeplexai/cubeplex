"""Workspace routes: list / create / invite / accept-invite."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import current_active_user, require_admin
from cubebox.db import get_session
from cubebox.models import Role, User
from cubebox.repositories import (
    InviteTokenRepository,
    MembershipRepository,
    WorkspaceRepository,
)
from cubebox.utils.time import utc_isoformat

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    name: str
    org_id: str


class InviteCreate(BaseModel):
    role: str  # 'admin' or 'member'


class AcceptInvite(BaseModel):
    token: str


@router.get("")
async def list_my_workspaces(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, str]]:
    mem_repo = MembershipRepository(session)
    ws_repo = WorkspaceRepository(session)
    memberships = await mem_repo.list_user_workspaces(user.id)
    out: list[dict[str, str]] = []
    for m in memberships:
        ws = await ws_repo.get(m.workspace_id)
        if ws:
            out.append({"id": ws.id, "name": ws.name, "org_id": ws.org_id, "role": m.role})
    return out


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: Annotated[WorkspaceCreate, Body()],
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    ws_repo = WorkspaceRepository(session)
    mem_repo = MembershipRepository(session)
    ws = await ws_repo.create(org_id=body.org_id, name=body.name)
    await mem_repo.grant(user_id=user.id, workspace_id=ws.id, role=Role.ADMIN)
    return {"id": ws.id, "name": ws.name, "org_id": ws.org_id}


@router.post("/{workspace_id}/invites", status_code=status.HTTP_201_CREATED)
async def create_invite(
    workspace_id: str,
    body: Annotated[InviteCreate, Body()],
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    if ctx.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="X-Workspace-Id header must match workspace_id in path",
        )
    if body.role not in ("admin", "member"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="role must be admin or member"
        )
    inv_repo = InviteTokenRepository(session)
    tok = await inv_repo.issue(
        workspace_id=workspace_id, role=body.role, created_by=ctx.user.id
    )
    return {"token": tok.token, "expires_at": utc_isoformat(tok.expires_at)}


@router.post("/invites/accept")
async def accept_invite(
    body: Annotated[AcceptInvite, Body()],
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    inv_repo = InviteTokenRepository(session)
    tok = await inv_repo.consume(body.token)
    if tok is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invite token invalid, expired, or already used",
        )
    mem_repo = MembershipRepository(session)
    existing = await mem_repo.get_role(user_id=user.id, workspace_id=tok.workspace_id)
    if existing is None:
        await mem_repo.grant(
            user_id=user.id, workspace_id=tok.workspace_id, role=Role(tok.role)
        )
    return {"workspace_id": tok.workspace_id, "role": tok.role}
