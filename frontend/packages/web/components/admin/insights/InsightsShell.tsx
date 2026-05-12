'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { useCostData, type CostFilters } from '@/hooks/useCostData'
import { InsightsTopBar } from './InsightsTopBar'
import { InsightsFilterSidebar } from './InsightsFilterSidebar'
import { KpiRow } from './cost/KpiRow'
import { StackedSection, defaultCostColumns, type SummaryRow } from './cost/StackedSection'
import { CacheSection } from './cost/CacheSection'
import { PALETTE_WORKSPACE, PALETTE_MODEL, PALETTE_USER, PALETTE_CACHE } from './cost/palettes'
import type { CostAggregateRow, TimeseriesResponse } from '@cubebox/core'

/** Server returns up to 25 series; chart caps further to keep visual density sane. */
function capTimeseries(ts: TimeseriesResponse, n: number): TimeseriesResponse {
  if (ts.series.length <= n) return ts
  const ranked = [...ts.series].sort((a, b) => {
    const sumOf = (s: typeof a) => s.points.reduce((acc, p) => acc + p.cost_amount_micro, 0)
    return sumOf(b) - sumOf(a)
  })
  const keep = ranked.slice(0, n - 1)
  const rest = ranked.slice(n - 1)
  const dateMap: Record<
    string,
    { cost: number; calls: number; input: number; output: number; cr: number; cw: number }
  > = {}
  rest.forEach((s) =>
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
  const dates = [...new Set(rest.flatMap((s) => s.points.map((p) => p.date)))].sort()
  const otherSeries = {
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

function aggRowToSummaryRow(r: CostAggregateRow): SummaryRow {
  return {
    bucket: r.bucket,
    cost_amount_micro: r.cost_amount_micro,
    call_count: r.call_count,
    input_tokens: r.input_tokens,
    output_tokens: r.output_tokens,
    cache_read_tokens: r.cache_read_tokens,
    cache_write_tokens: r.cache_write_tokens,
    currency: r.currency,
  }
}

export function InsightsShell() {
  const t = useTranslations('adminInsights.cost')
  const [filters, setFilters] = useState<CostFilters>({
    range: '30d',
    workspaceIds: [],
    models: [],
    granularity: 'day',
  })
  const data = useCostData(filters)

  const availableWorkspaces = useMemo(
    () => (data.summary?.by_workspace ?? []).map((r) => ({ id: r.bucket, name: r.bucket })),
    [data.summary],
  )
  const availableModels = useMemo(
    () => (data.summary?.by_model ?? []).map((r) => r.bucket),
    [data.summary],
  )

  const rangeDays =
    typeof filters.range === 'string'
      ? filters.range === '7d'
        ? 7
        : filters.range === '30d'
          ? 30
          : 90
      : 30

  return (
    <div className="flex flex-col h-full">
      <InsightsTopBar
        fromDate={data.summary?.from_date ?? '…'}
        toDate={data.summary?.to_date ?? '…'}
      />
      <div className="flex flex-1 min-h-0">
        <InsightsFilterSidebar
          filters={filters}
          onChange={setFilters}
          availableWorkspaces={availableWorkspaces}
          availableModels={availableModels}
        />
        <div className="flex-1 overflow-y-auto p-4 space-y-6">
          {data.error && (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 text-sm p-3">
              {data.error}
            </div>
          )}
          {data.summary && (
            <KpiRow summary={data.summary} priorSummary={data.priorSummary} rangeDays={rangeDays} />
          )}
          {data.summary && data.byWorkspace && (
            <StackedSection
              title={t('byWorkspace')}
              timeseries={capTimeseries(data.byWorkspace, 10)}
              tableRows={data.summary.by_workspace.map(aggRowToSummaryRow)}
              palette={PALETTE_WORKSPACE}
              topN={10}
              columns={defaultCostColumns(t, 'workspace')}
            />
          )}
          {data.summary && data.byModel && (
            <StackedSection
              title={t('byModel')}
              timeseries={capTimeseries(data.byModel, 10)}
              tableRows={data.summary.by_model.map(aggRowToSummaryRow)}
              palette={PALETTE_MODEL}
              topN={10}
              columns={defaultCostColumns(t, 'model')}
            />
          )}
          {data.summary && data.byUser && (
            <StackedSection
              title={t('byUser')}
              timeseries={capTimeseries(data.byUser, 8)}
              tableRows={data.summary.by_user.map(aggRowToSummaryRow)}
              palette={PALETTE_USER}
              topN={8}
              columns={defaultCostColumns(t, 'user')}
            />
          )}
          {data.summary && data.byModel && (
            <CacheSection
              timeseriesByModel={capTimeseries(data.byModel, 5)}
              summary={data.summary}
              palette={PALETTE_CACHE}
            />
          )}
        </div>
      </div>
    </div>
  )
}
