import type { CostAggregateRow, CostSummaryResponse } from '../types/billing'
import { toApiError, type ApiClient } from './client'

export async function fetchCostSummary(
  client: ApiClient,
  params: { from?: string; to?: string } = {},
): Promise<CostSummaryResponse> {
  const query = new URLSearchParams()
  if (params.from) query.set('from_date', params.from)
  if (params.to) query.set('to_date', params.to)

  const res = await client.get(`/api/v1/admin/cost/summary?${query}`)
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

  const res = await client.get(`/api/v1/admin/cost/by-workspace/${wsId}?${query}`)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<CostAggregateRow[]>
}

export function buildExportUrl(wsId?: string, params: { from?: string; to?: string } = {}): string {
  const query = new URLSearchParams()
  if (params.from) query.set('from_date', params.from)
  if (params.to) query.set('to_date', params.to)
  const base = wsId
    ? `/api/v1/admin/cost/by-workspace/${wsId}/export.csv`
    : '/api/v1/admin/cost/export.csv'
  return `${base}?${query}`
}
