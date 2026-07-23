# Usage insights tokens default — implementation plan

**Goal**: Default Insights to token metrics with a Tokens | Cost toggle;
keep cost mode behavior; persist preference.

**Architecture**: Frontend-only metric mode over existing cost/usage APIs.
Parameterize value accessors in KPI, stacked sections, and charts. No
schema or migration changes.

**Tech stack**: React Insights shell, Recharts stacked chart, next-intl,
`localStorage`, existing `@cubeplex/core` billing types.

---

## Unit 1: Metric state + toggle UI

**Files**:

- `frontend/packages/web/components/admin/insights/InsightsShell.tsx`
- `frontend/packages/web/components/admin/insights/InsightsTopBar.tsx`
- `frontend/packages/web/messages/en.json`, `zh.json` (`adminInsights.*`)
- Optional: `frontend/packages/web/lib/cost/metricPreference.ts` for
  read/write helper

**Interfaces**:

```ts
export type InsightsMetric = 'tokens' | 'cost'

// localStorage: 'cubeplex.insights.metric'
// default: 'tokens'
```

**Core logic**:

- `InsightsShell` holds `metric` state initialized from localStorage
  (client-only; SSR-safe default tokens then hydrate).
- Pass `metric` + `onMetricChange` into top bar segmented control.
- On change: set state + write localStorage.

**Tests intent**:

- Unit test preference helper (default, round-trip, invalid → tokens).
- Component smoke: toggle calls onChange.

---

## Unit 2: Token helpers + formatting

**Files**:

- `frontend/packages/web/lib/cost/helpers.ts` (+ `helpers.test.ts`)
- Optionally extract `formatTokenCount` from
  `components/chat/TokenUsageBar.tsx` into a shared util and re-import

**Interfaces**:

```ts
function tokenTotal(row: { input_tokens: number; output_tokens: number }): number
// input_tokens + output_tokens

function formatTokenCount(n: number): string  // compact K/M
function sumTokensFromSummary(summary: CostSummaryResponse): {
  total: number
  input: number
  output: number
}
```

**Tests intent**: pure unit tests for totals and formatting edge cases
(0, 999, 1_200, 3_400_000).

---

## Unit 3: KpiRow metric modes

**Files**:

- `frontend/packages/web/components/admin/insights/cost/KpiRow.tsx`

**What changes**:

- Accept `metric: InsightsMetric`.
- Tokens mode tiles: total tokens, input, output (or total + tokens/call +
  cache + users — match spec table; keep grid readable).
- Cost mode: preserve current tiles.
- Prior-period deltas use the same metric for each tile.

**Tests intent**: render with mock summary; assert token labels when
metric=tokens and USD path when cost.

---

## Unit 4: StackedSection + StackedChart parameterization

**Files**:

- `frontend/packages/web/components/admin/insights/cost/StackedSection.tsx`
- `frontend/packages/web/components/admin/insights/cost/StackedChart.tsx`
- `frontend/packages/web/components/admin/insights/InsightsShell.tsx`
  (pass metric / columns)

**Core logic**:

```ts
// value of a summary row / point
metric === 'cost'
  ? row.cost_amount_micro
  : row.input_tokens + row.output_tokens

// StackedChart pivot:
// cost: pt.cost_amount_micro / 1e6  (today)
// tokens: tokenTotal(pt)           (raw tokens on axis)
```

- `topNWithOther(..., rankFn)` uses token total in tokens mode.
- Column defs: `defaultCostColumns` vs `defaultTokenColumns` (or one
  factory with metric).
- Tooltip / Y-axis formatters switch between `$` and compact tokens.
- `CacheSection` unchanged (still token-based).

**Tests intent**:

- Helper: ranking prefers higher token total when metric=tokens.
- Chart pivot unit test if extracted pure function.

---

## Unit 5: Cost-empty hint

**Files**:

- `InsightsShell.tsx` or a small `CostPricingHint` component
- i18n strings + link to admin models route (existing org models path)

**Core logic**: show only when `metric === 'cost'` && total cost 0 &&
token total > 0.

**Tests intent**: conditional render unit test with mock props.

---

## Unit 6: Docs (implementation PR)

- User-facing Insights/admin cost page under `docs/site` if one exists;
  note tokens default and that cost needs model prices.
- No new doc file unless none covers Insights.

---

## Unit 7: Verification

- Unit tests for helpers + preference.
- Manual: unset prices → tokens non-zero; set prices → cost mode; reload
  keeps preference; cache section still works.

---

## Non-goals

- Backend API changes (unless a timeseries path is found without tokens —
  then a narrow backend fix is allowed; verify first)
- Renaming nav to “Usage” (optional follow-up)
- Workspace non-admin usage page
