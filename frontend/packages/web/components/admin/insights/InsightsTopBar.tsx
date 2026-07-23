'use client'

import { useTranslations } from 'next-intl'
import { buildExportUrl } from '@cubeplex/core'
import { buttonVariants } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { Download } from 'lucide-react'
import type { InsightsMetric } from '@/lib/cost/metricPreference'

interface Props {
  fromDate: string
  toDate: string
  metric: InsightsMetric
  onMetricChange: (metric: InsightsMetric) => void
}

export function InsightsTopBar({ fromDate, toDate, metric, onMetricChange }: Props) {
  const t = useTranslations('adminInsights')
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-border/70">
      <div className="min-w-0">
        <h1 className="text-sm font-semibold">{t('heading')}</h1>
        <p className="text-xs text-muted-foreground">{`${fromDate} — ${toDate}`}</p>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <div
          role="tablist"
          aria-label={t('metricToggleLabel')}
          className="inline-flex items-center rounded-md border bg-muted/40 p-0.5"
        >
          {(['tokens', 'cost'] as const).map((value) => {
            const selected = metric === value
            return (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={selected}
                tabIndex={selected ? 0 : -1}
                onClick={() => onMetricChange(value)}
                className={cn(
                  'rounded px-2.5 py-1 text-xs font-medium transition-colors',
                  selected
                    ? 'bg-background text-foreground shadow-sm'
                    : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {value === 'tokens' ? t('metricTokens') : t('metricCost')}
              </button>
            )
          })}
        </div>
        <a
          href={buildExportUrl(undefined, { from: fromDate, to: toDate })}
          download
          className={cn(buttonVariants({ variant: 'default', size: 'sm' }))}
        >
          <Download className="size-3.5 mr-1.5" />
          {t('exportOrgCsv')}
        </a>
      </div>
    </div>
  )
}
