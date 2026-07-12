# Admin Insights Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `/admin/cost` as `/admin/insights`: a faceted explorer with KPI row, three stacked-area sections (workspace / model / user), and a cache-efficiency section. Backend gains `by_user` in `/admin/cost/summary` and a new `/admin/cost/timeseries` endpoint for 2D aggregation.

**Architecture:** Additive backend changes (no migrations, no breakages). New `recharts`-powered frontend page composed of small focused components under `components/admin/insights/`. The old `/admin/cost` route becomes a redirect stub.

**Tech Stack:** FastAPI + SQLAlchemy (backend), Next.js 16 / React 19 / TypeScript / Tailwind / shadcn/ui / Zustand (frontend), `recharts` (new dep), Playwright (E2E).

**Spec:** `docs/superpowers/specs/2026-05-12-admin-insights-redesign-design.md`

**Worktree:** `feat/admin-cost-redesign` at `/home/chris/cubeplex/.worktrees/feat/admin-cost-redesign`. Allocated ports `8027` / `3027`. All paths below are relative to the worktree root unless otherwise noted.

---

## File Map

### Backend

- **Modify** `backend/cubeplex/repositories/billing.py`
  - Add `get_timeseries(dimension, since, until, granularity, workspace_ids, models)` method that does 2D aggregation `(bucket × time_bucket)`.
- **Modify** `backend/cubeplex/api/schemas/billing.py`
  - Add `by_user` field to `CostSummaryResponse`.
  - Add `TimeseriesPoint`, `TimeseriesSeries`, `TimeseriesResponse`.
- **Modify** `backend/cubeplex/api/routes/v1/cost.py`
  - Add `by_user` to `/summary` route handler.
  - Add new `GET /timeseries` route handler.
- **Modify / create** `backend/tests/e2e/test_billing_cost_api.py` (extend existing or create alongside `test_billing.py`)
  - New cases for `by_user`, `/timeseries` happy paths + negatives.

### Frontend — core package

- **Modify** `frontend/packages/core/src/types/billing.ts`
  - Add `by_user` to `CostSummaryResponse`.
  - Add `TimeseriesPoint`, `TimeseriesSeries`, `TimeseriesResponse`.
- **Modify** `frontend/packages/core/src/api/billing.ts`
  - Add `fetchCostTimeseries(client, params)`.
- **Modify** `frontend/packages/core/src/api/index.ts`
  - Export `fetchCostTimeseries`.
- **Modify** `frontend/packages/core/src/types/index.ts`
  - Re-export new timeseries types.

### Frontend — web package

- **Modify** `frontend/packages/web/package.json` — add `recharts` dep.
- **Modify** `frontend/packages/web/messages/en.json` and `zh.json` — add `adminInsights` namespace.
- **Modify** `frontend/packages/web/components/admin/AdminSubNav.tsx` — relabel Cost → Insights, swap icon.
- **Create** `frontend/packages/web/app/admin/insights/page.tsx`.
- **Replace** `frontend/packages/web/app/admin/cost/page.tsx` — becomes a redirect stub to `/admin/insights`.
- **Create** `frontend/packages/web/hooks/useCostData.ts`.
- **Create** `frontend/packages/web/lib/cost/helpers.ts` — `computeCacheHitRate`, `topNWithOther`, `percentDelta`, `formatPercent`.
- **Create** `frontend/packages/web/lib/cost/helpers.test.ts` — Vitest unit tests for the pure helpers.
- **Create** components under `frontend/packages/web/components/admin/insights/`:
  - `InsightsShell.tsx`
  - `InsightsTopBar.tsx`
  - `InsightsFilterSidebar.tsx`
  - `cost/KpiRow.tsx`
  - `cost/StackedSection.tsx`
  - `cost/CacheSection.tsx`
  - `cost/StackedChart.tsx`
  - `cost/RateChart.tsx`
- **Rename** `frontend/packages/web/__tests__/e2e/admin-cost.spec.ts` → `admin-insights.spec.ts`, extend.

---

## Pre-flight

- [ ] **Step P1: Confirm worktree env**

```bash
cd /home/chris/cubeplex/.worktrees/feat/admin-cost-redesign
cat .worktree.env
git rev-parse --abbrev-ref HEAD
```

Expected: branch `feat/admin-cost-redesign`, `CUBEPLEX_API__PORT=8027`, `PORT=3027`. Never substitute 8000/3000 — they belong to the main worktree.

- [ ] **Step P2: Confirm dev deps are installed**

```bash
cd backend && make dev-install
cd ../frontend && pnpm install
```

- [ ] **Step P3: Confirm backend E2E env files are present**

```bash
ls /home/chris/cubeplex/.worktrees/feat/admin-cost-redesign/backend/.env \
   /home/chris/cubeplex/.worktrees/feat/admin-cost-redesign/backend/config.development.local.yaml
```

If either is missing, copy from main per the worktree CLAUDE.md.

---

## Backend

### Task 1: `BillingRepository.get_timeseries`

**Files:**
- Modify: `backend/cubeplex/repositories/billing.py`
- Test: `backend/tests/e2e/test_billing_cost_api.py` (new file)

- [ ] **Step 1.1: Write failing E2E that calls the repo directly**

Create `backend/tests/e2e/test_billing_cost_api.py`:

```python
"""E2E tests for the redesigned /admin/cost/* surface and timeseries repo."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.db.engine import _build_database_url
from cubeplex.models.billing import BillingEvent, LlmBillingEvent
from cubeplex.repositories import BillingRepository

pytestmark = pytest.mark.e2e


async def _direct_session() -> AsyncSession:
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return maker()


async def _seed_events(
    session: AsyncSession,
    *,
    org_id: str,
    rows: list[dict[str, object]],
) -> None:
    """Insert a list of (workspace_id, user_id, provider, model_id, started_at,
    cost_micro, input, output, cache_read, cache_write) tuples."""
    for r in rows:
        be = BillingEvent(
            org_id=org_id,
            workspace_id=r["workspace_id"],
            user_id=r["user_id"],
            conversation_id=r.get("conversation_id", "conv-test"),
            event_type="llm_call",
            cost_amount_micro=int(r["cost_micro"]),
            currency="USD",
            started_at=r["started_at"],
            ended_at=r["started_at"] + timedelta(milliseconds=200),
            duration_ms=200,
            status="ok",
        )
        le = LlmBillingEvent(
            billing_event_id=be.id,
            provider=r["provider"],
            model_id=r["model_id"],
            input_tokens=int(r.get("input", 0)),
            output_tokens=int(r.get("output", 0)),
            cache_read_tokens=int(r.get("cache_read", 0)),
            cache_write_tokens=int(r.get("cache_write", 0)),
        )
        session.add(be)
        session.add(le)
    await session.commit()


async def test_get_timeseries_workspace_two_workspaces_two_days() -> None:
    """Two workspaces × two days produces 2 series × 2 points each, zero-padded."""
    session = await _direct_session()
    try:
        org = "org-ts-1"
        day1 = datetime(2026, 5, 1, 12, tzinfo=UTC)
        day2 = datetime(2026, 5, 2, 12, tzinfo=UTC)
        await _seed_events(
            session,
            org_id=org,
            rows=[
                {"workspace_id": "ws-a", "user_id": "u1", "provider": "openai",
                 "model_id": "gpt-4o", "started_at": day1, "cost_micro": 1_000_000,
                 "input": 100, "output": 20},
                {"workspace_id": "ws-b", "user_id": "u2", "provider": "openai",
                 "model_id": "gpt-4o", "started_at": day1, "cost_micro": 500_000,
                 "input": 50, "output": 10},
                {"workspace_id": "ws-a", "user_id": "u1", "provider": "openai",
                 "model_id": "gpt-4o", "started_at": day2, "cost_micro": 2_000_000,
                 "input": 200, "output": 40},
            ],
        )
        repo = BillingRepository(session, org_id=org)
        result = await repo.get_timeseries(
            dimension="workspace",
            since=datetime(2026, 5, 1, tzinfo=UTC),
            until=datetime(2026, 5, 2, 23, 59, 59, tzinfo=UTC),
            granularity="day",
        )
        series_by_bucket = {s["bucket"]: s for s in result}
        assert set(series_by_bucket) == {"ws-a", "ws-b"}
        # ws-a has both days; ws-b only day1, but day2 zero-padded
        ws_b_points = {p["date"]: p for p in series_by_bucket["ws-b"]["points"]}
        assert ws_b_points["2026-05-02"]["cost_amount_micro"] == 0
        assert ws_b_points["2026-05-02"]["calls"] == 0
        assert ws_b_points["2026-05-01"]["cost_amount_micro"] == 500_000
    finally:
        await session.close()
```

- [ ] **Step 1.2: Run to verify failure**

```bash
cd backend && uv run pytest tests/e2e/test_billing_cost_api.py::test_get_timeseries_workspace_two_workspaces_two_days -v
```

Expected: `AttributeError: 'BillingRepository' object has no attribute 'get_timeseries'`.

- [ ] **Step 1.3: Implement `get_timeseries`**

Add to `backend/cubeplex/repositories/billing.py` (after `get_org_spend`):

```python
    async def get_timeseries(
        self,
        *,
        dimension: Literal["workspace", "user", "model"],
        since: datetime,
        until: datetime,
        granularity: Literal["day", "week"] = "day",
        workspace_ids: list[str] | None = None,
        models: list[str] | None = None,
        max_series: int = 25,
    ) -> list[dict[str, Any]]:
        """2D aggregation: (dimension bucket × time bucket).

        Returns one row per (bucket, date) pair. The caller is expected to
        zero-pad missing dates and apply the top-N + "__other" collapse.
        Series count is capped at `max_series` by total cost; everything
        below the cap is collapsed to `bucket="__other"`.
        """
        # Time bucket column
        if granularity == "week":
            time_col = func.date_trunc("week", BillingEvent.started_at).label("time_bucket")
        else:
            time_col = func.date(BillingEvent.started_at).label("time_bucket")

        # Dimension bucket column
        if dimension == "workspace":
            dim_col = BillingEvent.workspace_id.label("bucket")  # type: ignore[attr-defined]
            dim_group = BillingEvent.workspace_id
        elif dimension == "user":
            dim_col = BillingEvent.user_id.label("bucket")  # type: ignore[attr-defined]
            dim_group = BillingEvent.user_id
        else:  # model
            dim_col = (LlmBillingEvent.provider + "/" + LlmBillingEvent.model_id).label(  # type: ignore[attr-defined]
                "bucket"
            )
            dim_group = (LlmBillingEvent.provider, LlmBillingEvent.model_id)

        stmt = (
            select(  # type: ignore[call-overload]
                dim_col,
                time_col,
                func.sum(BillingEvent.cost_amount_micro).label("cost"),
                func.count(BillingEvent.id).label("calls"),  # type: ignore[arg-type]
                func.sum(LlmBillingEvent.input_tokens).label("input_tokens"),
                func.sum(LlmBillingEvent.output_tokens).label("output_tokens"),
                func.sum(LlmBillingEvent.cache_read_tokens).label("cache_read_tokens"),
                func.sum(LlmBillingEvent.cache_write_tokens).label("cache_write_tokens"),
                BillingEvent.currency,
            )
            .join(LlmBillingEvent, LlmBillingEvent.billing_event_id == BillingEvent.id)
            .where(
                BillingEvent.org_id == self.org_id,
                BillingEvent.started_at >= since,
                BillingEvent.started_at <= until,
                BillingEvent.event_type == "llm_call",
            )
        )
        if workspace_ids:
            stmt = stmt.where(BillingEvent.workspace_id.in_(workspace_ids))
        if models:
            # models arrive as "provider/model_id" strings; rebuild predicate
            from sqlalchemy import or_, and_
            conds = []
            for m in models:
                if "/" in m:
                    p, mid = m.split("/", 1)
                    conds.append(and_(LlmBillingEvent.provider == p, LlmBillingEvent.model_id == mid))
            if conds:
                stmt = stmt.where(or_(*conds))

        if isinstance(dim_group, tuple):
            stmt = stmt.group_by(*dim_group, time_col, BillingEvent.currency)
        else:
            stmt = stmt.group_by(dim_group, time_col, BillingEvent.currency)

        rows = (await self.session.execute(stmt)).all()

        # Build series map: bucket -> {date: point}
        series_map: dict[str, dict[str, dict[str, Any]]] = {}
        bucket_totals: dict[str, int] = {}
        currency = "USD"
        for r in rows:
            bucket = str(r.bucket)
            date_str = (
                r.time_bucket.isoformat() if hasattr(r.time_bucket, "isoformat") else str(r.time_bucket)
            )
            series_map.setdefault(bucket, {})[date_str] = {
                "date": date_str,
                "cost_amount_micro": int(r.cost or 0),
                "calls": int(r.calls or 0),
                "input_tokens": int(r.input_tokens or 0),
                "output_tokens": int(r.output_tokens or 0),
                "cache_read_tokens": int(r.cache_read_tokens or 0),
                "cache_write_tokens": int(r.cache_write_tokens or 0),
            }
            bucket_totals[bucket] = bucket_totals.get(bucket, 0) + int(r.cost or 0)
            currency = r.currency

        # Build full date axis (zero-pad)
        from datetime import date as date_t, timedelta as td

        step = td(days=7) if granularity == "week" else td(days=1)
        cur = since.date() if granularity == "day" else (
            since.date() - td(days=since.weekday())
        )
        end = until.date()
        date_axis: list[str] = []
        while cur <= end:
            date_axis.append(cur.isoformat())
            cur = cur + step

        def _zero_point(d: str) -> dict[str, Any]:
            return {
                "date": d,
                "cost_amount_micro": 0,
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }

        # Top-N + "__other"
        ranked = sorted(bucket_totals.items(), key=lambda kv: kv[1], reverse=True)
        keep = {b for b, _ in ranked[: max(0, max_series - 1)]}
        other_points: dict[str, dict[str, Any]] = {}
        result_series: list[dict[str, Any]] = []
        for bucket, by_date in series_map.items():
            points = [by_date.get(d, _zero_point(d)) for d in date_axis]
            if bucket in keep or len(ranked) <= max_series:
                result_series.append({"bucket": bucket, "points": points, "currency": currency})
            else:
                for p in points:
                    op = other_points.setdefault(p["date"], _zero_point(p["date"]))
                    for k in (
                        "cost_amount_micro", "calls", "input_tokens", "output_tokens",
                        "cache_read_tokens", "cache_write_tokens",
                    ):
                        op[k] += p[k]
        if other_points:
            result_series.append({
                "bucket": "__other",
                "points": [other_points.get(d, _zero_point(d)) for d in date_axis],
                "currency": currency,
            })
        return result_series
```

- [ ] **Step 1.4: Run to verify pass**

```bash
cd backend && uv run pytest tests/e2e/test_billing_cost_api.py::test_get_timeseries_workspace_two_workspaces_two_days -v
```

Expected: PASS.

- [ ] **Step 1.5: Add a second test — top-N collapse**

Append to `test_billing_cost_api.py`:

```python
async def test_get_timeseries_top_n_collapses_to_other() -> None:
    """When buckets exceed max_series, smallest collapse into '__other'."""
    session = await _direct_session()
    try:
        org = "org-ts-2"
        day = datetime(2026, 5, 1, 12, tzinfo=UTC)
        rows = []
        for i in range(30):
            rows.append({
                "workspace_id": f"ws-{i:02d}",
                "user_id": "u",
                "provider": "openai",
                "model_id": "gpt-4o",
                "started_at": day,
                "cost_micro": (30 - i) * 100,  # ws-00 highest, ws-29 lowest
            })
        await _seed_events(session, org_id=org, rows=rows)
        repo = BillingRepository(session, org_id=org)
        series = await repo.get_timeseries(
            dimension="workspace",
            since=datetime(2026, 5, 1, tzinfo=UTC),
            until=datetime(2026, 5, 1, 23, 59, 59, tzinfo=UTC),
            granularity="day",
            max_series=10,
        )
        buckets = [s["bucket"] for s in series]
        assert "__other" in buckets
        assert len(series) == 10  # 9 real + 1 other
        # totals preserved
        total = sum(p["cost_amount_micro"] for s in series for p in s["points"])
        assert total == sum(r["cost_micro"] for r in rows)
    finally:
        await session.close()
```

- [ ] **Step 1.6: Run both repo tests**

```bash
cd backend && uv run pytest tests/e2e/test_billing_cost_api.py -v
```

Expected: PASS for both.

- [ ] **Step 1.7: Run mypy / lint / format**

```bash
cd backend && make format && make lint && make type-check
```

Fix any complaints before committing.

- [ ] **Step 1.8: Commit**

```bash
git add backend/cubeplex/repositories/billing.py backend/tests/e2e/test_billing_cost_api.py
git commit -m "feat(billing): add get_timeseries with zero-padding and top-N collapse"
```

---

### Task 2: `/admin/cost/summary` returns `by_user`

**Files:**
- Modify: `backend/cubeplex/api/schemas/billing.py`
- Modify: `backend/cubeplex/api/routes/v1/cost.py:75-103`
- Test: `backend/tests/e2e/test_billing_cost_api.py`

- [ ] **Step 2.1: Write failing E2E test**

Append to `test_billing_cost_api.py`:

```python
import httpx

from tests.e2e.conftest import register_admin_and_login  # if helper exists; otherwise inline


async def test_summary_includes_by_user(http_admin_client: httpx.AsyncClient) -> None:
    """/admin/cost/summary returns by_user mirroring by_workspace."""
    resp = await http_admin_client.get("/api/v1/admin/cost/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert "by_user" in body
    assert isinstance(body["by_user"], list)
    if body["by_user"]:
        first = body["by_user"][0]
        assert first["bucket_type"] == "user"
        for k in (
            "bucket", "input_tokens", "output_tokens", "cache_read_tokens",
            "cache_write_tokens", "cost_amount_micro", "currency", "call_count",
        ):
            assert k in first
```

If `http_admin_client` / `register_admin_and_login` don't already exist in `conftest.py`, follow the registration pattern in `test_billing.py` (look at how it talks to the running API — copy that fixture).

- [ ] **Step 2.2: Run to verify failure**

```bash
cd backend && uv run pytest tests/e2e/test_billing_cost_api.py::test_summary_includes_by_user -v
```

Expected: `KeyError: 'by_user'` or assertion failure on missing field.

- [ ] **Step 2.3: Add field to the pydantic schema**

In `backend/cubeplex/api/schemas/billing.py`, replace `CostSummaryResponse`:

```python
class CostSummaryResponse(BaseModel):
    from_date: date
    to_date: date
    total_cost_amount_micro: int
    currency: str
    total_calls: int
    by_workspace: list[CostAggregateRow]
    by_model: list[CostAggregateRow]
    by_user: list[CostAggregateRow]
    by_day: list[CostAggregateRow]
```

- [ ] **Step 2.4: Populate `by_user` in the route**

In `backend/cubeplex/api/routes/v1/cost.py`, inside `get_cost_summary`, after the `by_day` line add:

```python
    by_user = await repo.get_org_spend(since=since, until=until, group_by="user")
```

Pass it into the response constructor:

```python
    return CostSummaryResponse(
        from_date=since.date(),
        to_date=until.date(),
        total_cost_amount_micro=total_cost,
        currency=currency,
        total_calls=total_calls,
        by_workspace=[CostAggregateRow(**r) for r in by_workspace],
        by_model=[CostAggregateRow(**r) for r in by_model],
        by_user=[CostAggregateRow(**r) for r in by_user],
        by_day=[CostAggregateRow(**r) for r in by_day],
    )
```

- [ ] **Step 2.5: Run to verify pass**

```bash
cd backend && uv run pytest tests/e2e/test_billing_cost_api.py::test_summary_includes_by_user -v
```

Expected: PASS.

- [ ] **Step 2.6: Commit**

```bash
git add backend/cubeplex/api/schemas/billing.py backend/cubeplex/api/routes/v1/cost.py backend/tests/e2e/test_billing_cost_api.py
git commit -m "feat(api): add by_user to /admin/cost/summary"
```

---

### Task 3: `/admin/cost/timeseries` endpoint

**Files:**
- Modify: `backend/cubeplex/api/schemas/billing.py`
- Modify: `backend/cubeplex/api/routes/v1/cost.py`
- Test: `backend/tests/e2e/test_billing_cost_api.py`

- [ ] **Step 3.1: Write failing test**

Append to `test_billing_cost_api.py`:

```python
async def test_timeseries_workspace_happy_path(http_admin_client: httpx.AsyncClient) -> None:
    resp = await http_admin_client.get(
        "/api/v1/admin/cost/timeseries",
        params={"dimension": "workspace", "granularity": "day"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dimension"] == "workspace"
    assert body["granularity"] == "day"
    assert "series" in body and isinstance(body["series"], list)


async def test_timeseries_rejects_invalid_dimension(http_admin_client: httpx.AsyncClient) -> None:
    resp = await http_admin_client.get(
        "/api/v1/admin/cost/timeseries",
        params={"dimension": "skill"},
    )
    assert resp.status_code == 422  # FastAPI validation


async def test_timeseries_requires_admin(http_member_client: httpx.AsyncClient) -> None:
    resp = await http_member_client.get(
        "/api/v1/admin/cost/timeseries",
        params={"dimension": "workspace"},
    )
    assert resp.status_code == 403
```

`http_member_client` is a non-admin user fixture; if it doesn't exist yet, build it the same way `http_admin_client` is built but using a freshly-registered second user in a *different* org (so they aren't an admin of the admin's org). For single-tenant test mode, fall back to registering a normal user that does not have `OrganizationMembership.role = admin`.

- [ ] **Step 3.2: Run to verify failure**

```bash
cd backend && uv run pytest tests/e2e/test_billing_cost_api.py::test_timeseries_workspace_happy_path -v
```

Expected: 404 (route doesn't exist yet).

- [ ] **Step 3.3: Add response schemas**

In `backend/cubeplex/api/schemas/billing.py` append:

```python
class TimeseriesPoint(BaseModel):
    date: str  # YYYY-MM-DD (or week-start date if granularity=week)
    cost_amount_micro: int
    calls: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


class TimeseriesSeries(BaseModel):
    bucket: str  # workspace_id | user_id | "provider/model_id" | "__other"
    points: list[TimeseriesPoint]
    currency: str


class TimeseriesResponse(BaseModel):
    from_date: date
    to_date: date
    granularity: str  # "day" | "week"
    dimension: str    # "workspace" | "model" | "user"
    series: list[TimeseriesSeries]
    currency: str
```

- [ ] **Step 3.4: Add route**

In `backend/cubeplex/api/routes/v1/cost.py`, add after the `/by-workspace/{ws_id}` handler:

```python
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
    )
    currency = series_raw[0]["currency"] if series_raw else "USD"
    return TimeseriesResponse(
        from_date=since.date(),
        to_date=until.date(),
        granularity=granularity,
        dimension=dimension,
        currency=currency,
        series=[
            TimeseriesSeries(
                bucket=s["bucket"],
                currency=s["currency"],
                points=[TimeseriesPoint(**p) for p in s["points"]],
            )
            for s in series_raw
        ],
    )
```

Imports at top of `cost.py`:

```python
from typing import Annotated, Literal
from cubeplex.api.schemas.billing import (
    CostAggregateRow,
    CostSummaryResponse,
    TimeseriesResponse,
    TimeseriesSeries,
    TimeseriesPoint,
)
```

- [ ] **Step 3.5: Run to verify pass**

```bash
cd backend && uv run pytest tests/e2e/test_billing_cost_api.py -v
```

Expected: PASS for all three timeseries tests.

- [ ] **Step 3.6: Lint + type-check**

```bash
cd backend && make check
```

- [ ] **Step 3.7: Commit**

```bash
git add backend/cubeplex/api/schemas/billing.py backend/cubeplex/api/routes/v1/cost.py backend/tests/e2e/test_billing_cost_api.py
git commit -m "feat(api): add /admin/cost/timeseries endpoint"
```

---

## Frontend — shared core

### Task 4: Core types and API client

**Files:**
- Modify: `frontend/packages/core/src/types/billing.ts`
- Modify: `frontend/packages/core/src/api/billing.ts`
- Modify: `frontend/packages/core/src/api/index.ts`

- [ ] **Step 4.1: Extend `CostSummaryResponse` and add timeseries types**

Replace `frontend/packages/core/src/types/billing.ts` with:

```typescript
export interface CostAggregateRow {
  bucket: string
  bucket_type: 'workspace' | 'user' | 'model' | 'day'
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
  cost_amount_micro: number
  currency: string
  call_count: number
}

export interface CostSummaryResponse {
  from_date: string
  to_date: string
  total_cost_amount_micro: number
  currency: string
  total_calls: number
  by_workspace: CostAggregateRow[]
  by_model: CostAggregateRow[]
  by_user: CostAggregateRow[]
  by_day: CostAggregateRow[]
}

export interface TimeseriesPoint {
  date: string
  cost_amount_micro: number
  calls: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
}

export interface TimeseriesSeries {
  bucket: string
  points: TimeseriesPoint[]
  currency: string
}

export interface TimeseriesResponse {
  from_date: string
  to_date: string
  granularity: 'day' | 'week'
  dimension: 'workspace' | 'model' | 'user'
  series: TimeseriesSeries[]
  currency: string
}

export function formatCostUsd(micro: number, currency: string = 'USD'): string {
  const amount = micro / 1_000_000
  return `${currency} ${amount.toFixed(4)}`
}
```

- [ ] **Step 4.2: Add `fetchCostTimeseries`**

Append to `frontend/packages/core/src/api/billing.ts`:

```typescript
import type { TimeseriesResponse } from '../types/billing'

export interface TimeseriesParams {
  dimension: 'workspace' | 'model' | 'user'
  granularity?: 'day' | 'week'
  from?: string
  to?: string
  workspace_ids?: string[]
  models?: string[]
}

export async function fetchCostTimeseries(
  client: ApiClient,
  params: TimeseriesParams,
): Promise<TimeseriesResponse> {
  const query = new URLSearchParams()
  query.set('dimension', params.dimension)
  if (params.granularity) query.set('granularity', params.granularity)
  if (params.from) query.set('from_date', params.from)
  if (params.to) query.set('to_date', params.to)
  if (params.workspace_ids && params.workspace_ids.length) {
    query.set('workspace_ids', params.workspace_ids.join(','))
  }
  if (params.models && params.models.length) {
    query.set('models', params.models.join(','))
  }
  const res = await client.get(`/api/v1/admin/cost/timeseries?${query}`)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<TimeseriesResponse>
}
```

Also re-export the type from the existing import line:

```typescript
import type {
  CostAggregateRow,
  CostSummaryResponse,
  TimeseriesResponse,
} from '../types/billing'
```

(remove the standalone `import type { TimeseriesResponse }` line — keep imports merged).

- [ ] **Step 4.3: Update index exports**

In `frontend/packages/core/src/api/index.ts`:

```typescript
export {
  fetchCostSummary,
  fetchWorkspaceCost,
  fetchCostTimeseries,
  buildExportUrl,
} from './billing'
```

In `frontend/packages/core/src/types/index.ts`:

```typescript
export type {
  CostAggregateRow,
  CostSummaryResponse,
  TimeseriesPoint,
  TimeseriesSeries,
  TimeseriesResponse,
} from './billing'
```

- [ ] **Step 4.4: Build core**

```bash
cd frontend && pnpm --filter @cubeplex/core build
```

Expected: clean tsc output, `dist/` updated.

- [ ] **Step 4.5: Commit**

```bash
git add frontend/packages/core
git commit -m "feat(core): add timeseries types and fetchCostTimeseries"
```

---

## Frontend — web shell

### Task 5: Add `recharts` dependency

**Files:**
- Modify: `frontend/packages/web/package.json`

- [ ] **Step 5.1: Add the dep**

```bash
cd frontend && pnpm --filter web add recharts@^2
```

Pin to v2 (the current line); v3 has breaking changes that may not match React 19 yet — pin to be safe and let a future bump be intentional.

- [ ] **Step 5.2: Verify build still passes**

```bash
cd frontend && pnpm -w -r run build
```

- [ ] **Step 5.3: Commit**

```bash
git add frontend/packages/web/package.json frontend/pnpm-lock.yaml
git commit -m "chore(web): add recharts dependency"
```

---

### Task 6: i18n — `adminInsights` namespace

**Files:**
- Modify: `frontend/packages/web/messages/en.json`
- Modify: `frontend/packages/web/messages/zh.json`
- Modify: existing `adminNav` block in both files (for the subnav label rename)

- [ ] **Step 6.1: Edit `en.json`**

Replace the entire `adminCost` block with `adminInsights`. Inside, all cost-specific keys live under `cost.*` so future non-cost sections have room:

```json
"adminInsights": {
  "heading": "Insights",
  "loading": "Loading…",
  "retry": "Retry",
  "noData": "No data in this period",
  "exportOrgCsv": "Export CSV ↓",
  "kpi": {
    "totalCost": "Total cost",
    "totalCalls": "Total calls",
    "avgPerCall": "Avg / call",
    "cacheHitRate": "Cache hit rate",
    "activeUsers": "Active users",
    "vsPrior": "vs prior {days}d",
    "unchanged": "unchanged"
  },
  "filters": {
    "range": "Range",
    "workspaces": "Workspaces",
    "models": "Models",
    "granularity": "Granularity",
    "day": "day",
    "week": "week"
  },
  "cost": {
    "byWorkspace": "By workspace",
    "byModel": "By model",
    "byUser": "By user",
    "cacheEfficiency": "Cache efficiency",
    "cacheEfficiencyHint": "Hit rate per day, by model",
    "columns": {
      "workspace": "Workspace",
      "model": "Provider / Model",
      "user": "User",
      "calls": "Calls",
      "input": "Input",
      "output": "Output",
      "cacheRw": "Cache R/W",
      "cacheReads": "Cache reads",
      "cacheWrites": "Cache writes",
      "uncachedInput": "Uncached input",
      "hitRate": "Hit rate",
      "estSavings": "Est. savings",
      "cost": "Cost",
      "share": "Share"
    },
    "other": "Other ({count} items)",
    "showAll": "+{count} more · show all",
    "showLess": "Show less",
    "orgTotal": "Org total",
    "orgAvg": "org avg"
  }
}
```

In the existing `adminNav` block, replace `"cost": "Cost"` with `"insights": "Insights"`.

- [ ] **Step 6.2: Mirror the same shape in `zh.json`**

Use Chinese strings; structure must match key-for-key. Example values:

```json
"adminInsights": {
  "heading": "运营洞察",
  "loading": "加载中…",
  ...
  "filters": {
    "range": "时间范围",
    "workspaces": "工作区",
    "models": "模型",
    "granularity": "粒度",
    "day": "日",
    "week": "周"
  },
  ...
}
```

Adjust translations to match the rest of `zh.json`'s tone — don't over-translate "KPI", "cache" etc. if other admin pages keep them in English.

In `adminNav`, replace `"cost": "成本"` with `"insights": "洞察"` (or whatever matches the existing translation register).

- [ ] **Step 6.3: Delete the old `adminCost` block**

The only caller is the old `app/admin/cost/page.tsx` which is being replaced. No grep hits should remain after Task 9. Delete the block from both files now to fail fast if anything references it.

- [ ] **Step 6.4: Commit**

```bash
git add frontend/packages/web/messages/en.json frontend/packages/web/messages/zh.json
git commit -m "feat(i18n): adminInsights namespace; rename Cost subnav to Insights"
```

---

### Task 7: AdminSubNav — relabel + change icon

**Files:**
- Modify: `frontend/packages/web/components/admin/AdminSubNav.tsx`

- [ ] **Step 7.1: Swap icon import**

Replace `CircleDollarSign` with `BarChart3` in the lucide-react import line at the top of the file.

- [ ] **Step 7.2: Update the nav item**

In `NATIVE_ITEMS`, change the last row from:

```typescript
{ href: '/admin/cost', label: t('cost'), icon: CircleDollarSign },
```

to:

```typescript
{ href: '/admin/insights', label: t('insights'), icon: BarChart3 },
```

- [ ] **Step 7.3: Type-check**

```bash
cd frontend && pnpm type-check
```

- [ ] **Step 7.4: Commit**

```bash
git add frontend/packages/web/components/admin/AdminSubNav.tsx
git commit -m "feat(admin-nav): swap Cost link to Insights"
```

---

### Task 8: Helpers + unit tests

**Files:**
- Create: `frontend/packages/web/lib/cost/helpers.ts`
- Create: `frontend/packages/web/lib/cost/helpers.test.ts`

Pure logic that the page consumes. These are the only unit tests in the frontend portion of this plan; everything else is covered by Playwright E2E per project convention.

- [ ] **Step 8.1: Write failing tests**

`frontend/packages/web/lib/cost/helpers.test.ts`:

```typescript
import { describe, it, expect } from 'vitest'
import { computeCacheHitRate, topNWithOther, percentDelta } from './helpers'

describe('computeCacheHitRate', () => {
  it('returns null when no input or cache reads', () => {
    expect(computeCacheHitRate({ input: 0, cacheRead: 0 })).toBeNull()
  })
  it('returns ratio of cache_read to (cache_read + input)', () => {
    expect(computeCacheHitRate({ input: 70, cacheRead: 30 })).toBeCloseTo(0.3)
  })
})

describe('topNWithOther', () => {
  it('keeps top N items by `cost`', () => {
    const items = [
      { id: 'a', cost: 100 },
      { id: 'b', cost: 200 },
      { id: 'c', cost: 50 },
      { id: 'd', cost: 10 },
    ]
    const result = topNWithOther(items, 2, (i) => i.cost)
    expect(result.kept.map((x) => x.id)).toEqual(['b', 'a'])
    expect(result.otherCount).toBe(2)
    expect(result.otherSum).toBe(60)
  })
  it('returns everything when count <= N', () => {
    const items = [{ id: 'a', cost: 1 }]
    const result = topNWithOther(items, 5, (i) => i.cost)
    expect(result.kept).toHaveLength(1)
    expect(result.otherCount).toBe(0)
  })
})

describe('percentDelta', () => {
  it('returns null when prior is 0', () => {
    expect(percentDelta(100, 0)).toBeNull()
  })
  it('returns positive percent for growth', () => {
    expect(percentDelta(120, 100)).toBeCloseTo(0.2)
  })
})
```

- [ ] **Step 8.2: Run failing tests**

Vitest is already configured for the web package (`pnpm --filter web test`).

```bash
cd frontend && pnpm --filter web exec vitest run lib/cost/helpers.test.ts
```

Expected: FAIL with "Cannot find module './helpers'".

- [ ] **Step 8.3: Implement helpers**

`frontend/packages/web/lib/cost/helpers.ts`:

```typescript
export function computeCacheHitRate(args: {
  input: number
  cacheRead: number
}): number | null {
  const denom = args.input + args.cacheRead
  if (denom === 0) return null
  return args.cacheRead / denom
}

export interface TopNResult<T> {
  kept: T[]
  otherCount: number
  otherSum: number
}

export function topNWithOther<T>(
  items: T[],
  n: number,
  costOf: (item: T) => number,
): TopNResult<T> {
  if (items.length <= n) {
    return { kept: [...items].sort((a, b) => costOf(b) - costOf(a)), otherCount: 0, otherSum: 0 }
  }
  const sorted = [...items].sort((a, b) => costOf(b) - costOf(a))
  const kept = sorted.slice(0, n)
  const rest = sorted.slice(n)
  return {
    kept,
    otherCount: rest.length,
    otherSum: rest.reduce((s, x) => s + costOf(x), 0),
  }
}

export function percentDelta(current: number, prior: number): number | null {
  if (prior === 0) return null
  return (current - prior) / prior
}

export function formatPercent(v: number | null, digits = 0): string {
  if (v === null || Number.isNaN(v)) return '—'
  return `${(v * 100).toFixed(digits)}%`
}
```

- [ ] **Step 8.4: Tests pass**

```bash
cd frontend && pnpm --filter web exec vitest run lib/cost/helpers.test.ts
```

- [ ] **Step 8.5: Commit**

```bash
git add frontend/packages/web/lib/cost/
git commit -m "feat(insights): pure helpers for cache rate, top-N, deltas"
```

---

### Task 9: Route scaffold — `/admin/insights` + redirect from `/admin/cost`

**Files:**
- Create: `frontend/packages/web/app/admin/insights/page.tsx`
- Replace: `frontend/packages/web/app/admin/cost/page.tsx`

- [ ] **Step 9.1: Create the new page as a thin client wrapper**

`frontend/packages/web/app/admin/insights/page.tsx`:

```typescript
'use client'

import { InsightsShell } from '@/components/admin/insights/InsightsShell'

export default function InsightsPage() {
  return <InsightsShell />
}
```

`InsightsShell` lands in Task 13. This stub compiles once the shell file exists; for now skip running the dev server.

- [ ] **Step 9.2: Replace the old cost page with a server redirect**

Replace the contents of `frontend/packages/web/app/admin/cost/page.tsx`:

```typescript
import { redirect } from 'next/navigation'

export default function CostRedirect(): never {
  redirect('/admin/insights')
}
```

- [ ] **Step 9.3: Commit (stubs in place; will compile after Task 13)**

```bash
git add frontend/packages/web/app/admin/insights/page.tsx \
        frontend/packages/web/app/admin/cost/page.tsx
git commit -m "feat(admin-insights): route scaffold + cost redirect stub"
```

---

### Task 10: `useCostData` hook

**Files:**
- Create: `frontend/packages/web/hooks/useCostData.ts`

- [ ] **Step 10.1: Implement the hook**

```typescript
'use client'

import { useEffect, useMemo, useState } from 'react'
import {
  createApiClient,
  fetchCostSummary,
  fetchCostTimeseries,
  type CostSummaryResponse,
  type TimeseriesResponse,
} from '@cubeplex/core'

export type RangePreset = '7d' | '30d' | '90d'
export type Granularity = 'day' | 'week'

export interface CostFilters {
  range: RangePreset | { from: string; to: string }
  workspaceIds: string[]
  models: string[]
  granularity: Granularity
}

export interface CostData {
  summary: CostSummaryResponse | null
  priorSummary: CostSummaryResponse | null
  byWorkspace: TimeseriesResponse | null
  byModel: TimeseriesResponse | null
  byUser: TimeseriesResponse | null
  loading: boolean
  error: string | null
  errors: { section: string; message: string }[]
}

function resolveDates(filters: CostFilters): { from: string; to: string; days: number } {
  if (typeof filters.range === 'object') {
    const days = Math.max(
      1,
      Math.round(
        (new Date(filters.range.to).getTime() - new Date(filters.range.from).getTime()) /
          (24 * 3600 * 1000),
      ),
    )
    return { from: filters.range.from, to: filters.range.to, days }
  }
  const days = filters.range === '7d' ? 7 : filters.range === '30d' ? 30 : 90
  const to = new Date()
  const from = new Date(to.getTime() - days * 24 * 3600 * 1000)
  const iso = (d: Date) => d.toISOString().slice(0, 10)
  return { from: iso(from), to: iso(to), days }
}

function priorWindow(from: string, to: string): { from: string; to: string } {
  const fromD = new Date(from)
  const toD = new Date(to)
  const span = toD.getTime() - fromD.getTime()
  const priorTo = new Date(fromD.getTime() - 24 * 3600 * 1000)
  const priorFrom = new Date(priorTo.getTime() - span)
  const iso = (d: Date) => d.toISOString().slice(0, 10)
  return { from: iso(priorFrom), to: iso(priorTo) }
}

export function useCostData(filters: CostFilters): CostData {
  const client = useMemo(() => createApiClient(''), [])
  const key = JSON.stringify(filters)
  const [data, setData] = useState<CostData>({
    summary: null,
    priorSummary: null,
    byWorkspace: null,
    byModel: null,
    byUser: null,
    loading: true,
    error: null,
    errors: [],
  })

  useEffect(() => {
    let cancelled = false
    const { from, to } = resolveDates(filters)
    const prior = priorWindow(from, to)
    setData((d) => ({ ...d, loading: true, error: null, errors: [] }))

    const wsIds = filters.workspaceIds.length ? filters.workspaceIds : undefined
    const models = filters.models.length ? filters.models : undefined

    Promise.allSettled([
      fetchCostSummary(client, { from, to }),
      fetchCostSummary(client, { from: prior.from, to: prior.to }),
      fetchCostTimeseries(client, {
        dimension: 'workspace', from, to, granularity: filters.granularity,
        workspace_ids: wsIds, models,
      }),
      fetchCostTimeseries(client, {
        dimension: 'model', from, to, granularity: filters.granularity,
        workspace_ids: wsIds, models,
      }),
      fetchCostTimeseries(client, {
        dimension: 'user', from, to, granularity: filters.granularity,
        workspace_ids: wsIds, models,
      }),
    ]).then((results) => {
      if (cancelled) return
      const [summary, priorSummary, byWorkspace, byModel, byUser] = results
      const errors: { section: string; message: string }[] = []
      const pick = <T,>(label: string, r: PromiseSettledResult<T>): T | null => {
        if (r.status === 'fulfilled') return r.value
        errors.push({ section: label, message: r.reason?.message ?? String(r.reason) })
        return null
      }
      const summaryVal = pick('summary', summary)
      // top-level error only if /summary itself failed
      const topLevelError = summary.status === 'rejected' ? errors[0]?.message ?? 'load failed' : null
      setData({
        summary: summaryVal,
        priorSummary: pick('priorSummary', priorSummary),
        byWorkspace: pick('byWorkspace', byWorkspace),
        byModel: pick('byModel', byModel),
        byUser: pick('byUser', byUser),
        loading: false,
        error: topLevelError,
        errors,
      })
    })

    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, client])

  return data
}
```

- [ ] **Step 10.2: Type-check**

```bash
cd frontend && pnpm type-check
```

- [ ] **Step 10.3: Commit**

```bash
git add frontend/packages/web/hooks/useCostData.ts
git commit -m "feat(insights): useCostData hook with parallel fetch + prior window"
```

---

### Task 11: Recharts wrappers

**Files:**
- Create: `frontend/packages/web/components/admin/insights/cost/StackedChart.tsx`
- Create: `frontend/packages/web/components/admin/insights/cost/RateChart.tsx`

- [ ] **Step 11.1: Stacked area chart**

```typescript
'use client'

import {
  Area, AreaChart, CartesianGrid, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'
import type { TimeseriesResponse } from '@cubeplex/core'

interface Props {
  data: TimeseriesResponse
  palette: string[]   // ordered by series rank; "__other" uses last color
  height?: number
  formatValue?: (micro: number) => string
}

interface PivotRow {
  date: string
  [bucket: string]: number | string
}

function pivot(data: TimeseriesResponse): { rows: PivotRow[]; buckets: string[] } {
  const buckets = data.series.map((s) => s.bucket)
  const datesSet = new Set<string>()
  data.series.forEach((s) => s.points.forEach((p) => datesSet.add(p.date)))
  const dates = [...datesSet].sort()
  const rows: PivotRow[] = dates.map((date) => {
    const row: PivotRow = { date }
    data.series.forEach((s) => {
      const pt = s.points.find((p) => p.date === date)
      row[s.bucket] = pt ? pt.cost_amount_micro / 1_000_000 : 0
    })
    return row
  })
  return { rows, buckets }
}

export function StackedChart({ data, palette, height = 200, formatValue }: Props) {
  const { rows, buckets } = pivot(data)
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="date" tick={{ fontSize: 10 }} axisLine={false} tickLine={false} />
        <YAxis
          tick={{ fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          width={50}
          tickFormatter={(v) => (typeof v === 'number' ? `$${v.toFixed(0)}` : String(v))}
        />
        <Tooltip
          formatter={(v: number) =>
            formatValue ? formatValue(v * 1_000_000) : `$${v.toFixed(2)}`
          }
        />
        {buckets.map((b, i) => (
          <Area
            key={b}
            type="monotone"
            dataKey={b}
            stackId="cost"
            stroke={palette[Math.min(i, palette.length - 1)]}
            fill={palette[Math.min(i, palette.length - 1)]}
            fillOpacity={0.7}
            strokeWidth={1.2}
          />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  )
}
```

- [ ] **Step 11.2: Rate line chart**

```typescript
'use client'

import {
  CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'

export interface RateSeries {
  bucket: string
  points: { date: string; rate: number | null }[]
  color: string
}

interface Props {
  series: RateSeries[]
  orgAvg?: { date: string; rate: number | null }[]
  height?: number
}

interface PivotRow {
  date: string
  [bucket: string]: number | string | null
}

export function RateChart({ series, orgAvg, height = 200 }: Props) {
  const datesSet = new Set<string>()
  series.forEach((s) => s.points.forEach((p) => datesSet.add(p.date)))
  orgAvg?.forEach((p) => datesSet.add(p.date))
  const dates = [...datesSet].sort()
  const rows: PivotRow[] = dates.map((date) => {
    const row: PivotRow = { date }
    series.forEach((s) => {
      const pt = s.points.find((p) => p.date === date)
      row[s.bucket] = pt && pt.rate !== null ? pt.rate * 100 : null
    })
    if (orgAvg) {
      const pt = orgAvg.find((p) => p.date === date)
      row.__avg = pt && pt.rate !== null ? pt.rate * 100 : null
    }
    return row
  })
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="date" tick={{ fontSize: 10 }} axisLine={false} tickLine={false} />
        <YAxis
          domain={[0, 100]}
          tick={{ fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          width={36}
          tickFormatter={(v) => `${v}%`}
        />
        <Tooltip formatter={(v: number) => (v == null ? '—' : `${v.toFixed(1)}%`)} />
        {series.map((s) => (
          <Line
            key={s.bucket}
            type="monotone"
            dataKey={s.bucket}
            stroke={s.color}
            strokeWidth={1.8}
            dot={false}
            connectNulls
          />
        ))}
        {orgAvg && (
          <Line
            type="monotone"
            dataKey="__avg"
            stroke="hsl(var(--muted-foreground))"
            strokeWidth={1.2}
            strokeDasharray="4 4"
            dot={false}
            connectNulls
          />
        )}
      </LineChart>
    </ResponsiveContainer>
  )
}
```

- [ ] **Step 11.3: Type-check**

```bash
cd frontend && pnpm type-check
```

- [ ] **Step 11.4: Commit**

```bash
git add frontend/packages/web/components/admin/insights/cost/StackedChart.tsx \
        frontend/packages/web/components/admin/insights/cost/RateChart.tsx
git commit -m "feat(insights): recharts stacked area + rate line wrappers"
```

---

### Task 12: Topbar + filter sidebar

**Files:**
- Create: `frontend/packages/web/components/admin/insights/InsightsTopBar.tsx`
- Create: `frontend/packages/web/components/admin/insights/InsightsFilterSidebar.tsx`

**Note on date range picker:** The spec describes a 📅 button that opens a
custom date range picker overriding the preset. This iteration ships
*presets only* (7d / 30d / 90d). The custom-date code path is already
threaded through `useCostData` (the `range` filter accepts
`{from, to}`) — only the UI control is deferred to a follow-up. The
top bar shows the resolved range as text. Do not add a date picker
component in this plan.

- [ ] **Step 12.1: Topbar**

```typescript
'use client'

import { useTranslations } from 'next-intl'
import { buildExportUrl } from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { buttonVariants } from '@/components/ui/button'
import { Download } from 'lucide-react'

interface Props {
  fromDate: string
  toDate: string
}

export function InsightsTopBar({ fromDate, toDate }: Props) {
  const t = useTranslations('adminInsights')
  return (
    <div className="flex items-center justify-between px-4 py-3 border-b border-border/70">
      <div>
        <h1 className="text-sm font-semibold">{t('heading')}</h1>
        <p className="text-xs text-muted-foreground">{`${fromDate} — ${toDate}`}</p>
      </div>
      <a
        href={buildExportUrl(undefined, { from: fromDate, to: toDate })}
        download
        className={cn(buttonVariants({ variant: 'default', size: 'sm' }))}
      >
        <Download className="size-3.5 mr-1.5" />
        {t('exportOrgCsv')}
      </a>
    </div>
  )
}
```

- [ ] **Step 12.2: Filter sidebar**

```typescript
'use client'

import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { CostFilters, RangePreset } from '@/hooks/useCostData'

interface Props {
  filters: CostFilters
  onChange: (next: CostFilters) => void
  availableWorkspaces: { id: string; name: string }[]
  availableModels: string[]
}

const RANGES: RangePreset[] = ['7d', '30d', '90d']

export function InsightsFilterSidebar({
  filters, onChange, availableWorkspaces, availableModels,
}: Props) {
  const t = useTranslations('adminInsights.filters')

  const toggle = <T,>(list: T[], v: T): T[] =>
    list.includes(v) ? list.filter((x) => x !== v) : [...list, v]

  return (
    <aside
      className="w-52 shrink-0 border-r border-border/70 bg-card/40 p-3 text-xs space-y-5
                 overflow-y-auto"
      aria-label="filters"
    >
      <section>
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {t('range')}
        </p>
        <div className="grid grid-cols-3 gap-1">
          {RANGES.map((r) => (
            <Button
              key={r}
              size="sm"
              variant={filters.range === r ? 'default' : 'outline'}
              className="h-7 text-xs"
              onClick={() => onChange({ ...filters, range: r })}
            >
              {r}
            </Button>
          ))}
        </div>
      </section>

      <section>
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {t('workspaces')}
        </p>
        <div className="space-y-1">
          {availableWorkspaces.map((w) => {
            const on = filters.workspaceIds.includes(w.id)
            return (
              <button
                key={w.id}
                onClick={() =>
                  onChange({ ...filters, workspaceIds: toggle(filters.workspaceIds, w.id) })
                }
                className={cn(
                  'w-full rounded-md px-2 py-1 text-left text-xs',
                  on ? 'bg-primary/10 text-primary' : 'hover:bg-muted',
                )}
              >
                {w.name}
              </button>
            )
          })}
        </div>
      </section>

      <section>
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {t('models')}
        </p>
        <div className="space-y-1">
          {availableModels.map((m) => {
            const on = filters.models.includes(m)
            return (
              <button
                key={m}
                onClick={() => onChange({ ...filters, models: toggle(filters.models, m) })}
                className={cn(
                  'w-full rounded-md px-2 py-1 text-left text-[11px] font-mono',
                  on ? 'bg-primary/10 text-primary' : 'hover:bg-muted',
                )}
              >
                {m}
              </button>
            )
          })}
        </div>
      </section>

      <section>
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {t('granularity')}
        </p>
        <div className="grid grid-cols-2 gap-1">
          {(['day', 'week'] as const).map((g) => (
            <Button
              key={g}
              size="sm"
              variant={filters.granularity === g ? 'default' : 'outline'}
              className="h-7 text-xs"
              onClick={() => onChange({ ...filters, granularity: g })}
            >
              {t(g)}
            </Button>
          ))}
        </div>
      </section>
    </aside>
  )
}
```

- [ ] **Step 12.3: Type-check + commit**

```bash
cd frontend && pnpm type-check
git add frontend/packages/web/components/admin/insights/InsightsTopBar.tsx \
        frontend/packages/web/components/admin/insights/InsightsFilterSidebar.tsx
git commit -m "feat(insights): top bar + filter sidebar"
```

---

### Task 13: KPI row + stacked section + cache section + shell

**Files:**
- Create: `frontend/packages/web/components/admin/insights/cost/KpiRow.tsx`
- Create: `frontend/packages/web/components/admin/insights/cost/StackedSection.tsx`
- Create: `frontend/packages/web/components/admin/insights/cost/CacheSection.tsx`
- Create: `frontend/packages/web/components/admin/insights/InsightsShell.tsx`

This task is largest. Implement, type-check, commit at the end.

- [ ] **Step 13.1: KPI row**

`components/admin/insights/cost/KpiRow.tsx`:

```typescript
'use client'

import { useTranslations } from 'next-intl'
import type { CostSummaryResponse } from '@cubeplex/core'
import { formatPercent, percentDelta } from '@/lib/cost/helpers'
import { cn } from '@/lib/utils'

interface Props {
  summary: CostSummaryResponse
  priorSummary: CostSummaryResponse | null
  rangeDays: number
}

function fmtUsd(micro: number, currency: string): string {
  const amt = micro / 1_000_000
  return `${currency === 'USD' ? '$' : currency + ' '}${amt.toFixed(2)}`
}

function fmtNum(n: number): string {
  return n.toLocaleString()
}

function totalCacheRead(s: CostSummaryResponse): number {
  return s.by_workspace.reduce((a, r) => a + r.cache_read_tokens, 0)
}
function totalInput(s: CostSummaryResponse): number {
  return s.by_workspace.reduce((a, r) => a + r.input_tokens, 0)
}

function hitRate(s: CostSummaryResponse | null): number | null {
  if (!s) return null
  const cr = totalCacheRead(s)
  const inp = totalInput(s)
  if (cr + inp === 0) return null
  return cr / (cr + inp)
}

export function KpiRow({ summary, priorSummary, rangeDays }: Props) {
  const t = useTranslations('adminInsights.kpi')
  const cur = {
    cost: summary.total_cost_amount_micro,
    calls: summary.total_calls,
    avg: summary.total_calls ? summary.total_cost_amount_micro / summary.total_calls : 0,
    cache: hitRate(summary),
    users: summary.by_user.length,
  }
  const prev = {
    cost: priorSummary?.total_cost_amount_micro ?? null,
    calls: priorSummary?.total_calls ?? null,
    cache: hitRate(priorSummary),
    users: priorSummary?.by_user.length ?? null,
  }

  const delta = (a: number, b: number | null) => (b === null ? null : percentDelta(a, b))

  const tile = (label: string, value: string, deltaPct: number | null, kind: 'up-bad' | 'up-good' | 'neutral') => {
    const text = deltaPct === null ? t('unchanged') : formatPercent(deltaPct, 0)
    const isUp = deltaPct !== null && deltaPct > 0.01
    const isDn = deltaPct !== null && deltaPct < -0.01
    const color =
      kind === 'neutral' || (!isUp && !isDn)
        ? 'text-muted-foreground'
        : kind === 'up-bad'
        ? isUp ? 'text-red-600' : 'text-green-600'
        : isUp ? 'text-green-600' : 'text-red-600'
    return (
      <div className="rounded-md border bg-card px-3 py-2.5">
        <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {label}
        </div>
        <div className="mt-1 text-lg font-semibold tabular-nums">{value}</div>
        <div className={cn('mt-0.5 text-[11px]', color)}>
          {deltaPct === null
            ? t('unchanged')
            : `${isUp ? '↑ ' : isDn ? '↓ ' : ''}${text} ${t('vsPrior', { days: rangeDays })}`}
        </div>
      </div>
    )
  }

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-2.5">
      {tile(t('totalCost'), fmtUsd(cur.cost, summary.currency), delta(cur.cost, prev.cost), 'up-bad')}
      {tile(t('totalCalls'), fmtNum(cur.calls), delta(cur.calls, prev.calls), 'up-bad')}
      {tile(t('avgPerCall'), fmtUsd(cur.avg, summary.currency), null, 'neutral')}
      {tile(
        t('cacheHitRate'),
        cur.cache === null ? '—' : formatPercent(cur.cache, 0),
        delta(cur.cache ?? 0, prev.cache),
        'up-good',
      )}
      {tile(t('activeUsers'), fmtNum(cur.users), delta(cur.users, prev.users), 'neutral')}
    </div>
  )
}
```

- [ ] **Step 13.2: Stacked section (generic)**

`components/admin/insights/cost/StackedSection.tsx`:

```typescript
'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import type { TimeseriesResponse } from '@cubeplex/core'
import { StackedChart } from './StackedChart'
import { topNWithOther } from '@/lib/cost/helpers'

export interface Column {
  key: string
  label: string
  align?: 'left' | 'right'
  render?: (row: SummaryRow) => React.ReactNode
}

export interface SummaryRow {
  bucket: string
  cost_amount_micro: number
  call_count: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
  currency: string
}

interface Props {
  title: string
  timeseries: TimeseriesResponse
  tableRows: SummaryRow[]
  palette: string[]
  topN: number
  columns: Column[]
  showAllInitially?: boolean
}

export function StackedSection({
  title, timeseries, tableRows, palette, topN, columns, showAllInitially,
}: Props) {
  const t = useTranslations('adminInsights.cost')
  const [showAll, setShowAll] = useState(!!showAllInitially)

  const { kept, otherCount, otherSum } = topNWithOther(tableRows, topN, (r) => r.cost_amount_micro)
  const visible = showAll ? [...tableRows].sort((a, b) => b.cost_amount_micro - a.cost_amount_micro) : kept

  const totalCost = tableRows.reduce((a, r) => a + r.cost_amount_micro, 0)

  return (
    <section className="space-y-2">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold">{title}</h2>
      </div>
      <div className="rounded-md border bg-card p-3">
        <StackedChart data={timeseries} palette={palette} />
      </div>
      <table className="w-full text-xs tabular-nums">
        <thead>
          <tr className="text-muted-foreground">
            {columns.map((c) => (
              <th
                key={c.key}
                className={c.align === 'right' ? 'text-right px-2 py-1.5' : 'text-left px-2 py-1.5'}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {visible.map((row) => (
            <tr key={row.bucket} className="border-b border-border/40 last:border-0">
              {columns.map((c) => {
                const content = c.render
                  ? c.render(row)
                  : (row as unknown as Record<string, unknown>)[c.key]
                return (
                  <td
                    key={c.key}
                    className={c.align === 'right' ? 'text-right px-2 py-1.5' : 'px-2 py-1.5'}
                  >
                    {content as React.ReactNode}
                  </td>
                )
              })}
            </tr>
          ))}
          {!showAll && otherCount > 0 && (
            <tr>
              <td
                colSpan={columns.length}
                className="text-center text-muted-foreground italic py-2 cursor-pointer hover:underline"
                onClick={() => setShowAll(true)}
              >
                {t('showAll', { count: otherCount })}
              </td>
            </tr>
          )}
          {showAll && tableRows.length > topN && (
            <tr>
              <td
                colSpan={columns.length}
                className="text-center text-muted-foreground italic py-2 cursor-pointer hover:underline"
                onClick={() => setShowAll(false)}
              >
                {t('showLess')}
              </td>
            </tr>
          )}
        </tbody>
      </table>
      {/* unused totalCost guards against an unused-var lint; kept to allow share calcs in callers */}
      <span className="hidden">{totalCost}</span>
    </section>
  )
}
```

- [ ] **Step 13.3: Cache section**

`components/admin/insights/cost/CacheSection.tsx`:

```typescript
'use client'

import { useTranslations } from 'next-intl'
import type { CostSummaryResponse, TimeseriesResponse } from '@cubeplex/core'
import { RateChart, type RateSeries } from './RateChart'
import { computeCacheHitRate, formatPercent } from '@/lib/cost/helpers'

interface Props {
  timeseriesByModel: TimeseriesResponse
  summary: CostSummaryResponse
  palette: string[]
}

export function CacheSection({ timeseriesByModel, summary, palette }: Props) {
  const t = useTranslations('adminInsights.cost')

  const series: RateSeries[] = timeseriesByModel.series.slice(0, palette.length).map((s, i) => ({
    bucket: s.bucket,
    color: palette[i],
    points: s.points.map((p) => ({
      date: p.date,
      rate: computeCacheHitRate({ input: p.input_tokens, cacheRead: p.cache_read_tokens }),
    })),
  }))

  // Org-avg per day: sum cache_read and input across all series
  const dateMap: Record<string, { cr: number; inp: number }> = {}
  timeseriesByModel.series.forEach((s) =>
    s.points.forEach((p) => {
      const v = (dateMap[p.date] = dateMap[p.date] ?? { cr: 0, inp: 0 })
      v.cr += p.cache_read_tokens
      v.inp += p.input_tokens
    }),
  )
  const orgAvg = Object.entries(dateMap)
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([date, v]) => ({
      date,
      rate: computeCacheHitRate({ input: v.inp, cacheRead: v.cr }),
    }))

  return (
    <section className="space-y-2 mt-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold">
          {t('cacheEfficiency')}{' '}
          <span className="font-normal text-muted-foreground">
            — {t('cacheEfficiencyHint')}
          </span>
        </h2>
      </div>
      <div className="rounded-md border bg-card p-3">
        <RateChart series={series} orgAvg={orgAvg} />
      </div>
      <table className="w-full text-xs tabular-nums">
        <thead>
          <tr className="text-muted-foreground">
            <th className="text-left px-2 py-1.5">{t('columns.model')}</th>
            <th className="text-right px-2 py-1.5">{t('columns.cacheReads')}</th>
            <th className="text-right px-2 py-1.5">{t('columns.cacheWrites')}</th>
            <th className="text-right px-2 py-1.5">{t('columns.uncachedInput')}</th>
            <th className="text-right px-2 py-1.5">{t('columns.hitRate')}</th>
          </tr>
        </thead>
        <tbody>
          {summary.by_model.map((r) => {
            const rate = computeCacheHitRate({
              input: r.input_tokens, cacheRead: r.cache_read_tokens,
            })
            return (
              <tr key={r.bucket} className="border-b border-border/40 last:border-0">
                <td className="px-2 py-1.5 font-mono text-[11px]">{r.bucket}</td>
                <td className="text-right px-2 py-1.5">{r.cache_read_tokens.toLocaleString()}</td>
                <td className="text-right px-2 py-1.5">{r.cache_write_tokens.toLocaleString()}</td>
                <td className="text-right px-2 py-1.5">{r.input_tokens.toLocaleString()}</td>
                <td className="text-right px-2 py-1.5">{formatPercent(rate, 0)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </section>
  )
}
```

- [ ] **Step 13.4: Shell — composes everything**

`components/admin/insights/InsightsShell.tsx`:

```typescript
'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { useCostData, type CostFilters } from '@/hooks/useCostData'
import { InsightsTopBar } from './InsightsTopBar'
import { InsightsFilterSidebar } from './InsightsFilterSidebar'
import { KpiRow } from './cost/KpiRow'
import { StackedSection, type Column, type SummaryRow } from './cost/StackedSection'
import { CacheSection } from './cost/CacheSection'
import type { CostAggregateRow, TimeseriesResponse } from '@cubeplex/core'

const PALETTE_WORKSPACE = ['#1e40af', '#1d4ed8', '#2563eb', '#3b82f6', '#60a5fa', '#93c5fd', '#bfdbfe']
const PALETTE_MODEL = ['#166534', '#15803d', '#16a34a', '#22c55e', '#4ade80', '#86efac', '#bbf7d0']
const PALETTE_USER = ['#6b21a8', '#7e22ce', '#9333ea', '#a855f7', '#c084fc', '#d8b4fe', '#e9d5ff']
const PALETTE_CACHE = ['#b45309', '#d97706', '#f59e0b', '#fbbf24', '#fcd34d']

/** Server returns up to 25 series; chart caps further to keep visual density sane. */
function capTimeseries(ts: TimeseriesResponse, n: number): TimeseriesResponse {
  if (ts.series.length <= n) return ts
  const ranked = [...ts.series].sort((a, b) => {
    const sumOf = (s: typeof a) => s.points.reduce((acc, p) => acc + p.cost_amount_micro, 0)
    return sumOf(b) - sumOf(a)
  })
  const keep = ranked.slice(0, n - 1)
  const rest = ranked.slice(n - 1)
  const dateMap: Record<string, { cost: number; calls: number; input: number; output: number; cr: number; cw: number }> = {}
  rest.forEach((s) =>
    s.points.forEach((p) => {
      const v = (dateMap[p.date] = dateMap[p.date] ?? { cost: 0, calls: 0, input: 0, output: 0, cr: 0, cw: 0 })
      v.cost += p.cost_amount_micro
      v.calls += p.calls
      v.input += p.input_tokens
      v.output += p.output_tokens
      v.cr += p.cache_read_tokens
      v.cw += p.cache_write_tokens
    }),
  )
  const dates = [...new Set(rest.flatMap((s) => s.points.map((p) => p.date)))].sort()
  const otherSeries = {
    bucket: '__other',
    currency: ts.currency,
    points: dates.map((date) => ({
      date,
      cost_amount_micro: dateMap[date]?.cost ?? 0,
      calls: dateMap[date]?.calls ?? 0,
      input_tokens: dateMap[date]?.input ?? 0,
      output_tokens: dateMap[date]?.output ?? 0,
      cache_read_tokens: dateMap[date]?.cr ?? 0,
      cache_write_tokens: dateMap[date]?.cw ?? 0,
    })),
  }
  return { ...ts, series: [...keep, otherSeries] }
}

function aggRowToSummaryRow(r: CostAggregateRow): SummaryRow {
  return {
    bucket: r.bucket,
    cost_amount_micro: r.cost_amount_micro,
    call_count: r.call_count,
    input_tokens: r.input_tokens,
    output_tokens: r.output_tokens,
    cache_read_tokens: r.cache_read_tokens,
    cache_write_tokens: r.cache_write_tokens,
    currency: r.currency,
  }
}

function fmtUsd(micro: number): string {
  return `$${(micro / 1_000_000).toFixed(2)}`
}

export function InsightsShell() {
  const t = useTranslations('adminInsights.cost')
  const [filters, setFilters] = useState<CostFilters>({
    range: '30d',
    workspaceIds: [],
    models: [],
    granularity: 'day',
  })
  const data = useCostData(filters)

  const availableWorkspaces = useMemo(
    () => (data.summary?.by_workspace ?? []).map((r) => ({ id: r.bucket, name: r.bucket })),
    [data.summary],
  )
  const availableModels = useMemo(
    () => (data.summary?.by_model ?? []).map((r) => r.bucket),
    [data.summary],
  )

  const rangeDays =
    typeof filters.range === 'string'
      ? filters.range === '7d' ? 7 : filters.range === '30d' ? 30 : 90
      : 30

  return (
    <div className="flex flex-col h-full">
      <InsightsTopBar
        fromDate={data.summary?.from_date ?? '…'}
        toDate={data.summary?.to_date ?? '…'}
      />
      <div className="flex flex-1 min-h-0">
        <InsightsFilterSidebar
          filters={filters}
          onChange={setFilters}
          availableWorkspaces={availableWorkspaces}
          availableModels={availableModels}
        />
        <div className="flex-1 overflow-y-auto p-4 space-y-6">
          {data.error && (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 text-sm p-3">
              {data.error}
            </div>
          )}
          {data.summary && (
            <KpiRow summary={data.summary} priorSummary={data.priorSummary} rangeDays={rangeDays} />
          )}
          {data.summary && data.byWorkspace && (
            <StackedSection
              title={t('byWorkspace')}
              timeseries={capTimeseries(data.byWorkspace, 10)}
              tableRows={data.summary.by_workspace.map(aggRowToSummaryRow)}
              palette={PALETTE_WORKSPACE}
              topN={10}
              columns={defaultCostColumns(t, 'workspace')}
            />
          )}
          {data.summary && data.byModel && (
            <StackedSection
              title={t('byModel')}
              timeseries={capTimeseries(data.byModel, 10)}
              tableRows={data.summary.by_model.map(aggRowToSummaryRow)}
              palette={PALETTE_MODEL}
              topN={10}
              columns={defaultCostColumns(t, 'model')}
            />
          )}
          {data.summary && data.byUser && (
            <StackedSection
              title={t('byUser')}
              timeseries={capTimeseries(data.byUser, 8)}
              tableRows={data.summary.by_user.map(aggRowToSummaryRow)}
              palette={PALETTE_USER}
              topN={8}
              columns={defaultCostColumns(t, 'user')}
            />
          )}
          {data.summary && data.byModel && (
            <CacheSection
              timeseriesByModel={capTimeseries(data.byModel, 5)}
              summary={data.summary}
              palette={PALETTE_CACHE}
            />
          )}
        </div>
      </div>
    </div>
  )
}

function defaultCostColumns(
  t: ReturnType<typeof useTranslations>,
  kind: 'workspace' | 'model' | 'user',
): Column[] {
  const base: Column[] = [
    { key: 'bucket', label: t(`columns.${kind}`) },
    { key: 'call_count', label: t('columns.calls'), align: 'right',
      render: (r) => r.call_count.toLocaleString() },
    { key: 'input_tokens', label: t('columns.input'), align: 'right',
      render: (r) => r.input_tokens.toLocaleString() },
    { key: 'output_tokens', label: t('columns.output'), align: 'right',
      render: (r) => r.output_tokens.toLocaleString() },
  ]
  if (kind === 'model') {
    base.push({
      key: 'cache_rw', label: t('columns.cacheRw'), align: 'right',
      render: (r) =>
        `${(r.cache_read_tokens / 1e6).toFixed(2)}M / ${(r.cache_write_tokens / 1e6).toFixed(2)}M`,
    })
  }
  base.push({
    key: 'cost_amount_micro', label: t('columns.cost'), align: 'right',
    render: (r) => fmtUsd(r.cost_amount_micro),
  })
  return base
}
```

- [ ] **Step 13.5: Type-check end-to-end**

```bash
cd frontend && pnpm type-check
```

Fix any complaints. Common issues: imports that should come from `@cubeplex/core`, missing `'use client'` directive, `useTranslations` return type inference.

- [ ] **Step 13.6: Boot dev server and smoke-test**

```bash
cd frontend && pnpm dev
```

Open `http://localhost:3027/admin/insights` (worktree port, NOT 3000), register an admin user if needed, verify the page renders with the KPI row + four sections. Click each filter chip and confirm the URL stays stable while sections re-fetch.

If `/admin/cost` is hit (e.g. an existing tab), confirm it redirects to `/admin/insights`.

- [ ] **Step 13.7: Commit**

```bash
git add frontend/packages/web/components/admin/insights/
git commit -m "feat(insights): KPI row + stacked sections + cache section + shell"
```

---

### Task 14: E2E rename and extend

**Files:**
- Rename: `frontend/packages/web/__tests__/e2e/admin-cost.spec.ts` → `admin-insights.spec.ts`

- [ ] **Step 14.1: Rename via git**

```bash
git mv frontend/packages/web/__tests__/e2e/admin-cost.spec.ts \
       frontend/packages/web/__tests__/e2e/admin-insights.spec.ts
```

- [ ] **Step 14.2: Rewrite tests**

Replace the file contents:

```typescript
import { test, expect } from '@playwright/test'

function uniqueEmail(): string {
  return `u-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
}

const PASSWORD = 'correcthorsebatterystaple'

async function registerAs(page: import('@playwright/test').Page, email: string): Promise<void> {
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })
}

test.describe('Admin Insights page', () => {
  test('insights page renders KPI row and four sections', async ({ page }) => {
    await registerAs(page, uniqueEmail())
    await page.goto('/admin/insights')
    await expect(page.getByRole('heading', { name: 'Insights' })).toBeVisible({
      timeout: 10_000,
    })
    await expect(page.getByText('Total cost')).toBeVisible()
    await expect(page.getByText('Cache hit rate')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'By workspace' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'By model' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'By user' })).toBeVisible()
    await expect(page.getByRole('heading', { name: /Cache efficiency/ })).toBeVisible()
  })

  test('legacy /admin/cost redirects to /admin/insights', async ({ page }) => {
    await registerAs(page, uniqueEmail())
    await page.goto('/admin/cost')
    await expect(page).toHaveURL(/\/admin\/insights$/)
    await expect(page.getByRole('heading', { name: 'Insights' })).toBeVisible({
      timeout: 10_000,
    })
  })

  test('granularity toggle changes URL state', async ({ page }) => {
    await registerAs(page, uniqueEmail())
    await page.goto('/admin/insights')
    await expect(page.getByRole('heading', { name: 'Insights' })).toBeVisible({
      timeout: 10_000,
    })
    await page.getByRole('button', { name: /^week$/ }).click()
    // chart re-renders; assert no error banner
    await expect(page.getByText(/load failed/i)).not.toBeVisible()
  })

  test('export CSV link returns csv content-type', async ({ page, request }) => {
    await registerAs(page, uniqueEmail())
    await page.goto('/admin/insights')
    await expect(page.getByRole('heading', { name: 'Insights' })).toBeVisible({
      timeout: 10_000,
    })
    const cookies = await page.context().cookies()
    const cookieStr = cookies.map((c) => `${c.name}=${c.value}`).join('; ')
    const resp = await request.get('/api/v1/admin/cost/export.csv', {
      headers: { Cookie: cookieStr },
    })
    expect(resp.status()).toBe(200)
    expect(resp.headers()['content-type']).toContain('text/csv')
  })

  test('Insights nav item appears in admin sidebar', async ({ page }) => {
    await registerAs(page, uniqueEmail())
    await page.goto('/admin')
    await expect(page.getByRole('heading', { name: 'Admin' })).toBeVisible({ timeout: 10_000 })
    const nav = page.getByRole('navigation', { name: /admin sub-nav/i })
    await expect(nav.getByRole('link', { name: 'Insights' })).toBeVisible()
  })
})
```

- [ ] **Step 14.3: Run the spec**

```bash
cd frontend && pnpm test:e2e __tests__/e2e/admin-insights.spec.ts
```

Expected: all five tests pass. If the dev server isn't already running on the worktree port, the Playwright config will start one. Verify the report URL.

- [ ] **Step 14.4: Run full type-check + lint sweep**

```bash
cd frontend && pnpm type-check
cd .. # back to worktree root
```

- [ ] **Step 14.5: Run the full backend test for the cost surface**

```bash
cd backend && uv run pytest tests/e2e/test_billing_cost_api.py tests/e2e/test_billing.py -v
```

- [ ] **Step 14.6: Commit**

```bash
git add frontend/packages/web/__tests__/e2e/admin-insights.spec.ts
git commit -m "test(e2e): admin-insights spec with redirect + 4-section + nav coverage"
```

---

## Wrap-up

- [ ] **Step W1: Final lint sweep**

```bash
cd backend && make check
cd ../frontend && pnpm type-check
```

Both must be clean.

- [ ] **Step W2: Run targeted regressions**

```bash
# Backend cost surface
cd backend && uv run pytest tests/e2e/test_billing.py tests/e2e/test_billing_cost_api.py -v

# Frontend admin E2E (insights + adjacent admin pages)
cd ../frontend && pnpm test:e2e __tests__/e2e/admin-insights.spec.ts __tests__/e2e/admin-console-skeleton.spec.ts
```

- [ ] **Step W3: Manual smoke-check**

Start both servers in the worktree:

```bash
cd backend && python main.py &     # listens on :8027
cd ../frontend && pnpm dev          # listens on :3027
```

Visit `http://localhost:3027/admin/insights` — verify all four sections render, granularity toggles work, workspace/model chips filter, and the CSV export button returns a download.

Stop the servers when done.

- [ ] **Step W4: Final commit / branch hygiene**

```bash
git status        # should be clean
git log --oneline origin/main..HEAD   # review the commit train
```

- [ ] **Step W5: Hand off**

Invoke `superpowers:finishing-a-development-branch` to choose how to integrate the work (merge / PR / cleanup). Do not switch branches or open a PR without the user's call.
