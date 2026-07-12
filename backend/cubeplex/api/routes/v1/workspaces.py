"""Workspace routes: list / create / members / archive."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import current_active_user, require_admin
from cubeplex.db import get_session
from cubeplex.models import Conversation, Role, User, Workspace
from cubeplex.models.agent_config import AgentConfig
from cubeplex.repositories import (
    MembershipRepository,
    OrganizationMembershipRepository,
    WorkspaceRepository,
)
from cubeplex.utils.time import utc_isoformat

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    name: str
    org_id: str


class WorkspaceUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


@router.get("")
async def list_my_workspaces(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    include_archived: bool = False,
) -> list[dict[str, str | None]]:
    mem_repo = MembershipRepository(session)
    ws_repo = WorkspaceRepository(session)
    memberships = await mem_repo.list_user_workspaces(user.id)
    pairs: list[tuple[str, Workspace]] = []
    for m in memberships:
        ws = await ws_repo.get(m.workspace_id)
        if ws is None:
            continue
        if not include_archived and ws.archived_at is not None:
            continue
        pairs.append((m.role, ws))

    # Aggregate max(Conversation.updated_at) per workspace — cubeplex has no
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
    from cubeplex.auth.singleton_org import get_singleton_org_id

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

    from cubeplex.mcp.workspace_bootstrap import enroll_workspace_in_org_wide_mcp

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

    from cubeplex.plugins.audit import audit_log

    await audit_log(
        action="workspace.renamed",
        user_id=ctx.user.id,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        ip=request.client.host if request.client else None,
        metadata={"new_name": body.name},
    )
    return {"id": ws.id, "name": ws.name, "org_id": ws.org_id}


@router.post("/{workspace_id}/leave")
async def leave_workspace(
    workspace_id: str,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> dict[str, bool]:
    from sqlalchemy import delete as sa_delete

    from cubeplex.models import Membership
    from cubeplex.plugins.audit import audit_log

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


@router.post("/{workspace_id}/archive")
async def archive_workspace(
    workspace_id: str,
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str | None]:
    from datetime import UTC

    ws = await WorkspaceRepository(session).get(workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="not found")

    mem_repo = MembershipRepository(session)
    ws_repo = WorkspaceRepository(session)
    memberships = await mem_repo.list_user_workspaces(ctx.user.id)
    active_count = 0
    for m in memberships:
        other = await ws_repo.get(m.workspace_id)
        if (
            other is not None
            and other.archived_at is None
            and other.id != workspace_id
            and other.org_id == ctx.org_id
        ):
            active_count += 1
    if active_count == 0:
        raise HTTPException(status_code=400, detail="cannot_archive_last_workspace")

    ws.archived_at = datetime.now(UTC)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return {"id": ws.id, "name": ws.name, "archived_at": utc_isoformat(ws.archived_at)}


@router.post("/{workspace_id}/unarchive")
async def unarchive_workspace(
    workspace_id: str,
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str | None]:
    ws = await WorkspaceRepository(session).get(workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="not found")
    ws.archived_at = None
    session.add(ws)
    await session.commit()
    return {"id": ws.id, "name": ws.name, "archived_at": None}


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: str,
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    from sqlalchemy import delete as sa_delete
    from sqlalchemy import select as sa_select

    from cubeplex.models import (
        AgentConfig,
        Artifact,
        ArtifactVersion,
        Attachment,
        Conversation,
        MCPCredentialGrant,
        MCPWorkspaceConnectorState,
        Membership,
        MemoryItem,
        OrgSkillInstall,
        ScheduledTask,
        WorkspaceSkillBinding,
    )
    from cubeplex.models.billing import BillingEvent, LlmBillingEvent
    from cubeplex.models.egress_ref import EgressRef
    from cubeplex.models.im_connector import IMConnectorAccount
    from cubeplex.models.sandbox_env import SandboxEnvVar
    from cubeplex.models.scheduled_task import ScheduledTaskRun
    from cubeplex.models.trigger import Trigger, TriggerEvent
    from cubeplex.models.user_event import UserEvent
    from cubeplex.models.user_sandbox import UserSandbox

    ws_repo = WorkspaceRepository(session)
    mem_repo = MembershipRepository(session)
    memberships = await mem_repo.list_user_workspaces(ctx.user.id)
    active_others = 0
    for m in memberships:
        other = await ws_repo.get(m.workspace_id)
        if (
            other is not None
            and other.archived_at is None
            and other.id != workspace_id
            and other.org_id == ctx.org_id
        ):
            active_others += 1
    if active_others == 0:
        raise HTTPException(status_code=400, detail="cannot_delete_last_workspace")

    # Collect vault credential ids from workspace-scoped grants and sandbox env
    # vars before those rows are deleted, so we can remove the backing secrets.
    from cubeplex.models.credential import Credential

    mcp_grant_tbl = MCPCredentialGrant.__table__  # type: ignore[attr-defined]
    ws_grant_cred_ids = (
        (
            await session.execute(
                sa_select(mcp_grant_tbl.c.credential_id).where(
                    mcp_grant_tbl.c.workspace_id == workspace_id
                )
            )
        )
        .scalars()
        .all()
    )
    ws_grant_refresh_ids = (
        (
            await session.execute(
                sa_select(mcp_grant_tbl.c.refresh_credential_id).where(
                    mcp_grant_tbl.c.workspace_id == workspace_id,
                    mcp_grant_tbl.c.refresh_credential_id.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    sandbox_tbl = SandboxEnvVar.__table__  # type: ignore[attr-defined]
    ws_sandbox_cred_ids = (
        (
            await session.execute(
                sa_select(sandbox_tbl.c.credential_id).where(
                    sandbox_tbl.c.workspace_id == workspace_id,
                    sandbox_tbl.c.credential_id.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )

    # LlmBillingEvent has no workspace_id — delete via BillingEvent parent.
    billing_tbl = BillingEvent.__table__  # type: ignore[attr-defined]
    ws_billing_ids = sa_select(billing_tbl.c.id).where(billing_tbl.c.workspace_id == workspace_id)
    await session.execute(
        sa_delete(LlmBillingEvent).where(
            LlmBillingEvent.billing_event_id.in_(ws_billing_ids)  # type: ignore[attr-defined]
        )
    )

    # Collect conversation ids in this workspace so we can delete checkpointer
    # threads and ConversationShare rows before the Conversation rows go.
    conv_tbl = Conversation.__table__  # type: ignore[attr-defined]
    ws_conv_ids = sa_select(conv_tbl.c.id).where(conv_tbl.c.workspace_id == workspace_id)

    from cubeplex.models.conversation_share import ConversationShare

    await session.execute(
        sa_delete(ConversationShare).where(
            ConversationShare.conversation_id.in_(ws_conv_ids)  # type: ignore[attr-defined]
        )
    )
    # cubepi_threads / cubepi_messages: thread_id == conversation_id.
    # cubepi_messages cascades from cubepi_threads (ON DELETE CASCADE).
    from sqlalchemy import text

    await session.execute(
        text(
            "DELETE FROM cubepi_threads WHERE thread_id IN (SELECT id FROM conversations WHERE workspace_id = :wsid)"
        ),
        {"wsid": workspace_id},
    )

    # Purge unpromoted workspace-scoped MCP connector templates (spec §10).
    # MCPConnector is org-scoped (no workspace_id), so it is NOT caught by the
    # bulk workspace cascade below. We find all templates owned by this workspace
    # (any status), hard-delete their active connectors (+ state rows + grants)
    # inline, then bulk-UPDATE the template rows to clear workspace_id and promote
    # scope to 'org'. The bulk UPDATE must set status/workspace_id/scope in one
    # statement to avoid triggering the check constraint
    # (scope='workspace' AND workspace_id IS NOT NULL) mid-transaction.
    from cubeplex.models import MCPConnector as _MCPConnector
    from cubeplex.models import MCPConnectorTemplate
    from cubeplex.models import MCPCredentialGrant as _MCPGrant
    from cubeplex.models import MCPWorkspaceConnectorState as _MCPState

    # Collect all workspace-owned templates (any status) so we can clear their
    # FK before deleting the workspace row.
    ws_tpl_stmt = sa_select(MCPConnectorTemplate).where(
        MCPConnectorTemplate.scope == "workspace",  # type: ignore[arg-type]
        MCPConnectorTemplate.workspace_id == workspace_id,  # type: ignore[arg-type]
    )
    ws_templates = list((await session.execute(ws_tpl_stmt)).scalars().all())
    for ws_tpl in ws_templates:
        # Hard-delete the active connector (if any) for each template.
        connector_stmt = sa_select(_MCPConnector).where(
            _MCPConnector.template_id == ws_tpl.id,  # type: ignore[arg-type]
            _MCPConnector.status == "active",  # type: ignore[arg-type]
        )
        connector_row = (await session.execute(connector_stmt)).scalar_one_or_none()
        if connector_row is not None:
            await session.execute(
                sa_delete(_MCPState).where(
                    _MCPState.connector_id == connector_row.id  # type: ignore[arg-type]
                )
            )
            await session.execute(
                sa_delete(_MCPGrant).where(
                    _MCPGrant.connector_id == connector_row.id  # type: ignore[arg-type]
                )
            )
            await session.delete(connector_row)
    if ws_templates:
        # Bulk-UPDATE all workspace-owned templates in one statement so that
        # workspace_id and scope change atomically — the check constraint fires on
        # the final row shape, which must satisfy scope='org' AND workspace_id IS
        # NULL. Updating via ORM instance attributes causes SQLAlchemy to emit two
        # separate UPDATEs (one per dirty field), which transiently violates the
        # constraint. Using the core UPDATE avoids that.
        ws_tpl_tbl = MCPConnectorTemplate.__table__  # type: ignore[attr-defined]
        await session.execute(
            ws_tpl_tbl.update()
            .where(
                ws_tpl_tbl.c.scope == "workspace",
                ws_tpl_tbl.c.workspace_id == workspace_id,
            )
            .values(status="deleted", workspace_id=None, scope="org")
        )
        await session.flush()

    # Delete child rows deepest-first to avoid FK violations.
    # NOTE: UserSandbox rows are deleted without calling the sandbox manager's
    # kill path. Provider sandboxes are reaped by cleanup_expired. A public
    # sandbox-manager kill-by-workspace API is the proper fix — tracked follow-up.
    ws_child_tables = [
        EgressRef,
        MemoryItem,
        WorkspaceSkillBinding,
        OrgSkillInstall,
        TriggerEvent,
        Trigger,
        ScheduledTaskRun,
        ScheduledTask,
        MCPCredentialGrant,
        MCPWorkspaceConnectorState,
        # MCPConnector is org-scoped (no workspace_id); ws-owned connectors are
        # purged above in the ws-template cascade. Shared org connectors survive.
        SandboxEnvVar,
        UserSandbox,
        ArtifactVersion,
        Artifact,
        Attachment,
        BillingEvent,
        UserEvent,
        # IMConnectorAccount must precede Conversation: each account's
        # child IM tables (thread_links, receipts, run_queue, identity_links)
        # CASCADE from the account, and ``im_thread_links.conversation_id``
        # has no CASCADE back from conversations, so deleting accounts
        # first clears the thread_links before the Conversation rows go.
        IMConnectorAccount,
        Conversation,
        AgentConfig,
        Membership,
    ]
    for model in ws_child_tables:
        col = model.workspace_id  # type: ignore[attr-defined]
        await session.execute(sa_delete(model).where(col == workspace_id))

    # Delete backing Credential rows that the deleted grants and sandbox env vars
    # referenced (they're now orphaned after the referencing rows are gone).
    all_ws_cred_ids = set(
        list(ws_grant_cred_ids) + list(ws_grant_refresh_ids) + list(ws_sandbox_cred_ids)
    )
    if all_ws_cred_ids:
        await session.execute(
            sa_delete(Credential).where(
                Credential.id.in_(list(all_ws_cred_ids))  # type: ignore[attr-defined]
            )
        )

    await session.execute(
        sa_delete(Workspace).where(Workspace.id == workspace_id)  # type: ignore[arg-type]
    )
    await session.commit()
