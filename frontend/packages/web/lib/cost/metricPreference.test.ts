import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  INSIGHTS_METRIC_STORAGE_KEY,
  parseInsightsMetric,
  readInsightsMetric,
  writeInsightsMetric,
} from './metricPreference'

describe('parseInsightsMetric', () => {
  it('defaults missing/invalid to tokens', () => {
    expect(parseInsightsMetric(null)).toBe('tokens')
    expect(parseInsightsMetric(undefined)).toBe('tokens')
    expect(parseInsightsMetric('')).toBe('tokens')
    expect(parseInsightsMetric('garbage')).toBe('tokens')
    expect(parseInsightsMetric('Tokens')).toBe('tokens')
  })

  it('accepts cost only as exact string', () => {
    expect(parseInsightsMetric('cost')).toBe('cost')
  })

  it('accepts tokens', () => {
    expect(parseInsightsMetric('tokens')).toBe('tokens')
  })
})

describe('readInsightsMetric / writeInsightsMetric', () => {
  afterEach(() => {
    localStorage.removeItem(INSIGHTS_METRIC_STORAGE_KEY)
    vi.restoreAllMocks()
  })

  it('round-trips cost preference', () => {
    writeInsightsMetric('cost')
    expect(localStorage.getItem(INSIGHTS_METRIC_STORAGE_KEY)).toBe('cost')
    expect(readInsightsMetric()).toBe('cost')
  })

  it('returns tokens when storage empty', () => {
    expect(readInsightsMetric()).toBe('tokens')
  })

  it('returns tokens when getItem throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    expect(readInsightsMetric()).toBe('tokens')
  })
})
