"""Admin sandbox observability routes.

Read-only admin surface. RBAC: require_org_admin. All routes are
org-scoped via the dep; no cross-org access.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.admin_sandbox import (
    SyncEventOut,
    UserSandboxSnapshotOut,
)
from cubeplex.auth.dependencies import require_org_admin, resolve_current_org_id
from cubeplex.db import get_session
from cubeplex.models import User, UserSandbox
from cubeplex.repositories.user_sandbox_sync_event import UserSandboxSyncEventRepository

router = APIRouter(prefix="/admin", tags=["admin-sandboxes"])


@router.get("/sandboxes", response_model=list[UserSandboxSnapshotOut])
async def list_sandboxes(
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[UserSandboxSnapshotOut]:
    org_id = await resolve_current_org_id(user, session)
    stmt = (
        select(UserSandbox)
        .where(UserSandbox.org_id == org_id)  # type: ignore[arg-type]
        .order_by(desc(UserSandbox.last_activity_at))  # type: ignore[arg-type]
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [UserSandboxSnapshotOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/sandboxes/{user_sandbox_id}", response_model=UserSandboxSnapshotOut)
async def get_sandbox(
    user_sandbox_id: str,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserSandboxSnapshotOut:
    org_id = await resolve_current_org_id(user, session)
    row = (
        await session.execute(
            select(UserSandbox)
            .where(UserSandbox.id == user_sandbox_id)  # type: ignore[arg-type]
            .where(UserSandbox.org_id == org_id)  # type: ignore[arg-type]
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return UserSandboxSnapshotOut.model_validate(row, from_attributes=True)


@router.get(
    "/sandboxes/{user_sandbox_id}/sync-events",
    response_model=list[SyncEventOut],
)
async def list_sandbox_events(
    user_sandbox_id: str,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[SyncEventOut]:
    org_id = await resolve_current_org_id(user, session)
    # Verify sandbox belongs to actor's org
    parent = (
        await session.execute(
            select(UserSandbox)
            .where(UserSandbox.id == user_sandbox_id)  # type: ignore[arg-type]
            .where(UserSandbox.org_id == org_id)  # type: ignore[arg-type]
        )
    ).scalar_one_or_none()
    if parent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    repo = UserSandboxSyncEventRepository(session, org_id=org_id, workspace_id=parent.workspace_id)
    events = await repo.list_for_sandbox(user_sandbox_id, limit=limit, offset=offset)
    return [SyncEventOut.model_validate(e, from_attributes=True) for e in events]


@router.get("/sandbox-sync-events", response_model=list[SyncEventOut])
async def list_sync_events_scoped(
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    workspace_id: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[SyncEventOut]:
    org_id = await resolve_current_org_id(user, session)
    # Use org-wide classmethod when no workspace_id filter is given,
    # otherwise scope to the specified workspace.
    events = await UserSandboxSyncEventRepository.list_for_org(
        session,
        org_id=org_id,
        workspace_id=workspace_id,
        status=status_filter,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return [SyncEventOut.model_validate(e, from_attributes=True) for e in events]
