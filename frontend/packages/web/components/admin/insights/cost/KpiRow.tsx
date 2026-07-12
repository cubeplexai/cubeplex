'use client'

import { useTranslations } from 'next-intl'
import type { CostSummaryResponse } from '@cubeplex/core'
import { formatPercent, percentDelta } from '@/lib/cost/helpers'
import { cn } from '@/lib/utils'

interface Props {
  summary: CostSummaryResponse
  priorSummary: CostSummaryResponse | null
  rangeDays: number
}

function fmtUsd(micro: number, currency: string): string {
  const amt = micro / 1_000_000
  return `${currency === 'USD' ? '$' : currency + ' '}${amt.toFixed(2)}`
}

function fmtNum(n: number): string {
  return n.toLocaleString()
}

function totalCacheRead(s: CostSummaryResponse): number {
  return s.by_workspace.reduce((a, r) => a + r.cache_read_tokens, 0)
}

function totalInput(s: CostSummaryResponse): number {
  return s.by_workspace.reduce((a, r) => a + r.input_tokens, 0)
}

function hitRate(s: CostSummaryResponse | null): number | null {
  if (!s) return null
  const cr = totalCacheRead(s)
  const inp = totalInput(s)
  if (cr + inp === 0) return null
  return cr / (cr + inp)
}

export function KpiRow({ summary, priorSummary, rangeDays }: Props) {
  const t = useTranslations('adminInsights.kpi')
  const cur = {
    cost: summary.total_cost_amount_micro,
    calls: summary.total_calls,
    avg: summary.total_calls ? summary.total_cost_amount_micro / summary.total_calls : 0,
    cache: hitRate(summary),
    users: summary.by_user.length,
  }
  const prev = {
    cost: priorSummary?.total_cost_amount_micro ?? null,
    calls: priorSummary?.total_calls ?? null,
    cache: hitRate(priorSummary),
    users: priorSummary?.by_user.length ?? null,
  }
  const delta = (a: number, b: number | null) => (b === null ? null : percentDelta(a, b))

  function tile(
    label: string,
    value: string,
    deltaPct: number | null,
    kind: 'up-bad' | 'up-good' | 'neutral',
  ) {
    const text = deltaPct === null ? t('unchanged') : formatPercent(deltaPct, 0)
    const isUp = deltaPct !== null && deltaPct > 0.01
    const isDn = deltaPct !== null && deltaPct < -0.01
    const color =
      kind === 'neutral' || (!isUp && !isDn)
        ? 'text-muted-foreground'
        : kind === 'up-bad'
          ? isUp
            ? 'text-danger-fg'
            : 'text-success-fg'
          : isUp
            ? 'text-success-fg'
            : 'text-danger-fg'
    return (
      <div className="rounded-md border bg-card px-3 py-2.5">
        <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {label}
        </div>
        <div className="mt-1 text-lg font-semibold tabular-nums">{value}</div>
        <div className={cn('mt-0.5 text-[11px]', color)}>
          {deltaPct === null
            ? t('unchanged')
            : `${isUp ? '↑ ' : isDn ? '↓ ' : ''}${text} ${t('vsPrior', { days: rangeDays })}`}
        </div>
      </div>
    )
  }

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-2.5">
      {tile(
        t('totalCost'),
        fmtUsd(cur.cost, summary.currency),
        delta(cur.cost, prev.cost),
        'up-bad',
      )}
      {tile(t('totalCalls'), fmtNum(cur.calls), delta(cur.calls, prev.calls), 'up-bad')}
      {tile(t('avgPerCall'), fmtUsd(cur.avg, summary.currency), null, 'neutral')}
      {tile(
        t('cacheHitRate'),
        cur.cache === null ? '—' : formatPercent(cur.cache, 0),
        delta(cur.cache ?? 0, prev.cache),
        'up-good',
      )}
      {tile(t('activeUsers'), fmtNum(cur.users), delta(cur.users, prev.users), 'neutral')}
    </div>
  )
}
