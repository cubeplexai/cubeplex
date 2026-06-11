"""Workspace routes: list / create / invite / accept-invite."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import current_active_user, require_admin
from cubebox.db import get_session
from cubebox.models import Conversation, Role, User, Workspace
from cubebox.models.agent_config import AgentConfig
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


class WorkspaceUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class InviteCreate(BaseModel):
    role: str  # 'admin' or 'member'


class AcceptInvite(BaseModel):
    token: str


@router.get("")
async def list_my_workspaces(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, str | None]]:
    mem_repo = MembershipRepository(session)
    ws_repo = WorkspaceRepository(session)
    memberships = await mem_repo.list_user_workspaces(user.id)
    pairs: list[tuple[str, Workspace]] = []
    for m in memberships:
        ws = await ws_repo.get(m.workspace_id)
        if ws is not None:
            pairs.append((m.role, ws))

    # Aggregate max(Conversation.updated_at) per workspace — cubebox has no
    # Message table (history lives in cubepi PostgresCheckpointer), but
    # ConversationRepository.update_timestamp() bumps updated_at on every
    # message round-trip, so this is an accurate "last activity" signal.
    activity_map: dict[str, datetime] = {}
    ws_ids = [ws.id for _, ws in pairs]
    if ws_ids:
        tbl = Conversation.__table__  # type: ignore[attr-defined]
        stmt = (
            select(tbl.c.workspace_id, func.max(tbl.c.updated_at))
            .where(tbl.c.workspace_id.in_(ws_ids))
            .group_by(tbl.c.workspace_id)
        )
        rows = (await session.execute(stmt)).all()
        activity_map = {row[0]: row[1] for row in rows}

    out: list[dict[str, str | None]] = []
    for role, ws in pairs:
        last_at = activity_map.get(ws.id)
        out.append(
            {
                "id": ws.id,
                "name": ws.name,
                "org_id": ws.org_id,
                "role": role,
                "last_activity_at": utc_isoformat(last_at) if last_at else None,
            }
        )
    return out


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: Annotated[WorkspaceCreate, Body()],
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> dict[str, str]:
    from cubebox.auth.singleton_org import get_singleton_org_id
    from cubebox.repositories import OrganizationMembershipRepository

    mode = getattr(request.app.state, "deployment_mode", "single_tenant")
    if mode == "single_tenant":
        org_id = await get_singleton_org_id(session)
        if org_id is None:
            raise HTTPException(status_code=409, detail="setup_required")
    else:
        org_id = body.org_id
        if not await OrganizationMembershipRepository(session).get_role(
            user_id=user.id, org_id=org_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="not a member of this org",
            )

    from cubebox.mcp.workspace_bootstrap import enroll_workspace_in_org_wide_mcp

    ws_repo = WorkspaceRepository(session)
    mem_repo = MembershipRepository(session)
    ws = await ws_repo.create(org_id=org_id, name=body.name)
    await mem_repo.grant(user_id=user.id, workspace_id=ws.id, role=Role.ADMIN)
    agent_cfg = AgentConfig(org_id=org_id, workspace_id=ws.id)
    session.add(agent_cfg)
    await enroll_workspace_in_org_wide_mcp(
        session, org_id=org_id, workspace_id=ws.id, actor_user_id=user.id
    )
    await session.commit()
    return {"id": ws.id, "name": ws.name, "org_id": ws.org_id}


@router.patch("/{workspace_id}")
async def rename_workspace(
    workspace_id: str,
    body: Annotated[WorkspaceUpdate, Body()],
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> dict[str, str]:
    ws_repo = WorkspaceRepository(session)
    ws = await ws_repo.update_name(workspace_id, body.name)
    if ws is None:
        raise HTTPException(status_code=404, detail="workspace not found")

    from cubebox.plugins.audit import audit_log

    await audit_log(
        action="workspace.renamed",
        user_id=ctx.user.id,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        ip=request.client.host if request.client else None,
        metadata={"new_name": body.name},
    )
    return {"id": ws.id, "name": ws.name, "org_id": ws.org_id}


@router.post("/{workspace_id}/invites", status_code=status.HTTP_201_CREATED)
async def create_invite(
    workspace_id: str,
    body: Annotated[InviteCreate, Body()],
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> dict[str, str]:
    if body.role not in ("admin", "member"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="role must be admin or member"
        )
    inv_repo = InviteTokenRepository(session)
    tok = await inv_repo.issue(workspace_id=workspace_id, role=body.role, created_by=ctx.user.id)

    from cubebox.plugins.audit import audit_log

    await audit_log(
        action="workspace.invite_created",
        user_id=ctx.user.id,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        target_type="invite",
        target_id=tok.token,
        ip=request.client.host if request.client else None,
        metadata={"role": body.role},
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
        await mem_repo.grant(user_id=user.id, workspace_id=tok.workspace_id, role=Role(tok.role))
    return {"workspace_id": tok.workspace_id, "role": tok.role}
