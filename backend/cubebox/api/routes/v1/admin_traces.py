"""Admin trace viewer routes. Gated by require_org_admin.

See docs/dev/specs/2026-06-11-admin-trace-viewer-design.md.

Tempo errors are logged with full body server-side and surfaced to the
admin client as a constant 502 message, so internal hostnames / Tempo
parse errors / query strings never reach the frontend.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.trace import (
    TagValuesResponse,
    TraceListResponse,
)
from cubebox.auth.dependencies import require_org_admin, resolve_current_org_id
from cubebox.db import get_session
from cubebox.models import User
from cubebox.services.tempo_client import (
    TempoClient,
    TempoQueryError,
    TempoQueryValueError,
    get_tempo_client,
)

router = APIRouter(prefix="/admin/traces", tags=["admin-traces"])

_ALLOWED_TAGS = frozenset(
    {
        "cubepi.metadata.workspace_id",
        "cubepi.metadata.user_id",
        "cubepi.metadata.conversation_id",
        "gen_ai.request.model",
    }
)


async def _client_or_503() -> TempoClient:
    client = get_tempo_client()
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="Admin trace viewer is not configured for this deployment.",
        )
    return client


def _bad_upstream(exc: Exception) -> HTTPException:
    logger.warning("Tempo upstream error: {}", exc)
    return HTTPException(status_code=502, detail="Upstream trace store error")


# Order matters: /tag-values is registered before /{trace_id} so FastAPI
# matches the literal path first instead of treating "tag-values" as a
# trace_id. (Task 11 adds the /{trace_id} route below this one.)
@router.get("", response_model=TraceListResponse)
async def list_traces(
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    workspace_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    conversation_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    model: str | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    min_duration_ms: int | None = Query(default=None, ge=0),
    max_duration_ms: int | None = Query(default=None, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> TraceListResponse:
    if start and end and start >= end:
        raise HTTPException(status_code=400, detail="start must be earlier than end")
    client = await _client_or_503()
    org_id = await resolve_current_org_id(user, session)
    try:
        traces = await client.search(
            org_id=org_id,
            workspace_id=workspace_id,
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
            model=model,
            start=start,
            end=end,
            min_duration_ms=min_duration_ms,
            max_duration_ms=max_duration_ms,
            limit=limit,
        )
    except TempoQueryValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TempoQueryError as exc:
        raise _bad_upstream(exc) from exc
    return TraceListResponse(traces=traces)


@router.get("/tag-values", response_model=TagValuesResponse)
async def get_tag_values(
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    tag: str = Query(..., description="Tag name; must be in the allow list."),
) -> TagValuesResponse:
    if tag not in _ALLOWED_TAGS:
        raise HTTPException(status_code=400, detail=f"Tag '{tag}' not allowed")
    client = await _client_or_503()
    org_id = await resolve_current_org_id(user, session)
    try:
        values = await client.tag_values(tag=tag, org_id=org_id)
    except TempoQueryError as exc:
        raise _bad_upstream(exc) from exc
    return TagValuesResponse(values=values)
