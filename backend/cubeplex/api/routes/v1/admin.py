"""Admin routes: /admin/me + (future) plugin extension manifest mount.

The /admin/me endpoint intentionally returns 200 with is_admin=true|false
(NOT 403) — the frontend uses it to decide whether to render the admin
entry point in the sidebar popover. Only the routing gate for
/admin/* proper (require_org_admin) returns 403.
"""

import re
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.routes.v1.cost import router as cost_router
from cubeplex.auth.dependencies import (
    current_active_user,
    require_org_admin,
    resolve_current_org_id,
    resolve_unambiguous_admin_org_id,
)
from cubeplex.db import get_session
from cubeplex.models import User
from cubeplex.repositories import OrganizationMembershipRepository, OrganizationRepository

router = APIRouter(prefix="/admin", tags=["admin"])

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


class OrgUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=255)
    slug: str | None = Field(None, min_length=3, max_length=32)


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


@router.get("/org")
async def get_org(
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    org_id = await resolve_unambiguous_admin_org_id(user, session)
    org = await OrganizationRepository(session).get(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="org not found")
    return {"id": org.id, "name": org.name, "slug": org.slug}


@router.patch("/org")
async def update_org(
    body: Annotated[OrgUpdate, Body()],
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> dict[str, str]:
    if body.name is None and body.slug is None:
        raise HTTPException(status_code=400, detail="at least one field required")
    if body.slug is not None and not _SLUG_RE.match(body.slug):
        raise HTTPException(status_code=400, detail="slug_invalid_format")

    org_id = await resolve_unambiguous_admin_org_id(user, session)
    org = await OrganizationRepository(session).get(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="org not found")

    if body.slug is not None and body.slug != org.slug:
        from sqlalchemy import select

        from cubeplex.models import Organization

        existing = await session.execute(
            select(Organization).where(Organization.slug == body.slug)  # type: ignore[arg-type]
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail="slug_taken")
        org.slug = body.slug

    if body.name is not None:
        org.name = body.name

    session.add(org)
    await session.commit()
    await session.refresh(org)

    from cubeplex.plugins.audit import audit_log

    await audit_log(
        action="org.updated",
        user_id=user.id,
        org_id=org_id,
        ip=request.client.host if request.client else None,
    )
    return {"id": org.id, "name": org.name, "slug": org.slug}


router.include_router(cost_router)
