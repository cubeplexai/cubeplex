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

import type { TimeseriesResponse, TimeseriesSeries } from '@cubebox/core'

/**
 * Cap a timeseries response to at most `n` series by cost-rank, collapsing the rest
 * (and any pre-existing server-side `__other`) into a single client-side `__other`
 * series. Guarantees the returned series array contains at most one `__other`.
 */
export function capTimeseries(ts: TimeseriesResponse, n: number): TimeseriesResponse {
  const backendOther = ts.series.find((s) => s.bucket === '__other')
  const real = ts.series.filter((s) => s.bucket !== '__other')
  if (real.length <= n - 1 && !backendOther) return ts
  const ranked = [...real].sort((a, b) => {
    const sumOf = (s: TimeseriesSeries) => s.points.reduce((acc, p) => acc + p.cost_amount_micro, 0)
    return sumOf(b) - sumOf(a)
  })
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
