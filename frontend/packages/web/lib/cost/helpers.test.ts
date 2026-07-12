import { describe, it, expect } from 'vitest'
import {
  computeCacheHitRate,
  topNWithOther,
  percentDelta,
  formatPercent,
  capTimeseries,
} from './helpers'
import type { TimeseriesResponse, TimeseriesSeries } from '@cubeplex/core'

function makeSeries(bucket: string, dailyCost: Record<string, number>): TimeseriesSeries {
  return {
    bucket,
    currency: 'USD',
    points: Object.entries(dailyCost).map(([date, cost]) => ({
      date,
      cost_amount_micro: cost,
      calls: 1,
      input_tokens: 0,
      output_tokens: 0,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
    })),
  }
}

function makeTs(series: TimeseriesSeries[]): TimeseriesResponse {
  return {
    from_date: '2026-05-01',
    to_date: '2026-05-02',
    granularity: 'day',
    dimension: 'workspace',
    series,
    currency: 'USD',
  }
}

describe('computeCacheHitRate', () => {
  it('returns null when no input or cache reads', () => {
    expect(computeCacheHitRate({ input: 0, cacheRead: 0 })).toBeNull()
  })
  it('returns null when cache read is 0 (provider has no cache hits)', () => {
    expect(computeCacheHitRate({ input: 2016, cacheRead: 0 })).toBeNull()
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

describe('capTimeseries', () => {
  it('returns input unchanged when count fits', () => {
    const ts = makeTs([makeSeries('a', { '2026-05-01': 10 })])
    expect(capTimeseries(ts, 5)).toBe(ts)
  })

  it('collapses tail into __other and never produces duplicate __other key', () => {
    // Backend already collapsed some buckets into __other (e.g. it was >25 originally).
    // Client cap further reduces to top 2 (n=2 -> keep top 1 + __other).
    const ts = makeTs([
      makeSeries('big', { '2026-05-01': 1000 }),
      makeSeries('medium', { '2026-05-01': 500 }),
      makeSeries('small', { '2026-05-01': 50 }),
      makeSeries('__other', { '2026-05-01': 300 }),
    ])
    const capped = capTimeseries(ts, 2)
    const otherCount = capped.series.filter((s) => s.bucket === '__other').length
    expect(otherCount).toBe(1)
    expect(capped.series).toHaveLength(2)
    expect(capped.series[0].bucket).toBe('big')
    const otherSeries = capped.series.find((s) => s.bucket === '__other')!
    // medium (500) + small (50) + backend __other (300) = 850
    expect(otherSeries.points[0].cost_amount_micro).toBe(850)
  })

  it('folds large pre-existing backend __other into client __other', () => {
    // Backend __other (800) is larger than some real buckets but must NOT take a top slot.
    const ts = makeTs([
      makeSeries('a', { '2026-05-01': 1000 }),
      makeSeries('b', { '2026-05-01': 100 }),
      makeSeries('__other', { '2026-05-01': 800 }),
    ])
    const capped = capTimeseries(ts, 2)
    expect(capped.series.map((s) => s.bucket)).toEqual(['a', '__other'])
    const otherSeries = capped.series.find((s) => s.bucket === '__other')!
    // b (100) + backend __other (800) = 900
    expect(otherSeries.points[0].cost_amount_micro).toBe(900)
  })
})
