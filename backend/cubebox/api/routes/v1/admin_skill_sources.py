"""Org-admin management of remote skill sources (/admin/skill-sources)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.context import RequestContext
from cubebox.db import get_session
from cubebox.mcp.dependencies import get_admin_request_context
from cubebox.models import SkillSource
from cubebox.repositories.skill_source import SkillSourceRepository

router = APIRouter(prefix="/admin/skill-sources", tags=["admin-skill-sources"])

_TRUST_TIERS = {"official", "community", "untrusted"}


class CreateSkillSourceRequest(BaseModel):
    name: str
    base_url: str
    repo: str | None = None
    trust_tier: str = "untrusted"


class PatchSkillSourceRequest(BaseModel):
    enabled: bool | None = None
    trust_tier: str | None = None


class SkillSourceResponse(BaseModel):
    id: str
    name: str
    kind: str
    base_url: str
    repo: str | None
    trust_tier: str
    enabled: bool


def _to_response(row: SkillSource) -> SkillSourceResponse:
    return SkillSourceResponse(
        id=row.id,
        name=row.name,
        kind=row.kind,
        base_url=row.base_url,
        repo=row.repo,
        trust_tier=row.trust_tier,
        enabled=row.enabled,
    )


@router.post("", status_code=201, response_model=SkillSourceResponse)
async def create_source(
    body: CreateSkillSourceRequest,
    *,
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillSourceResponse:
    if body.trust_tier not in _TRUST_TIERS:
        raise HTTPException(status_code=400, detail="BAD_TRUST_TIER")
    row = await SkillSourceRepository(session).create(
        org_id=ctx.org_id,
        name=body.name,
        base_url=body.base_url,
        repo=body.repo,
        trust_tier=body.trust_tier,
        created_by_user_id=ctx.user.id,
    )
    return _to_response(row)


@router.get("", response_model=list[SkillSourceResponse])
async def list_sources(
    *,
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[SkillSourceResponse]:
    rows = await SkillSourceRepository(session).list_for_org(ctx.org_id)
    return [_to_response(r) for r in rows]


@router.patch("/{source_id}", response_model=SkillSourceResponse)
async def patch_source(
    source_id: str,
    body: PatchSkillSourceRequest,
    *,
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillSourceResponse:
    repo = SkillSourceRepository(session)
    if body.enabled is not None:
        if not await repo.set_enabled(ctx.org_id, source_id, body.enabled):
            raise HTTPException(status_code=404, detail="SOURCE_NOT_FOUND")
    if body.trust_tier is not None:
        if body.trust_tier not in _TRUST_TIERS:
            raise HTTPException(status_code=400, detail="BAD_TRUST_TIER")
        if not await repo.set_trust_tier(ctx.org_id, source_id, body.trust_tier):
            raise HTTPException(status_code=404, detail="SOURCE_NOT_FOUND")
    row = await repo.get(ctx.org_id, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="SOURCE_NOT_FOUND")
    return _to_response(row)
