"""FastAPI dependencies for auth + scoping."""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.context import RequestContext
from cubebox.auth.users import fastapi_users
from cubebox.db import get_session
from cubebox.models import Role, User
from cubebox.repositories import MembershipRepository, WorkspaceRepository

current_active_user = fastapi_users.current_user(active=True)


async def request_context(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_workspace_id: Annotated[str | None, Header(alias="X-Workspace-Id")] = None,
) -> RequestContext:
    """Resolve the active workspace + role from header + membership lookup."""
    if not x_workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Workspace-Id header is required",
        )

    ws_repo = WorkspaceRepository(session)
    workspace = await ws_repo.get(x_workspace_id)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace '{x_workspace_id}' not found",
        )

    mem_repo = MembershipRepository(session)
    role = await mem_repo.get_role(user_id=user.id, workspace_id=x_workspace_id)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this workspace",
        )

    return RequestContext(
        user=user, org_id=workspace.org_id, workspace_id=x_workspace_id, role=role
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
