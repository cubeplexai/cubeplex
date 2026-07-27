"""Admin cost/billing endpoints. All routes require org-admin access."""

import csv
import io
from collections.abc import AsyncGenerator, Sequence
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.billing import (
    CostAggregateRow,
    CostSummaryResponse,
    TimeseriesPoint,
    TimeseriesResponse,
    TimeseriesSeries,
)
from cubeplex.auth.dependencies import current_active_user, resolve_current_org_id
from cubeplex.db import get_session
from cubeplex.models import User, Workspace
from cubeplex.repositories import (
    BillingRepository,
    OrganizationMembershipRepository,
    WorkspaceRepository,
)

router = APIRouter(prefix="/cost", tags=["cost"])

_EXPORT_FIELDS: list[str] = [
    "started_at",
    "workspace_id",
    "user_id",
    "conversation_id",
    "provider",
    "model_id",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cost_amount",
    "currency",
    "status",
    "subagent_depth",
    "duration_ms",
]


async def _require_org_admin(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> tuple[User, str]:
    """Returns (user, org_id) after verifying org-admin access."""
    org_id = await resolve_current_org_id(user, session)
    is_admin = await OrganizationMembershipRepository(session).is_admin(
        user_id=user.id, org_id=org_id
    )
    if not is_admin:
        raise HTTPException(status_code=403, detail="org admin required")
    return user, org_id


def _parse_date_range(
    from_date: str | None,
    to_date: str | None,
) -> tuple[datetime, datetime]:
    today = date.today()
    try:
        since_d = (
            date(today.year, today.month, 1) if from_date is None else date.fromisoformat(from_date)
        )
        until_d = today if to_date is None else date.fromisoformat(to_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date: {exc}") from exc
    since = datetime(since_d.year, since_d.month, since_d.day, tzinfo=UTC)
    until = datetime(until_d.year, until_d.month, until_d.day, 23, 59, 59, 999999, tzinfo=UTC)
    return since, until


def _user_label(*, display_name: str | None, email: str) -> str:
    name = (display_name or "").strip()
    return name if name else email


async def _bucket_labels(
    session: AsyncSession,
    *,
    dimension: Literal["workspace", "user"],
    buckets: Sequence[str],
) -> dict[str, str]:
    """Resolve public IDs to display labels. Missing entities are omitted."""
    ids = [b for b in buckets if b and b != "__other"]
    if not ids:
        return {}
    if dimension == "workspace":
        ws_stmt = select(Workspace).where(Workspace.id.in_(ids))  # type: ignore[attr-defined]
        workspaces = list((await session.execute(ws_stmt)).scalars().all())
        return {ws.id: ws.name for ws in workspaces}
    user_stmt = select(User).where(User.id.in_(ids))  # type: ignore[attr-defined]
    users = list((await session.execute(user_stmt)).scalars().all())
    return {u.id: _user_label(display_name=u.display_name, email=u.email) for u in users}


def _with_labels(
    rows: list[dict[str, Any]],
    labels: dict[str, str],
) -> list[CostAggregateRow]:
    return [
        CostAggregateRow(
            **row,
            bucket_label=labels.get(str(row["bucket"])),
        )
        for row in rows
    ]


@router.get("/summary", response_model=CostSummaryResponse)
async def get_cost_summary(
    session: Annotated[AsyncSession, Depends(get_session)],
    auth: Annotated[tuple[User, str], Depends(_require_org_admin)],
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
) -> CostSummaryResponse:
    _, org_id = auth
    since, until = _parse_date_range(from_date, to_date)
    repo = BillingRepository(session, org_id=org_id)

    # Each get_org_spend call reuses the same AsyncSession/connection.
    # Keep sequential — concurrent use on one session is a SQLAlchemy anti-pattern.
    by_workspace = await repo.get_org_spend(since=since, until=until, group_by="workspace")
    by_model = await repo.get_org_spend(since=since, until=until, group_by="model")
    by_user = await repo.get_org_spend(since=since, until=until, group_by="user")
    by_day = await repo.get_org_spend(since=since, until=until, group_by="day")

    currency = by_workspace[0]["currency"] if by_workspace else "USD"
    total_cost = sum(r["cost_amount_micro"] for r in by_workspace if r["currency"] == currency)
    total_calls = sum(r["call_count"] for r in by_workspace)

    ws_labels = await _bucket_labels(
        session,
        dimension="workspace",
        buckets=[str(r["bucket"]) for r in by_workspace],
    )
    user_labels = await _bucket_labels(
        session,
        dimension="user",
        buckets=[str(r["bucket"]) for r in by_user],
    )

    return CostSummaryResponse(
        from_date=since.date(),
        to_date=until.date(),
        total_cost_amount_micro=total_cost,
        currency=currency,
        total_calls=total_calls,
        by_workspace=_with_labels(by_workspace, ws_labels),
        by_model=[CostAggregateRow(**r) for r in by_model],
        by_user=_with_labels(by_user, user_labels),
        by_day=[CostAggregateRow(**r) for r in by_day],
    )


@router.get("/timeseries", response_model=TimeseriesResponse)
async def get_cost_timeseries(
    session: Annotated[AsyncSession, Depends(get_session)],
    auth: Annotated[tuple[User, str], Depends(_require_org_admin)],
    dimension: Literal["workspace", "model", "user"] = Query(default="workspace"),
    granularity: Literal["day", "week"] = Query(default="day"),
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
    workspace_ids: str | None = Query(default=None, description="comma-separated"),
    models: str | None = Query(default=None, description="comma-separated provider/model"),
    rank_by: Literal["cost", "tokens"] = Query(
        default="cost",
        description="Rank series for top-N / __other collapse: cost or tokens",
    ),
) -> TimeseriesResponse:
    _, org_id = auth
    since, until = _parse_date_range(from_date, to_date)
    repo = BillingRepository(session, org_id=org_id)
    ws_filter = [s for s in (workspace_ids or "").split(",") if s] or None
    model_filter = [s for s in (models or "").split(",") if s] or None
    series_raw = await repo.get_timeseries(
        dimension=dimension,
        since=since,
        until=until,
        granularity=granularity,
        workspace_ids=ws_filter,
        models=model_filter,
        rank_by=rank_by,
    )
    currency = series_raw[0]["currency"] if series_raw else "USD"
    labels: dict[str, str] = {}
    if dimension in ("workspace", "user"):
        labels = await _bucket_labels(
            session,
            dimension=dimension,
            buckets=[str(s["bucket"]) for s in series_raw],
        )
    return TimeseriesResponse(
        from_date=since.date(),
        to_date=until.date(),
        granularity=granularity,
        dimension=dimension,
        currency=currency,
        series=[
            TimeseriesSeries(
                bucket=s["bucket"],
                bucket_label=labels.get(str(s["bucket"])),
                currency=s["currency"],
                points=[TimeseriesPoint(**p) for p in s["points"]],
            )
            for s in series_raw
        ],
    )


@router.get("/by-workspace/{ws_id}", response_model=list[CostAggregateRow])
async def get_workspace_cost(
    ws_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    auth: Annotated[tuple[User, str], Depends(_require_org_admin)],
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
    group_by: str = Query(default="day"),
) -> list[CostAggregateRow]:
    _, org_id = auth
    # Verify workspace belongs to this org
    ws_repo = WorkspaceRepository(session)
    ws = await ws_repo.get(ws_id)
    if ws is None or ws.org_id != org_id:
        raise HTTPException(status_code=404, detail="Workspace not found")
    since, until = _parse_date_range(from_date, to_date)
    repo = BillingRepository(session, org_id=org_id)
    rows = await repo.get_workspace_spend(
        workspace_id=ws_id,
        since=since,
        until=until,
        group_by=group_by,  # type: ignore[arg-type]
    )
    return [CostAggregateRow(**r) for r in rows]


@router.get("/export.csv")
async def export_org_csv(
    session: Annotated[AsyncSession, Depends(get_session)],
    auth: Annotated[tuple[User, str], Depends(_require_org_admin)],
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
) -> StreamingResponse:
    _, org_id = auth
    since, until = _parse_date_range(from_date, to_date)
    repo = BillingRepository(session, org_id=org_id)

    async def _generate() -> AsyncGenerator[str, None]:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_EXPORT_FIELDS)
        yield buf.getvalue()
        async for row in repo.stream_events_for_export(since=since, until=until):
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([row[k] for k in _EXPORT_FIELDS])
            yield buf.getvalue()

    filename = f"cost_{since.strftime('%Y-%m')}_{org_id[:8]}.csv"
    return StreamingResponse(
        _generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/by-workspace/{ws_id}/export.csv")
async def export_workspace_csv(
    ws_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    auth: Annotated[tuple[User, str], Depends(_require_org_admin)],
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
) -> StreamingResponse:
    _, org_id = auth
    # Verify workspace belongs to this org
    ws_repo = WorkspaceRepository(session)
    ws = await ws_repo.get(ws_id)
    if ws is None or ws.org_id != org_id:
        raise HTTPException(status_code=404, detail="Workspace not found")
    since, until = _parse_date_range(from_date, to_date)
    repo = BillingRepository(session, org_id=org_id)

    async def _generate() -> AsyncGenerator[str, None]:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_EXPORT_FIELDS)
        yield buf.getvalue()
        async for row in repo.stream_events_for_export(
            since=since, until=until, workspace_id=ws_id
        ):
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([row[k] for k in _EXPORT_FIELDS])
            yield buf.getvalue()

    filename = f"cost_{since.strftime('%Y-%m')}_{ws_id[:8]}.csv"
    return StreamingResponse(
        _generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
