"""Workspace-scope user sandbox routes.

- ``GET    /sandboxes``                   — list the caller's own sandbox entities.
- ``POST   /sandboxes/{id}/restart``      — request restart; keep row + PVC.
- ``DELETE /sandboxes/{id}``              — request delete; hide after confirmed cleanup.

Scope-isolated: no admin counterpart. Admins see fleet-wide info via
``/api/v1/admin/sandboxes/*`` (``require_org_admin``). Reuse goes one layer
down (manager / repository), never at the route layer.
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.ws_sandbox import MySandboxOut
from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import current_active_user, require_member
from cubeplex.db import get_session
from cubeplex.models import Conversation, Role, Topic, User, UserSandbox
from cubeplex.sandbox.base import SandboxConflictError
from cubeplex.sandbox.manager import get_sandbox_manager
from cubeplex.sandbox.scope import get_workspace_sandbox_mode

router = APIRouter(prefix="/ws/{workspace_id}/sandboxes", tags=["ws-sandboxes"])


@router.get("", response_model=list[MySandboxOut])
async def list_my_sandboxes(
    ctx: Annotated[RequestContext, Depends(require_member)],
    actor: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[MySandboxOut]:
    """List sandbox entities visible to the caller in this workspace.

    In ``workspace`` shared-sandbox mode, lists the workspace-shared sandbox
    to all workspace members with ``can_manage=(ctx.role == Role.ADMIN)``.
    In ``per_user`` mode, lists live entities owned by ``actor.id``.
    """
    ws_mode = await get_workspace_sandbox_mode(session, ctx.workspace_id)
    if ws_mode == "workspace":
        stmt = (
            select(UserSandbox)
            .where(UserSandbox.org_id == ctx.org_id)  # type: ignore[arg-type]
            .where(UserSandbox.workspace_id == ctx.workspace_id)  # type: ignore[arg-type]
            .where(UserSandbox.scope_type == "workspace")  # type: ignore[arg-type]
            .where(UserSandbox.deleted_at.is_(None))  # type: ignore[union-attr]
            .order_by(desc(UserSandbox.last_activity_at))  # type: ignore[arg-type]
        )
        can_manage = ctx.role == Role.ADMIN
    else:
        stmt = (
            select(UserSandbox)
            .where(UserSandbox.org_id == ctx.org_id)  # type: ignore[arg-type]
            .where(UserSandbox.workspace_id == ctx.workspace_id)  # type: ignore[arg-type]
            .where(UserSandbox.user_id == actor.id)  # type: ignore[arg-type]
            .where(UserSandbox.deleted_at.is_(None))  # type: ignore[union-attr]
            .order_by(desc(UserSandbox.last_activity_at))  # type: ignore[arg-type]
        )
        can_manage = True
    rows = list((await session.execute(stmt)).scalars().all())

    # Batch-resolve scope_title (avoid N+1). user-scope rows get None; a
    # deleted conversation/topic also yields None — frontend renders i18n
    # "(deleted)" so no cross-scope existence is leaked.
    conv_ids = [r.scope_id for r in rows if r.scope_type == "conversation"]
    topic_ids = [r.scope_id for r in rows if r.scope_type == "topic"]
    conv_titles: dict[str, str] = {}
    topic_titles: dict[str, str] = {}
    if conv_ids:
        conv_rows = (
            await session.execute(
                select(Conversation.id, Conversation.title).where(  # type: ignore[call-overload]
                    Conversation.id.in_(conv_ids)  # type: ignore[attr-defined]
                )
            )
        ).all()
        conv_titles = {cast(str, r[0]): cast(str, r[1]) for r in conv_rows}
    if topic_ids:
        topic_rows = (
            await session.execute(
                select(Topic.id, Topic.title).where(  # type: ignore[call-overload]
                    Topic.id.in_(topic_ids)  # type: ignore[attr-defined]
                )
            )
        ).all()
        topic_titles = {cast(str, r[0]): cast(str, r[1]) for r in topic_rows}

    def title_for(r: UserSandbox) -> str | None:
        if r.scope_type == "conversation":
            return conv_titles.get(r.scope_id)
        if r.scope_type == "topic":
            return topic_titles.get(r.scope_id)
        return None

    return [
        MySandboxOut(
            id=r.id,
            scope_type=r.scope_type,
            scope_id=r.scope_id,
            scope_title=title_for(r),
            status=r.status,
            cleanup_action=r.cleanup_action,
            cleanup_requested_at=r.cleanup_requested_at,
            image=r.image,
            last_activity_at=r.last_activity_at,
            created_at=r.created_at,
            can_manage=can_manage,
        )
        for r in rows
    ]


@router.post("/{user_sandbox_id}/restart", status_code=status.HTTP_202_ACCEPTED)
async def restart_my_sandbox(
    user_sandbox_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
    actor: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Request a restart; provider cleanup may still be pending on return."""
    await _verify_ownership(ctx, actor, user_sandbox_id, session)
    manager = get_sandbox_manager()
    try:
        await manager.restart_user_sandbox(user_sandbox_id)
    except SandboxConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc) or "sandbox is provisioning; retry shortly",
        ) from exc


@router.delete("/{user_sandbox_id}", status_code=status.HTTP_202_ACCEPTED)
async def delete_my_sandbox(
    user_sandbox_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
    actor: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Request deletion; hide the row only after provider cleanup is confirmed."""
    await _verify_ownership(ctx, actor, user_sandbox_id, session)
    manager = get_sandbox_manager()
    await manager.delete_user_sandbox(user_sandbox_id)


async def _verify_ownership(
    ctx: RequestContext,
    actor: User,
    user_sandbox_id: str,
    session: AsyncSession,
) -> UserSandbox:
    """Verify sandbox lifecycle mutation permissions.

    - Missing / soft-deleted / wrong workspace -> 404.
    - ``scope_type == 'workspace'`` -> requires workspace admin (403 otherwise).
    - Personal / conversation / topic scope -> requires ``row.user_id == actor.id``
      (404 otherwise so cross-user existence is never leaked).
    """
    row = (
        await session.execute(
            select(UserSandbox)
            .where(UserSandbox.id == user_sandbox_id)  # type: ignore[arg-type]
            .where(UserSandbox.org_id == ctx.org_id)  # type: ignore[arg-type]
            .where(UserSandbox.workspace_id == ctx.workspace_id)  # type: ignore[arg-type]
            .where(UserSandbox.deleted_at.is_(None))  # type: ignore[union-attr]
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sandbox not found")
    if row.scope_type == "workspace":
        if ctx.role != Role.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="workspace admin required",
            )
        return row
    if row.user_id != actor.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sandbox not found")
    return row
