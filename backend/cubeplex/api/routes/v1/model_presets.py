"""Workspace endpoint exposing available model presets to chat composer."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.model_presets import (
    WorkspacePresetsResponse,
    WorkspacePresetSummary,
)
from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import require_member
from cubeplex.db import get_session
from cubeplex.llm.preset_details import detail_fields
from cubeplex.llm.snapshot import load_llm_snapshot

router = APIRouter(
    prefix="/ws/{workspace_id}/model-presets",
    tags=["workspace-model-presets"],
)


@router.get("")
async def get_workspace_model_presets(
    raw_request: Request,
    *,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WorkspacePresetsResponse:
    snap = await load_llm_snapshot(
        session,
        ctx.org_id,
        raw_request.app.state.encryption_backend,
    )
    presets: list[WorkspacePresetSummary] = []
    for p in snap.model_presets:
        d = detail_fields(snap.providers, p.primary)
        presets.append(
            WorkspacePresetSummary(
                key=p.key,
                kind=p.kind,
                primary=p.primary,
                description=p.description,
                is_default=p.is_default,
                provider_slug=d.provider_slug,
                model_id=d.model_id,
                model_display_name=d.model_display_name,
                context_window=d.context_window,
                reasoning=d.reasoning,
                input_modalities=d.input_modalities,
            )
        )
    return WorkspacePresetsResponse(presets=presets)
