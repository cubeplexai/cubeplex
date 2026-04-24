"""FastAPI dependencies for auth + scoping."""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.context import RequestContext
from cubebox.auth.users import fastapi_users
from cubebox.db import get_session
from cubebox.models import Membership, Role, User, Workspace
from cubebox.repositories import MembershipRepository, WorkspaceRepository

current_active_user = fastapi_users.current_user(active=True)


async def request_context(
    workspace_id: Annotated[str, Path()],
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RequestContext:
    """Resolve the active workspace + role from URL path + membership lookup.

    Workspace scoping is encoded in the URL (`/api/v1/ws/{workspace_id}/...`);
    every business endpoint declares `workspace_id` as a path parameter and
    depends on this function. There is no header fallback — the path is the
    single source of truth.
    """
    ws_repo = WorkspaceRepository(session)
    workspace = await ws_repo.get(workspace_id)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace '{workspace_id}' not found",
        )

    mem_repo = MembershipRepository(session)
    role = await mem_repo.get_role(user_id=user.id, workspace_id=workspace_id)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this workspace",
        )

    return RequestContext(user=user, org_id=workspace.org_id, workspace_id=workspace_id, role=role)


def require_role(
    *allowed: Role,
) -> Callable[..., Awaitable[RequestContext]]:
    """Dependency factory: enforce that ctx.role is in `allowed`."""

    async def _check(
        ctx: Annotated[RequestContext, Depends(request_context)],
    ) -> RequestContext:
        if ctx.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role {ctx.role.value} is not allowed; "
                    f"need one of {[r.value for r in allowed]}"
                ),
            )
        return ctx

    return _check


require_admin = require_role(Role.ADMIN)
require_member = require_role(Role.ADMIN, Role.MEMBER)


async def resolve_current_org_id(user: User, session: AsyncSession) -> str:
    """Resolve the user's current org (v1: first workspace's org).

    v1 is single-org-per-user (register bootstrap creates one personal org).
    When multi-org ships, this reads a cookie-set current_org_id and validates
    that the user is a member of a workspace in that org.

    Raises 403 if the user has no workspace memberships at all — shouldn't
    happen for a registered user but guards against edge cases.
    """
    stmt = (
        select(Workspace)
        .join(Membership, Membership.workspace_id == Workspace.id)  # type: ignore[arg-type]
        .where(Membership.user_id == user.id)  # type: ignore[arg-type]
        .order_by(Workspace.created_at)  # type: ignore[arg-type]
        .limit(1)
    )
    result = await session.execute(stmt)
    workspace = result.scalars().first()
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No org membership found for user",
        )
    return workspace.org_id


async def require_org_admin(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """v1: user is "org admin" iff they hold ADMIN in any workspace of their org.

    Admin routes (`/admin/*`) are not workspace-scoped — this dependency
    resolves the user's current org from their membership graph directly.
    When an org-level role concept lands, this implementation is replaced;
    callers (admin routes, /admin/me endpoint) are unchanged.
    """
    org_id = await resolve_current_org_id(user, session)
    is_admin = await MembershipRepository(session).user_has_role_in_org(
        user_id=user.id, org_id=org_id, role=Role.ADMIN
    )
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Org admin role required",
        )
    return user
