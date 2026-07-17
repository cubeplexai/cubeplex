# Admin Insights Page (cost sections v1)

**Status:** Draft
**Date:** 2026-05-12
**Worktree:** `feat/admin-cost-redesign`
**Predecessor:** `2026-04-28-cost-tracking-design.md` (v1 cost tracking)

## Goal

Replace the current bare-bones admin cost page (two summary cards + two tables)
with **Admin Insights** — a long-term home for org-level admin statistics.
This first iteration lays down the page shell and fills it with cost-related
sections: a faceted explorer that lets an org admin (a) see total spend at a
glance, (b) understand spend trend over time, (c) slice by workspace / model /
user, and (d) inspect cache efficiency, without leaving the page.

The page is named **Insights** rather than **Cost** because subsequent
iterations will add non-cost sections (active users, agent runs, error rates,
etc.). Cost is just the first content area.

## Non-goals

- No new pricing or billing logic. The cost numbers come from the existing
  `BillingEvent` table and `BillingRepository` aggregations.
- No `by skill` / `by tool` / `by conversation` dimensions in this iteration.
  v1 had no skill execution boundary (see existing project memory) and
  per-conversation drill-down is a separate, larger surface.
- No alerting / budgets / forecasts. This is a viewer, not a controller.
- No CSV/UX overhaul of the per-workspace export beyond what the current
  endpoints already give us.

## Current state

`frontend/packages/web/app/admin/cost/page.tsx` calls `GET /cost/summary` and
renders two KPI cards (total cost, total calls) plus two flat tables (by
workspace, by model). The summary response already includes a `by_day`
breakdown that the page silently discards. CSV export buttons exist per-row
and at the top.

Backend (`backend/cubeplex/api/routes/v1/cost.py`):
- `GET /cost/summary` → totals + `by_workspace` + `by_model` + `by_day`
  (all single-dimension aggregates)
- `GET /cost/by-workspace/{ws}` → second-level drill (group_by day | user |
  model inside a workspace)
- `GET /cost/export.csv`, `GET /cost/by-workspace/{ws}/export.csv`

`BillingRepository.get_org_spend(group_by=...)` already supports `workspace |
user | model | day`, so adding `by_user` at the summary level is a one-line
schema/route change.

## Information architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Top bar    Insights · May 1 — May 12 · org acme            [📅] [⬇CSV]  │
├──────────────────┬──────────────────────────────────────────────────────┤
│  Sidebar         │  KPI row (5 cards)                                   │
│                  │  ─ Total cost   Total calls   Avg/call   Cache hit  │
│  Range           │    rate   Active users                              │
│   [7  30  90]    │                                                      │
│                  │  Section · By workspace                              │
│  Workspaces      │  ┌─ stacked area chart (workspaces × day) ──────┐    │
│   ☑ acme-prod    │  └─────────────────────────────────────────────┘    │
│   ☑ research     │  Table: workspace | calls | input | output | cost   │
│   ☐ experiments  │         | share                                      │
│                  │                                                      │
│  Models          │  Section · By model                                  │
│   ☑ gpt-4o       │  ┌─ stacked area chart (models × day) ──────────┐    │
│   ☑ sonnet-4     │  └─────────────────────────────────────────────┘    │
│   ☐ haiku        │  Table: model | calls | input | output | cache R/W  │
│                  │         | cost | share                               │
│  Granularity     │                                                      │
│   [day | week]   │  Section · By user                                   │
│                  │  ┌─ stacked area chart (users × day) ───────────┐    │
│                  │  └─────────────────────────────────────────────┘    │
│                  │  Table: user | calls | input | output | cost |      │
│                  │         share  (collapsed beyond top N)             │
│                  │                                                      │
│                  │  Section · Cache efficiency                          │
│                  │  ┌─ multi-line chart (hit rate × day, per model) ┐   │
│                  │  └────────────────────────────────────────────── ┘   │
│                  │  Table: model | cache reads | writes | uncached    │
│                  │         input | hit rate | est. savings             │
└──────────────────┴──────────────────────────────────────────────────────┘
```

### Sidebar facets

- **Range**: segmented control, presets `7d / 30d / 90d`. Default `30d`.
  Clicking "📅" in the top bar opens a custom date range picker that overrides
  the preset. Range is the only required facet — all the others default to
  "all".
- **Workspaces**: chip multi-select. Empty selection = all workspaces.
- **Models**: chip multi-select (provider/model id). Empty selection = all.
- **Granularity**: `day | week`. Drives the time bucket in the timeseries
  endpoint.

User filtering is intentionally absent from the sidebar — the user dimension
is read-only output, not an input. Filtering by user is a future iteration if
needed.

### KPI row

Five tiles, each with current value + delta vs the prior equivalent window
(same length, immediately preceding):

| KPI                | Source                                                   |
| ------------------ | -------------------------------------------------------- |
| Total cost         | sum(cost_amount_micro) across filtered events            |
| Total calls        | sum(call_count)                                          |
| Avg / call         | total_cost / total_calls                                 |
| Cache hit rate     | sum(cache_read) / sum(cache_read + input_tokens)         |
| Active users       | distinct user_id with at least one call in the window    |

Deltas are simple percentage change vs the prior window. Color: red for cost
deltas going up, green for cache hit rate going up, grey for "no change"
(below ±1 pp or ±1%).

### Sections

Each of the four sections has the same skeleton: heading + legend + chart +
table. The first three are stacked area charts (sum); the fourth is a
multi-line chart (rate). All four sections share the same x-axis (filtered
range × granularity).

**Top-N policy.** Each section shows the top N buckets by cost; the rest
collapse into an "Other" series in the chart and an expandable "+N more" row
in the table.

- By workspace: N = 10 (orgs rarely have more)
- By model: N = 10
- By user: N = 8 visible in table; clicking the "+M more · show all" row
  expands the table in-place to show every user. The chart caps at N=8 +
  Other regardless of the table state (more lines would be noise)

**Cache hit rate** definition: per bucket,
`cache_read_tokens / (cache_read_tokens + input_tokens)`. Cache-write tokens
are not counted in the denominator — they are billed at a separate rate but
they aren't "input that could have been a hit". A model with no input in the
period reports `null`, rendered as `—`.

**Est. savings** in the cache table: lower bound, computed as
`cache_read_tokens × (model.input_price − model.cache_read_price)`. The model
prices come from the same pricing config the backend already uses to compute
`cost_amount_micro`. If pricing config is missing for a model, savings is
`null`.

## API changes

### Extend `GET /cost/summary` response

Add `by_user` to the response, mirroring `by_workspace` and `by_model`. The
repository already supports `group_by="user"`; only the route and pydantic
schema need updating.

```python
# backend/cubeplex/api/schemas/billing.py
class CostSummaryResponse(BaseModel):
    from_date: date
    to_date: date
    total_cost_amount_micro: int
    currency: str
    total_calls: int
    by_workspace: list[CostAggregateRow]
    by_model: list[CostAggregateRow]
    by_user: list[CostAggregateRow]   # NEW
    by_day: list[CostAggregateRow]
```

### New `GET /cost/timeseries`

For the stacked area + cache-rate charts the frontend needs a 2D aggregation
(bucket × day). Adding a single new endpoint is cleaner than fanning out N
calls to `/by-workspace/{ws}?group_by=day` from the client.

```
GET /cost/timeseries?dimension=workspace|model|user
                   &from=YYYY-MM-DD&to=YYYY-MM-DD
                   &granularity=day|week
                   &workspace_ids=ws1,ws2          (optional filter)
                   &models=openai/gpt-4o,...        (optional filter)
```

Response:

```json
{
  "from_date": "2026-05-01",
  "to_date": "2026-05-12",
  "granularity": "day",
  "dimension": "workspace",
  "buckets": ["acme-prod", "research", "experiments"],
  "series": [
    {
      "bucket": "acme-prod",
      "points": [
        {"date": "2026-05-01", "cost_amount_micro": 31200000, "calls": 540,
         "input_tokens": 720000, "output_tokens": 95000,
         "cache_read_tokens": 240000, "cache_write_tokens": 60000},
        ...
      ]
    },
    ...
  ],
  "currency": "USD"
}
```

Repository implementation: extend `BillingRepository` with
`get_timeseries(dimension, since, until, granularity, ...)` that adds the
day-bucket column to the existing single-dimension aggregator (group by
`(dimension_col, day_bucket, currency)`).

The same response shape serves all four chart sections:
- Sections 1–3 stack `cost_amount_micro` per series
- Section 4 derives hit rate per point: `cache_read / (cache_read + input)`

Filters: `workspace_ids` and `models` are applied to the underlying events
before grouping. They mirror the sidebar facets.

### Top-N at the API boundary

The timeseries endpoint returns at most 25 series. If the dimension has more
buckets, the smallest-cost ones are collapsed into a single
`bucket = "__other"` series. The frontend renders that as grey with the label
"Other (N items)".

### CSV export

Unchanged. The existing per-org and per-workspace CSV exports already cover
both the cost-per-event and the granularity needed for spreadsheets.

## Frontend architecture

### Route and shell

- Route moves from `/admin/cost` to `/admin/insights`. The old `/admin/cost`
  redirects to `/admin/insights` (a one-line `redirect()` in
  `app/admin/cost/page.tsx`) so existing bookmarks don't 404. The redirect
  is removed in a later cleanup once links are updated.
- `AdminSubNav` label changes from "Cost" to "Insights"; the icon stays
  `BarChart3` (was `CircleDollarSign`).
- Page-level loading and error states live in `page.tsx`. Lower-level error
  states (per-section "couldn't load this slice") live in the section
  components.

### Component tree

```
app/admin/insights/page.tsx
└── components/admin/insights/
    ├── InsightsShell.tsx               orchestrates filters + data
    ├── InsightsFilterSidebar.tsx       sidebar (range, ws, model, granularity)
    ├── InsightsTopBar.tsx              title + date range button + CSV
    └── cost/                           cost-specific section bundle
        ├── CostKpiRow.tsx              5 KPI tiles
        ├── CostStackedSection.tsx      generic stacked area + table
        │     (props: dimension, title, columns, data)
        ├── CostCacheSection.tsx        multi-line + cache table
        ├── CostStackedChart.tsx        recharts wrapper, top-N + Other
        └── CostRateChart.tsx           multi-line, y-axis 0–100%
```

Shell + sidebar + top bar are page-level (will be reused as new section
families land). The `cost/` subfolder bundles everything that is specifically
about cost — future additions like `users/` or `agents/` follow the same
pattern.

`CostStackedSection` is parameterised by `dimension` so the three sections
share rendering — they differ only in title, color palette, and the table
columns (cache R/W column only appears in the model table).

### Data fetching

A single `useCostData(filters)` hook in `packages/web/hooks/useCostData.ts`
(following the existing `useAdminAccess` / `useAllModels` pattern — hooks
live in web, not core) runs four requests in parallel whenever filters
change:

1. `GET /cost/summary` → KPI row + table totals
2. `GET /cost/timeseries?dimension=workspace`
3. `GET /cost/timeseries?dimension=model`
4. `GET /cost/timeseries?dimension=user`

Filters serialise to URL query params; the page state lives in the URL so
admins can share filtered views. `useCostData` keys its cache on the
serialised filter object; stale-while-revalidate behaviour comes from a small
in-memory cache, not from adding `swr` or `react-query` (the rest of the app
doesn't use them).

The KPI deltas need a second `/cost/summary` call against the immediately
prior window. This is also part of `useCostData`; results are merged into a
single `CostOverviewData` value the components consume.

### Charting library

The frontend has no chart library today (`TokenUsageBar` is CSS bars). This
redesign adds **`recharts`** to `packages/web/package.json`. Reasons:

- de-facto React chart lib, MIT, ~80 KB gzipped
- supports stacked area and multi-line out of the box with built-in tooltip
  + legend
- no other chart-heavy surface is planned, so we don't need to pick a
  framework — recharts is the smallest viable dep

Hand-rolling SVG (as in the mockups) would also work for static rendering,
but tooltips with per-day precise values per series are tedious to
hand-build and the admin will use them. Adding the dep is the right call.

### Top-N + "Other" rendering

The backend collapses to at most 25 series. The frontend additionally caps
the chart at top 10 (workspace, model) or top 8 (user) by total cost over the
window. Everything below the cap merges into one `__other` series, rendered
grey. The table follows the same cap, with an expandable row.

### Empty / loading / error states

- **Loading**: skeletons for the KPI row + four chart-shaped placeholders.
  No spinner overlay; the page renders the shell immediately so filter
  changes don't blank the screen.
- **Empty**: when a section has zero data (e.g. cache hit rate before any
  caching providers were used), render a one-line placeholder inside the
  chart frame ("No cache events in this period"), not an error. The KPI cell
  shows `—`.
- **Per-section error**: if `/timeseries?dimension=X` fails but the others
  succeed, that section shows an inline error ("Couldn't load X. Retry") and
  the rest of the page stays usable.
- **Top-level error**: only when `/cost/summary` fails. Replaces the page
  body with the existing error treatment.

### Permissions

The admin layout already gates on `require_org_admin`. No frontend permission
work here.

### i18n

The i18n namespace becomes `adminInsights`. `messages/{en,zh}.json` gains a
new `adminInsights` block; the legacy `adminCost` block is dropped (no other
caller). New keys: `heading` ("Insights"), `cost.byWorkspace`,
`cost.byModel`, `cost.byUser`, `cost.cacheEfficiency`, `cost.cacheHitRate`,
`cost.activeUsers`, `cost.avgPerCall`, `cost.cacheReads`, `cost.cacheWrites`,
`cost.uncachedInput`, `cost.hitRate`, `cost.estSavings`, `cost.other`,
`cost.noData`, `cost.retry`, plus per-section legend labels. The `cost.*`
prefix keeps room for future top-level sections (`users.*`, `agents.*`).
The `AdminSubNav` label key becomes `nav.insights`.

### Visual tokens

- Sidebar: `bg-card`, 200px wide on `>=lg`, collapses to a top-row filter bar
  on `<lg` (mobile is unlikely for admin but we don't actively break).
- KPI tiles: `bg-card border rounded-md p-3`, `text-lg font-semibold
  tabular-nums` for the value, muted small caps for the label.
- Chart sections: `border rounded-md p-4`, table inside with
  `border-collapse` and dashed row separators.
- Color palettes per section follow the mockup:
  - Workspace: blues (indigo-900 → blue-300, 10 ramped)
  - Model: greens (emerald-900 → emerald-300)
  - User: purples (purple-900 → fuchsia-300)
  - Cache: ambers (amber-800 → amber-300) for the line chart; org-avg dashed

Numeric cells everywhere use `font-variant-numeric: tabular-nums`.

## Testing

Per project conventions, E2E is the priority.

### Backend E2E

`backend/tests/e2e/test_cost_api.py` (extends existing file if present):

- `/cost/summary` returns `by_user` populated with one row per active user
  in the window
- `/cost/timeseries?dimension=workspace` returns one series per workspace
  with one point per day in the range, zero-padded for days with no events
- `/cost/timeseries?dimension=user&workspace_ids=ws1` filters correctly
- `/cost/timeseries?granularity=week` aggregates correctly
- Top-N collapse: when more than 25 buckets exist, the smallest collapse to
  `bucket="__other"` and totals are preserved

Negative paths:
- non-admin gets 403 (reuse `_require_org_admin` test setup)
- invalid `dimension` → 400
- invalid date format → 400

### Frontend E2E

Existing `__tests__/e2e/admin-cost.spec.ts` is renamed to
`__tests__/e2e/admin-insights.spec.ts` and extended:

- Admin lands on `/admin/insights`; KPI row shows 5 tiles with non-zero
  values for a seeded org
- Old `/admin/cost` URL redirects to `/admin/insights` (preserves bookmarks)
- Selecting a workspace chip filters all four sections; URL query updates
- Switching granularity from `day` to `week` changes x-axis tick count
- Cache section: hit rate appears as a percentage; "Est. savings" cell
  renders when pricing config is present
- The CSV export link still produces a download
- Non-admin (a member user) hitting `/admin/insights` gets redirected
  (existing behaviour, regression check)

### Unit tests

Only for the few pure helpers: `computeCacheHitRate`, `topNWithOther`. No
unit tests for components — the E2E covers the user-visible behaviour.

## Migration / rollout

- No database changes. No migration.
- Backend route changes are additive (one new endpoint, one new field). The
  old `/cost/summary` shape gains `by_user`; existing clients tolerate extra
  fields.
- Frontend ships as a single PR. New page at `app/admin/insights/page.tsx`;
  `app/admin/cost/page.tsx` becomes a redirect stub. AdminSubNav swaps the
  link label and icon. No feature flag — the old page has minimal traffic
  (admin only) and the new page is strictly a superset.

## Open questions (deliberately deferred)

- **Pricing config for cache savings**: relies on a model price table the
  backend uses to compute `cost_amount_micro`. If a model is missing from
  that table, savings reports `null`. We are not adding a UI to manage that
  table in this iteration.
- **Multi-currency**: the v1 cost tracking design assumes a single currency
  per org. This redesign preserves that assumption; the KPI row picks the
  modal currency in `by_workspace` and falls back to `USD`.
- **Time zone**: the existing `/summary` aggregates by UTC day. We keep
  that. Showing tooltips in the admin's local zone is a polish task we are
  not doing now.
- **Real-time refresh**: the page does not auto-refresh. Admins use the
  `📅` button to re-run with the same range and pick up new data. Live
  push (SSE) is out of scope.
- **Custom date range picker UI**: `useCostData.filters.range` accepts
  `{from, to}`, but only the `7d / 30d / 90d` preset segmented control is
  shipped in this iteration. A custom date picker behind the `📅` icon is
  a follow-up.

## Future optimizations

- **TimescaleDB / continuous aggregates**: not introduced now. Current
  scale (a single org producing a few thousand `billing_events` per day)
  is comfortably handled by vanilla PostgreSQL with an
  `(org_id, started_at)` index on `billing_events`. Worth revisiting if
  any of the following becomes true: events reach 10M+ rows per org,
  `get_org_spend` / `get_timeseries` p95 query latency exceeds ~300 ms,
  or we need scheduled rollups / retention / column compression.
  Migrating later is a self-contained change: install the extension,
  convert `billing_events` to a hypertable, materialize the per-day-per-
  bucket aggregations as continuous aggregates, point the repository at
  them. The frontend would not see any change.
