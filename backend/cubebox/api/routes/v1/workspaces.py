"""Workspace routes: list / create / invite / accept-invite."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import current_active_user, require_admin
from cubebox.config import config
from cubebox.db import get_session
from cubebox.models import Conversation, Role, User, Workspace
from cubebox.models.agent_config import AgentConfig
from cubebox.repositories import (
    InviteTokenRepository,
    MembershipRepository,
    OrganizationMembershipRepository,
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
    role: Literal["admin", "member"]
    email: str | None = None


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
) -> dict[str, str | bool]:
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

    email_sent = False
    if body.email is not None:
        from cubebox.services.email import get_email_service

        base_url = config.get("app.base_url", "http://localhost:3000")
        invite_url = f"{base_url}/invite/accept?token={tok.token}"
        ws = await WorkspaceRepository(session).get(workspace_id)
        ws_name = ws.name if ws else workspace_id
        try:
            await get_email_service().send(
                to=body.email,
                subject=f"You're invited to {ws_name} on cubebox",
                template="workspace_invite",
                context={"invite_url": invite_url, "workspace_name": ws_name},
            )
            email_sent = True
        except Exception:
            logger.warning("Failed to send invite email to {}", body.email)

    return {
        "token": tok.token,
        "expires_at": utc_isoformat(tok.expires_at),
        "email_sent": email_sent,
    }


@router.get("/{workspace_id}/invites")
async def list_invites(
    workspace_id: str,
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, str | None]]:
    inv_repo = InviteTokenRepository(session)
    tokens = await inv_repo.list_for_workspace(workspace_id)
    return [
        {
            "token": t.token,
            "role": t.role,
            "created_by": t.created_by,
            "expires_at": utc_isoformat(t.expires_at),
            "used_at": utc_isoformat(t.used_at) if t.used_at else None,
        }
        for t in tokens
    ]


@router.delete("/{workspace_id}/invites/{token}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    workspace_id: str,
    token: str,
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    inv_repo = InviteTokenRepository(session)
    await inv_repo.delete(token)


@router.post("/{workspace_id}/leave")
async def leave_workspace(
    workspace_id: str,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> dict[str, bool]:
    from sqlalchemy import delete as sa_delete

    from cubebox.models import Membership
    from cubebox.plugins.audit import audit_log

    mem_repo = MembershipRepository(session)
    role = await mem_repo.get_role(user_id=user.id, workspace_id=workspace_id)
    if role is None:
        raise HTTPException(status_code=404, detail="not a member")

    if role == Role.ADMIN:
        members = await mem_repo.list_workspace_members(workspace_id)
        admin_count = sum(1 for m in members if m.role == Role.ADMIN.value)
        if admin_count <= 1:
            raise HTTPException(
                status_code=400,
                detail="cannot_leave_as_last_admin",
            )

    ws = await WorkspaceRepository(session).get(workspace_id)
    org_id = ws.org_id if ws else None

    await session.execute(
        sa_delete(Membership).where(
            Membership.user_id == user.id,  # type: ignore[arg-type]
            Membership.workspace_id == workspace_id,  # type: ignore[arg-type]
        )
    )
    await session.commit()

    await audit_log(
        action="workspace.member_left",
        user_id=user.id,
        org_id=org_id,
        workspace_id=workspace_id,
        ip=request.client.host if request.client else None,
    )
    return {"left": True}


@router.post("/invites/accept")
async def accept_invite(
    body: Annotated[AcceptInvite, Body()],
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    from cubebox.models import OrgRole

    inv_repo = InviteTokenRepository(session)
    tok = await inv_repo.consume(body.token)
    if tok is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invite token invalid, expired, or already used",
        )

    ws = await WorkspaceRepository(session).get(tok.workspace_id)
    ws_name = ws.name if ws else ""
    org_id = ws.org_id if ws else ""

    if ws is not None:
        om_repo = OrganizationMembershipRepository(session)
        existing_org_role = await om_repo.get_role(user_id=user.id, org_id=ws.org_id)
        if existing_org_role is None:
            await om_repo.grant(user_id=user.id, org_id=ws.org_id, role=OrgRole.MEMBER)

    mem_repo = MembershipRepository(session)
    existing = await mem_repo.get_role(user_id=user.id, workspace_id=tok.workspace_id)
    if existing is None:
        await mem_repo.grant(user_id=user.id, workspace_id=tok.workspace_id, role=Role(tok.role))

    return {
        "workspace_id": tok.workspace_id,
        "workspace_name": ws_name,
        "org_id": org_id,
        "role": tok.role,
    }
