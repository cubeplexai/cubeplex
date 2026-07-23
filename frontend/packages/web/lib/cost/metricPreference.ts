export type InsightsMetric = 'tokens' | 'cost'

export const INSIGHTS_METRIC_STORAGE_KEY = 'cubeplex.insights.metric'

/** Parse a storage value; anything other than exact `'cost'` is tokens. */
export function parseInsightsMetric(raw: string | null | undefined): InsightsMetric {
  return raw === 'cost' ? 'cost' : 'tokens'
}

/**
 * Read preference from localStorage. Safe to call only on the client after
 * mount — InsightsShell must init state to `'tokens'` and call this in useEffect.
 */
export function readInsightsMetric(): InsightsMetric {
  try {
    return parseInsightsMetric(localStorage.getItem(INSIGHTS_METRIC_STORAGE_KEY))
  } catch {
    return 'tokens'
  }
}

export function writeInsightsMetric(metric: InsightsMetric): void {
  try {
    localStorage.setItem(INSIGHTS_METRIC_STORAGE_KEY, metric)
  } catch {
    // quota / private mode — preference is best-effort
  }
}
