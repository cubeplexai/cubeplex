import type { CostAggregateRow, CostSummaryResponse, TimeseriesResponse } from '../types/billing'
import { toApiError, type ApiClient } from './client'

// Cost reporting is served by the optional package, mounted by the host under
// /api/v1/admin/_extensions/<distribution>/. Kept as one constant so a future
// relocation is a single edit rather than five.
const COST_API_BASE = '/api/v1/admin/_extensions/cubeplex_ee/cost'

export async function fetchCostSummary(
  client: ApiClient,
  params: { from?: string; to?: string } = {},
): Promise<CostSummaryResponse> {
  const query = new URLSearchParams()
  if (params.from) query.set('from_date', params.from)
  if (params.to) query.set('to_date', params.to)

  const res = await client.get(`${COST_API_BASE}/summary?${query}`)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<CostSummaryResponse>
}

export async function fetchWorkspaceCost(
  client: ApiClient,
  wsId: string,
  params: { from?: string; to?: string; group_by?: string } = {},
): Promise<CostAggregateRow[]> {
  const query = new URLSearchParams()
  if (params.from) query.set('from_date', params.from)
  if (params.to) query.set('to_date', params.to)
  if (params.group_by) query.set('group_by', params.group_by)

  const res = await client.get(`${COST_API_BASE}/by-workspace/${wsId}?${query}`)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<CostAggregateRow[]>
}

export function buildExportUrl(wsId?: string, params: { from?: string; to?: string } = {}): string {
  const query = new URLSearchParams()
  if (params.from) query.set('from_date', params.from)
  if (params.to) query.set('to_date', params.to)
  const base = wsId
    ? `${COST_API_BASE}/by-workspace/${wsId}/export.csv`
    : `${COST_API_BASE}/export.csv`
  return `${base}?${query}`
}

export interface TimeseriesParams {
  dimension: 'workspace' | 'model' | 'user'
  granularity?: 'day' | 'week'
  from?: string
  to?: string
  workspace_ids?: string[]
  models?: string[]
  /** Series top-N / __other ranking. Default cost (server default). */
  rank_by?: 'cost' | 'tokens'
}

export async function fetchCostTimeseries(
  client: ApiClient,
  params: TimeseriesParams,
): Promise<TimeseriesResponse> {
  const query = new URLSearchParams()
  query.set('dimension', params.dimension)
  if (params.granularity) query.set('granularity', params.granularity)
  if (params.from) query.set('from_date', params.from)
  if (params.to) query.set('to_date', params.to)
  if (params.workspace_ids && params.workspace_ids.length) {
    query.set('workspace_ids', params.workspace_ids.join(','))
  }
  if (params.models && params.models.length) {
    query.set('models', params.models.join(','))
  }
  if (params.rank_by) {
    query.set('rank_by', params.rank_by)
  }
  const res = await client.get(`${COST_API_BASE}/timeseries?${query}`)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<TimeseriesResponse>
}
