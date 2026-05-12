export interface CostAggregateRow {
  bucket: string
  bucket_type: 'workspace' | 'user' | 'model' | 'day'
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
  cost_amount_micro: number
  currency: string
  call_count: number
}

export interface CostSummaryResponse {
  from_date: string
  to_date: string
  total_cost_amount_micro: number
  currency: string
  total_calls: number
  by_workspace: CostAggregateRow[]
  by_model: CostAggregateRow[]
  by_user: CostAggregateRow[]
  by_day: CostAggregateRow[]
}

export interface TimeseriesPoint {
  date: string
  cost_amount_micro: number
  calls: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
}

export interface TimeseriesSeries {
  bucket: string
  points: TimeseriesPoint[]
  currency: string
}

export interface TimeseriesResponse {
  from_date: string
  to_date: string
  granularity: 'day' | 'week'
  dimension: 'workspace' | 'model' | 'user'
  series: TimeseriesSeries[]
  currency: string
}

export function formatCostUsd(micro: number, currency: string = 'USD'): string {
  const amount = micro / 1_000_000
  return `${currency} ${amount.toFixed(4)}`
}
