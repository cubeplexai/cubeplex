"""Admin endpoints for managing OrgSettings.model_presets."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.model_presets import AdminModelPresetsBody
from cubeplex.auth.dependencies import require_org_admin, resolve_unambiguous_admin_org_id
from cubeplex.db import get_session
from cubeplex.llm.snapshot import load_llm_snapshot
from cubeplex.models import User
from cubeplex.services.model_presets import read_org_presets, write_org_presets

router = APIRouter(prefix="/admin/model-presets", tags=["admin-model-presets"])


class AdminModelPresetsResponse(BaseModel):
    value: AdminModelPresetsBody | None
    origin: Literal["org", "system", "none"]


@router.get("")
async def get_admin_model_presets(
    *,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminModelPresetsResponse:
    org_id = await resolve_unambiguous_admin_org_id(user, session)
    value, origin = await read_org_presets(session, org_id)
    return AdminModelPresetsResponse(value=value, origin=origin)


@router.put("")
async def put_admin_model_presets(
    raw_request: Request,
    body: AdminModelPresetsBody,
    *,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminModelPresetsResponse:
    org_id = await resolve_unambiguous_admin_org_id(user, session)
    snap = await load_llm_snapshot(
        session,
        org_id,
        raw_request.app.state.encryption_backend,
    )
    available_models: set[str] = {
        f"{slug}/{m.id}" for slug, cfg in snap.providers.items() for m in cfg.models
    }
    await write_org_presets(session, org_id, body, available_models=available_models)
    await session.commit()
    value, origin = await read_org_presets(session, org_id)
    return AdminModelPresetsResponse(value=value, origin=origin)
