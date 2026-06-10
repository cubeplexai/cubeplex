'use client'

import { useTranslations } from 'next-intl'
import type { CostSummaryResponse, TimeseriesResponse } from '@cubebox/core'
import { RateChart, type RateSeries } from './RateChart'
import { computeCacheHitRate, formatPercent } from '@/lib/cost/helpers'

interface Props {
  timeseriesByModel: TimeseriesResponse
  summary: CostSummaryResponse
  palette: string[]
}

export function CacheSection({ timeseriesByModel, summary, palette }: Props) {
  const t = useTranslations('adminInsights.cost')
  const tInsights = useTranslations('adminInsights')

  const series: RateSeries[] = timeseriesByModel.series.slice(0, palette.length).map((s, i) => ({
    bucket: s.bucket,
    color: palette[i],
    points: s.points.map((p) => ({
      date: p.date,
      rate: computeCacheHitRate({ input: p.input_tokens, cacheRead: p.cache_read_tokens }),
    })),
  }))

  const dateMap: Record<string, { cr: number; inp: number }> = {}
  timeseriesByModel.series.forEach((s) =>
    s.points.forEach((p) => {
      const v = (dateMap[p.date] = dateMap[p.date] ?? { cr: 0, inp: 0 })
      v.cr += p.cache_read_tokens
      v.inp += p.input_tokens
    }),
  )
  const orgAvg = Object.entries(dateMap)
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([date, v]) => ({
      date,
      rate: computeCacheHitRate({ input: v.inp, cacheRead: v.cr }),
    }))

  if (summary.by_model.length === 0) {
    return (
      <section className="space-y-2 mt-4">
        <h2 className="text-sm font-semibold">{t('cacheEfficiency')}</h2>
        <p className="rounded-md border border-dashed bg-muted/20 px-4 py-6 text-center text-xs text-muted-foreground">
          {tInsights('noData')}
        </p>
      </section>
    )
  }

  return (
    <section className="space-y-2 mt-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold">
          {t('cacheEfficiency')}{' '}
          <span className="font-normal text-muted-foreground">— {t('cacheEfficiencyHint')}</span>
        </h2>
      </div>
      <div className="rounded-md border bg-card p-3">
        <RateChart series={series} orgAvg={orgAvg} />
      </div>
      <table className="w-full text-xs tabular-nums">
        <thead>
          <tr className="text-muted-foreground">
            <th className="text-left px-2 py-1.5">{t('columns.model')}</th>
            <th className="text-right px-2 py-1.5">{t('columns.cacheReads')}</th>
            <th className="text-right px-2 py-1.5">{t('columns.cacheWrites')}</th>
            <th className="text-right px-2 py-1.5">{t('columns.uncachedInput')}</th>
            <th className="text-right px-2 py-1.5">{t('columns.hitRate')}</th>
          </tr>
        </thead>
        <tbody>
          {summary.by_model.map((r) => {
            const rate = computeCacheHitRate({
              input: r.input_tokens,
              cacheRead: r.cache_read_tokens,
            })
            return (
              <tr key={r.bucket} className="border-b border-border/40 last:border-0">
                <td className="px-2 py-1.5 font-mono text-[11px]">{r.bucket}</td>
                <td className="text-right px-2 py-1.5">{r.cache_read_tokens.toLocaleString()}</td>
                <td className="text-right px-2 py-1.5">{r.cache_write_tokens.toLocaleString()}</td>
                <td className="text-right px-2 py-1.5">{r.input_tokens.toLocaleString()}</td>
                <td className="text-right px-2 py-1.5">{formatPercent(rate, 0)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </section>
  )
}
