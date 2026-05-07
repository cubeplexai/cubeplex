"""Admin routes: /admin/me + (future) plugin extension manifest mount.

The /admin/me endpoint intentionally returns 200 with is_admin=true|false
(NOT 403) — the frontend uses it to decide whether to render the admin
entry point in the sidebar popover. Only the routing gate for
/admin/* proper (require_org_admin) returns 403.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.routes.v1.cost import router as cost_router
from cubebox.auth.dependencies import current_active_user, resolve_current_org_id
from cubebox.db import get_session
from cubebox.models import User
from cubebox.repositories import OrganizationMembershipRepository, OrganizationRepository

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminMeResponse(BaseModel):
    is_admin: bool
    org_id: str
    org_name: str


@router.get("/me", response_model=AdminMeResponse)
async def get_admin_me(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminMeResponse:
    org_id = await resolve_current_org_id(user, session)
    is_admin = await OrganizationMembershipRepository(session).is_admin(
        user_id=user.id, org_id=org_id
    )
    org = await OrganizationRepository(session).get(org_id)
    return AdminMeResponse(
        is_admin=is_admin,
        org_id=org_id,
        org_name=org.name if org else "",
    )


router.include_router(cost_router)
