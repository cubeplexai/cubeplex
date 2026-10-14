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
from cubeplex.llm.controls import chain_controls
from cubeplex.llm.preset_details import detail_fields
from cubeplex.llm.resolver import parse_model_ref
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
        controls = chain_controls(snap, p)
        pending = False
        for ref in p.chain:
            slug, model_id = parse_model_ref(ref)
            provider = snap.providers.get(slug)
            if provider:
                pending = pending or any(
                    model.verification_pending for model in provider.models if model.id == model_id
                )
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
                input_modalities=list(controls.input_modalities),
                reasoning_modes=list(controls.modes),
                reasoning_efforts=list(controls.efforts),
                verification_pending=pending,
            )
        )
    return WorkspacePresetsResponse(presets=presets)
