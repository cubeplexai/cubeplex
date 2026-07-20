"""Admin trace viewer routes. Gated by require_org_admin.

See docs/dev/specs/2026-06-11-admin-trace-viewer-design.md.

Tempo errors are logged with full body server-side and surfaced to the
admin client as a constant 502 message, so internal hostnames / Tempo
parse errors / query strings never reach the frontend.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.trace import (
    FilterOption,
    FilterOptionKind,
    FilterOptionsResponse,
    SpanNode,
    TagValuesResponse,
    TraceDetail,
    TraceListResponse,
)
from cubeplex.auth.dependencies import require_org_admin, resolve_current_org_id
from cubeplex.db import get_session
from cubeplex.models import Conversation, Membership, User, Workspace
from cubeplex.services.tempo_client import (
    TempoClient,
    TempoQueryError,
    TempoQueryValueError,
    TempoTraceNotFoundError,
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
    for label, dt in (("start", start), ("end", end)):
        if dt is not None and dt.tzinfo is None:
            raise HTTPException(
                status_code=400,
                detail=f"{label} must be a timezone-aware ISO 8601 timestamp",
            )
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


def _escape_like(value: str) -> str:
    """Escape LIKE/ILIKE wildcards so a prefix search treats them literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Registered before /{trace_id} (same reason as /tag-values above) so the
# literal "filter-options" path isn't captured as a trace_id.
@router.get("/filter-options", response_model=FilterOptionsResponse)
async def get_filter_options(
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    kind: FilterOptionKind = Query(..., description="Entity kind to list."),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=50),
) -> FilterOptionsResponse:
    """Read-only dropdown options for the traces filter bar.

    Postgres-backed (not Tempo): workspace/user/conversation names live in the
    app DB, keyed by the IDs the trace spans carry. Org-scoped from the session
    - never trusts a client-supplied org. Has no Tempo dependency, so it works
    even when the trace viewer's Tempo backend is unset.
    """
    org_id = await resolve_current_org_id(user, session)
    pattern = f"{_escape_like(q)}%" if q else None

    if kind is FilterOptionKind.WORKSPACE:
        # Coarse / low-cardinality: return all in the org (cap 200) so the
        # combobox can filter client-side without a prefix round-trip.
        stmt = select(cast(Any, Workspace.id), cast(Any, Workspace.name)).where(
            cast(Any, Workspace.org_id) == org_id
        )
        if pattern is not None:
            stmt = stmt.where(cast(Any, Workspace.name).ilike(pattern, escape="\\"))
        stmt = stmt.order_by(cast(Any, Workspace.name)).limit(200)
    elif kind is FilterOptionKind.CONVERSATION:
        # High-cardinality: server-side prefix typeahead, never materialize all.
        stmt = select(cast(Any, Conversation.id), cast(Any, Conversation.title)).where(
            cast(Any, Conversation.org_id) == org_id,
            cast(Any, Conversation.deleted_at).is_(None),
        )
        if pattern is not None:
            stmt = stmt.where(cast(Any, Conversation.title).ilike(pattern, escape="\\"))
        stmt = stmt.order_by(cast(Any, Conversation.title)).limit(limit)
    else:  # FilterOptionKind.USER - users are org-scoped only via membership.
        label = func.coalesce(cast(Any, User.display_name), cast(Any, User.email)).label("label")
        stmt = (
            select(cast(Any, User.id), label)
            .join(Membership, cast(Any, Membership.user_id) == User.id)
            .join(Workspace, cast(Any, Membership.workspace_id) == Workspace.id)
            .where(cast(Any, Workspace.org_id) == org_id)
        )
        if pattern is not None:
            stmt = stmt.where(
                or_(
                    cast(Any, User.display_name).ilike(pattern, escape="\\"),
                    cast(Any, User.email).ilike(pattern, escape="\\"),
                )
            )
        stmt = stmt.distinct().order_by(label).limit(limit)

    rows = (await session.execute(stmt)).all()
    options = [FilterOption(id=str(row[0]), name=str(row[1])) for row in rows]
    return FilterOptionsResponse(options=options)


def _has_foreign_org_span(node: SpanNode, expected_org_id: str) -> bool:
    span_org = node.raw_attributes.get("cubepi.metadata.org_id")
    if span_org is not None and str(span_org) != expected_org_id:
        return True
    return any(_has_foreign_org_span(c, expected_org_id) for c in node.children)


@router.get("/{trace_id}", response_model=TraceDetail)
async def get_trace_detail(
    trace_id: str,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TraceDetail:
    client = await _client_or_503()
    org_id = await resolve_current_org_id(user, session)
    try:
        detail = await client.get_trace(trace_id)
    except TempoQueryValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TempoTraceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Trace not found") from exc
    except TempoQueryError as exc:
        raise _bad_upstream(exc) from exc

    # Defence in depth: TraceQL is the primary gate, but a stray trace
    # without an org_id, or one belonging to another org, must never reach
    # the caller. Walk every span to catch mixed traces where a child span
    # carries a different org_id.
    if detail.summary.org_id is None or detail.summary.org_id != org_id:
        raise HTTPException(status_code=404, detail="Trace not found")
    if _has_foreign_org_span(detail.root, org_id):
        raise HTTPException(status_code=404, detail="Trace not found")
    return detail
