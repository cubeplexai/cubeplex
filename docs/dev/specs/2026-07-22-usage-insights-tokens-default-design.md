# Usage insights: tokens default, toggle to cost

## Goal

Make admin **Insights** useful without model pricing: **default the primary
metric to tokens**, keep a clear **Tokens | Cost** toggle for deployments
that set prices, and persist the choice lightly in the browser.

## Context

`/admin/insights` is cost-first today:

- `KpiRow` leads with total USD cost and avg $/call.
- `StackedChart` / `StackedSection` rank and plot `cost_amount_micro`.
- Token fields exist on summary rows and timeseries points but are secondary
  table columns only.

When `price_*_per_mtok` is unset, billing still records token buckets but
`cost_amount_micro` is 0 (`backend/cubeplex/middleware/cost.py`). Operators
see a flat $0 dashboard despite real traffic.

Chat already emphasizes tokens (`TokenUsageBar`). Insights should match.

### Data already available

Frontend types (`@cubeplex/core` billing types):

- `CostAggregateRow`: `input_tokens`, `output_tokens`, `cache_*`,
  `cost_amount_micro`, `call_count`, …
- `TimeseriesPoint`: same token fields **and** `cost_amount_micro`

Backend `BillingRepository` aggregates and timeseries already sum token
columns. **No new aggregate API is required** if the UI switches value
accessors. Confirm during implementation that every chart path uses
points that include tokens (they do on the typed response today).

### Surfaces

| Component | Role |
| --- | --- |
| `InsightsShell` | Wires filters + cost sections |
| `InsightsTopBar` | Title + CSV export |
| `KpiRow` | Cost-first KPI tiles |
| `StackedSection` / `StackedChart` | Rank + plot by cost |
| `CacheSection` | Already token-based hit rate — keep |
| `useCostData` | Fetches summary + timeseries |

## Approaches considered

**A. Tokens default + metric toggle (recommended)**  
UI state `metric: 'tokens' | 'cost'`; parameterize KPI/chart/table value
functions. Frontend-only.

**B. Tokens-only dashboard**  
Drops cost view; bad for orgs that do set prices.

**C. Backend “usage summary” redesign**  
Unnecessary; data is already dual-metric.

**Chosen: A.**

## Design

### Metric toggle

- Placement: **Insights top bar** (next to heading / export) as a compact
  segmented control: **Tokens** (default) | **Cost**.
- Accessibility: `role="tablist"` / `role="tab"` or radiogroup with clear
  labels.
- Default for first visit: **`tokens`**.
- Persist: `localStorage` key e.g. `cubeplex.insights.metric`
  (`'tokens' | 'cost'`). Invalid/missing → tokens.

### Token definition (locked)

| Concept | Formula |
| --- | --- |
| **Primary total tokens** | `input_tokens + output_tokens` |
| Cache | Keep separate via **Cache** section / cache columns; do **not** add cache write into primary total |
| Input display | Use stored `input_tokens` as billing already defines (align with existing table columns / chat conventions; no re-derivation) |

### Tokens mode (default)

**KPIs (suggested tiles):**

| Tile | Value |
| --- | --- |
| Total tokens | sum of (input + output) over range (from `by_workspace` or dedicated totals if present) |
| Input tokens | sum `input_tokens` |
| Output tokens | sum `output_tokens` |
| Cache hit rate | keep current formula |
| Calls and/or active users | keep useful non-cost tiles |

Avg tile: **tokens per call** (`total_tokens / total_calls`) instead of
avg $/call.

**Charts / rankings:**

- Value accessor: `input_tokens + output_tokens` (or precomputed helper).
- `topNWithOther` / `capTimeseries` rank by token total.
- `StackedChart` pivot uses token totals; Y-axis / tooltip use compact
  token formatting (not `$`).

**Tables:**

- Primary sort column = tokens; cost column optional secondary or hidden
  in tokens mode.

### Cost mode

- Restore current KPIs, ranking, chart dollars, columns.
- Empty-cost hint: if `total_cost_amount_micro === 0` and token total > 0,
  show a soft callout: model prices not configured → link toward admin
  Models pricing. Do not block the page.

### Formatting

- Tokens: compact (`1.2K`, `3.4M`) + full value on tooltip. Prefer sharing
  or extracting `formatTokenCount` from `TokenUsageBar` into
  `lib/cost/helpers` or `lib/format` so chat and admin stay consistent.
- Cost: existing microdollar → currency helpers.

### i18n

- `adminInsights` keys for toggle labels, token KPI names, cost-empty
  hint. en + zh.
- Nav label rename (“Cost” → “Usage”) is **optional later**; not required
  for acceptance of the toggle.

### Scope

- Admin insights only (existing page). Workspace-member usage surface is
  out of scope.

## Out of scope

- Changing cost computation or forcing price entry
- Replacing chat `TokenUsageBar`
- Multi-currency beyond existing `currency` field
- Real-time streaming org usage
- Renaming the whole Insights IA (optional phase 3)
- Non-admin usage dashboards

## Success criteria

1. Fresh visit defaults to **token** metrics (not $0 cost as the hero).
2. Toggle switches KPIs + stacked ranking/series without full reload.
3. Zero prices + non-zero traffic → tokens mode shows meaningful non-zero
   numbers.
4. Cost mode works when prices are set; $0 + tokens>0 shows pricing hint.
5. Cache section remains valid in both modes.
6. Preference persists across reloads (localStorage).
7. en/zh strings; toggle is keyboard/accessible.

## Resolved product choices

| Question | Decision |
| --- | --- |
| Primary token total | `input + output` only |
| Default metric | tokens |
| Preference | localStorage |
| Avg in tokens mode | tokens/call |
| Nav rename to “Usage” | later optional |
| Backend API | reuse existing token fields |

## Related

- Issue #394
- `InsightsShell`, `InsightsTopBar`, `cost/*`, `useCostData`
- `TokenUsageBar` / `formatTokenCount`
- Billing types + `BillingRepository` aggregates
