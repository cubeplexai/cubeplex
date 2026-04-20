"""FastAPI dependencies for auth + scoping."""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.context import RequestContext
from cubebox.auth.users import fastapi_users
from cubebox.db import get_session
from cubebox.models import Role, User
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

    return RequestContext(
        user=user, org_id=workspace.org_id, workspace_id=workspace_id, role=role
    )


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
