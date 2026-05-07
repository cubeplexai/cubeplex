"""System routes: /system/info (public) and /system/setup (auth, single_tenant)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.system import SetupRequest, SetupResponse, SystemInfoResponse
from cubebox.auth.dependencies import current_active_user
from cubebox.auth.singleton_org import acquire_setup_lock, org_count
from cubebox.db import get_session
from cubebox.models import Organization, OrgRole, Role, User
from cubebox.models.agent_config import AgentConfig
from cubebox.repositories import (
    MembershipRepository,
    OrganizationMembershipRepository,
    OrganizationRepository,
    WorkspaceRepository,
)

router = APIRouter(prefix="/system", tags=["system"])

# v1 hardcoded; bump on release. Kept in sync with backend/pyproject.toml.
_CUBEBOX_VERSION = "0.1.0"


@router.get("/info", response_model=SystemInfoResponse)
async def get_system_info(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SystemInfoResponse:
    mode = getattr(request.app.state, "deployment_mode", "single_tenant")
    count = (await session.execute(select(func.count()).select_from(Organization))).scalar_one()
    needs_setup = mode == "single_tenant" and int(count) == 0
    return SystemInfoResponse(
        deployment_mode=mode,  # type: ignore[arg-type]
        version=_CUBEBOX_VERSION,
        needs_org_setup=needs_setup,
    )


@router.post("/setup", response_model=SetupResponse, status_code=201)
async def post_setup(
    request: Request,
    body: SetupRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SetupResponse:
    mode = getattr(request.app.state, "deployment_mode", "single_tenant")
    if mode != "single_tenant":
        raise HTTPException(status_code=404, detail="mode_disallows_setup")

    locked = await acquire_setup_lock(session)
    if not locked:
        raise HTTPException(status_code=409, detail="setup_in_progress")

    if await org_count(session) > 0:
        raise HTTPException(status_code=409, detail="setup_already_completed")

    try:
        org = await OrganizationRepository(session).create(name=body.org_name, slug=body.slug)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="slug_taken") from exc

    await OrganizationMembershipRepository(session).grant(
        user_id=user.id, org_id=org.id, role=OrgRole.OWNER
    )
    ws = await WorkspaceRepository(session).create(org_id=org.id, name="Personal")
    await MembershipRepository(session).grant(user_id=user.id, workspace_id=ws.id, role=Role.ADMIN)
    session.add(AgentConfig(org_id=org.id, workspace_id=ws.id))
    await session.commit()

    try:
        from cubebox.auth.users import _install_preinstalled_skills

        await _install_preinstalled_skills(session, org_id=org.id, user_id=user.id)
    except Exception:
        pass

    return SetupResponse(org_id=org.id, workspace_id=ws.id)
