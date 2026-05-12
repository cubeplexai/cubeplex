export function computeCacheHitRate(args: { input: number; cacheRead: number }): number | null {
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
