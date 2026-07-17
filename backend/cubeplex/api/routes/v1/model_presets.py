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
    return WorkspacePresetsResponse(
        presets=[
            WorkspacePresetSummary(
                key=p.key,
                kind=p.kind,
                primary=p.primary,
                description=p.description,
                is_default=p.is_default,
            )
            for p in snap.model_presets
        ],
    )
