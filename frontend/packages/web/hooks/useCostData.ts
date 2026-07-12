'use client'

import { useEffect, useMemo, useState } from 'react'
import {
  createApiClient,
  fetchCostSummary,
  fetchCostTimeseries,
  type CostSummaryResponse,
  type TimeseriesResponse,
} from '@cubeplex/core'

export type RangePreset = '7d' | '30d' | '90d'
export type Granularity = 'day' | 'week'

export interface CostFilters {
  range: RangePreset | { from: string; to: string }
  workspaceIds: string[]
  models: string[]
  granularity: Granularity
}

export interface CostData {
  summary: CostSummaryResponse | null
  priorSummary: CostSummaryResponse | null
  byWorkspace: TimeseriesResponse | null
  byModel: TimeseriesResponse | null
  byUser: TimeseriesResponse | null
  loading: boolean
  error: string | null
  errors: { section: string; message: string }[]
}

function resolveDates(filters: CostFilters): { from: string; to: string; days: number } {
  if (typeof filters.range === 'object') {
    const days = Math.max(
      1,
      Math.round(
        (new Date(filters.range.to).getTime() - new Date(filters.range.from).getTime()) /
          (24 * 3600 * 1000),
      ),
    )
    return { from: filters.range.from, to: filters.range.to, days }
  }
  const days = filters.range === '7d' ? 7 : filters.range === '30d' ? 30 : 90
  const to = new Date()
  const from = new Date(to.getTime() - days * 24 * 3600 * 1000)
  const iso = (d: Date) => d.toISOString().slice(0, 10)
  return { from: iso(from), to: iso(to), days }
}

function priorWindow(from: string, to: string): { from: string; to: string } {
  const fromD = new Date(from)
  const toD = new Date(to)
  const span = toD.getTime() - fromD.getTime()
  const priorTo = new Date(fromD.getTime() - 24 * 3600 * 1000)
  const priorFrom = new Date(priorTo.getTime() - span)
  const iso = (d: Date) => d.toISOString().slice(0, 10)
  return { from: iso(priorFrom), to: iso(priorTo) }
}

export function useCostData(filters: CostFilters): CostData {
  const client = useMemo(() => createApiClient(''), [])
  const key = JSON.stringify(filters)
  const [data, setData] = useState<CostData>({
    summary: null,
    priorSummary: null,
    byWorkspace: null,
    byModel: null,
    byUser: null,
    loading: true,
    error: null,
    errors: [],
  })

  useEffect(() => {
    let cancelled = false
    const { from, to } = resolveDates(filters)
    const prior = priorWindow(from, to)
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setData((d) => ({ ...d, loading: true, error: null, errors: [] }))

    const wsIds = filters.workspaceIds.length ? filters.workspaceIds : undefined
    const models = filters.models.length ? filters.models : undefined

    Promise.allSettled([
      fetchCostSummary(client, { from, to }),
      fetchCostSummary(client, { from: prior.from, to: prior.to }),
      fetchCostTimeseries(client, {
        dimension: 'workspace',
        from,
        to,
        granularity: filters.granularity,
        workspace_ids: wsIds,
        models,
      }),
      fetchCostTimeseries(client, {
        dimension: 'model',
        from,
        to,
        granularity: filters.granularity,
        workspace_ids: wsIds,
        models,
      }),
      fetchCostTimeseries(client, {
        dimension: 'user',
        from,
        to,
        granularity: filters.granularity,
        workspace_ids: wsIds,
        models,
      }),
    ]).then((results) => {
      if (cancelled) return
      const [summary, priorSummary, byWorkspace, byModel, byUser] = results
      const errors: { section: string; message: string }[] = []
      function pick<T>(label: string, r: PromiseSettledResult<T>): T | null {
        if (r.status === 'fulfilled') return r.value
        const message = r.reason instanceof Error ? r.reason.message : String(r.reason)
        errors.push({ section: label, message })
        return null
      }
      const summaryVal = pick('summary', summary)
      const topLevelError =
        summary.status === 'rejected' ? (errors[0]?.message ?? 'load failed') : null
      setData({
        summary: summaryVal,
        priorSummary: pick('priorSummary', priorSummary),
        byWorkspace: pick('byWorkspace', byWorkspace),
        byModel: pick('byModel', byModel),
        byUser: pick('byUser', byUser),
        loading: false,
        error: topLevelError,
        errors,
      })
    })

    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, client])

  return data
}
