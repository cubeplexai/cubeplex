"""System routes: /system/info (public) and /system/setup (auth, single_tenant)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.system import SystemInfoResponse
from cubebox.db import get_session
from cubebox.models import Organization

router = APIRouter(prefix="/system", tags=["system"])

# v1 hardcoded; bump on release. Kept in sync with backend/pyproject.toml.
_CUBEBOX_VERSION = "0.1.0"


@router.get("/info", response_model=SystemInfoResponse)
async def get_system_info(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SystemInfoResponse:
    mode = getattr(request.app.state, "deployment_mode", "single_tenant")
    org_count = (await session.execute(select(func.count()).select_from(Organization))).scalar_one()
    needs_setup = mode == "single_tenant" and int(org_count) == 0
    return SystemInfoResponse(
        deployment_mode=mode,  # type: ignore[arg-type]
        version=_CUBEBOX_VERSION,
        needs_org_setup=needs_setup,
    )
