"""Workspace-scope sandbox status read endpoint.

Surfaces the caller's UserSandbox row (if any) in the current workspace as a
small read-only payload for the workspace sandbox status page. Scope-isolated:
no admin counterpart — admins see fleet-wide info via a different surface.
"""

from typing import Annotated, cast

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.sandbox_policy import SandboxStatusOut, SandboxStatusValue
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.db.session import get_session
from cubebox.repositories.user_sandbox import UserSandboxRepository
from cubebox.utils.time import utc_isoformat

router = APIRouter(prefix="/ws/{workspace_id}/sandbox", tags=["ws-sandbox"])


@router.get("/status", response_model=SandboxStatusOut)
async def get_sandbox_status(
    workspace_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> SandboxStatusOut:
    """Return the caller's active sandbox row in this workspace, or absent."""
    repo = UserSandboxRepository(session, org_id=ctx.org_id, workspace_id=workspace_id)
    row = await repo.get_active_by_user(ctx.user.id)
    if row is None:
        return SandboxStatusOut(
            status="absent",
            default_image=None,
            last_activity_at=None,
            browser_url=None,
        )
    # ``row.status`` is a free-form str on the model; the runtime constraint is
    # enforced by the existing UserSandboxRepository. Cast for the response type.
    return SandboxStatusOut(
        status=cast(SandboxStatusValue, row.status),
        default_image=row.image,
        last_activity_at=utc_isoformat(row.last_activity_at),
        browser_url=None,
    )
