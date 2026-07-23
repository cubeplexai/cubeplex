import type { CostSummaryResponse, TimeseriesResponse, TimeseriesSeries } from '@cubeplex/core'
import type { InsightsMetric } from './metricPreference'

export type { InsightsMetric }

export function computeCacheHitRate(args: { input: number; cacheRead: number }): number | null {
  if (args.cacheRead === 0) return null
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
    return {
      kept: [...items].sort((a, b) => costOf(b) - costOf(a)),
      otherCount: 0,
      otherSum: 0,
    }
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

/** Primary token total for Insights: input + output (cache stays separate). */
export function tokenTotal(row: { input_tokens: number; output_tokens: number }): number {
  return row.input_tokens + row.output_tokens
}

/** Compact token display shared by chat TokenUsageBar and admin Insights. */
export function formatTokenCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

/** Org-wide token sums from the summary (sums `by_workspace` rows). */
export function sumTokensFromSummary(summary: CostSummaryResponse): {
  total: number
  input: number
  output: number
} {
  let input = 0
  let output = 0
  for (const r of summary.by_workspace) {
    input += r.input_tokens
    output += r.output_tokens
  }
  return { total: input + output, input, output }
}

export function metricValueOf(
  row: { cost_amount_micro: number; input_tokens: number; output_tokens: number },
  metric: InsightsMetric,
): number {
  return metric === 'cost' ? row.cost_amount_micro : tokenTotal(row)
}

/**
 * Cap a timeseries response to at most `n` series by rank, collapsing the rest
 * (and any pre-existing server-side `__other`) into a single client-side `__other`
 * series. Guarantees the returned series array contains at most one `__other`.
 *
 * `metric` controls ranking: cost uses `cost_amount_micro`; tokens uses
 * `input_tokens + output_tokens` so zero-price traffic still ranks correctly.
 */
export function capTimeseries(
  ts: TimeseriesResponse,
  n: number,
  metric: InsightsMetric = 'cost',
): TimeseriesResponse {
  const backendOther = ts.series.find((s) => s.bucket === '__other')
  const real = ts.series.filter((s) => s.bucket !== '__other')
  if (real.length <= n - 1 && !backendOther) return ts
  const sumOf = (s: TimeseriesSeries) =>
    s.points.reduce((acc, p) => acc + metricValueOf(p, metric), 0)
  const ranked = [...real].sort((a, b) => sumOf(b) - sumOf(a))
  const keep = ranked.slice(0, n - 1)
  const rest = ranked.slice(n - 1)
  const sourcesForOther: TimeseriesSeries[] = backendOther ? [...rest, backendOther] : rest
  if (sourcesForOther.length === 0) return { ...ts, series: keep }
  const dateMap: Record<
    string,
    { cost: number; calls: number; input: number; output: number; cr: number; cw: number }
  > = {}
  sourcesForOther.forEach((s) =>
    s.points.forEach((p) => {
      const v = (dateMap[p.date] = dateMap[p.date] ?? {
        cost: 0,
        calls: 0,
        input: 0,
        output: 0,
        cr: 0,
        cw: 0,
      })
      v.cost += p.cost_amount_micro
      v.calls += p.calls
      v.input += p.input_tokens
      v.output += p.output_tokens
      v.cr += p.cache_read_tokens
      v.cw += p.cache_write_tokens
    }),
  )
  const dates = [...new Set(sourcesForOther.flatMap((s) => s.points.map((p) => p.date)))].sort()
  const otherSeries: TimeseriesSeries = {
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
