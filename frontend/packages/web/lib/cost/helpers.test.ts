import { describe, it, expect } from 'vitest'
import { computeCacheHitRate, topNWithOther, percentDelta, formatPercent } from './helpers'

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

describe('formatPercent', () => {
  it('renders null as dash', () => {
    expect(formatPercent(null)).toBe('—')
  })
  it('rounds to the requested digits', () => {
    expect(formatPercent(0.387, 1)).toBe('38.7%')
  })
})
