'use client'

import * as React from 'react'
import { useState } from 'react'
import { useTranslations } from 'next-intl'
import type { TimeseriesResponse } from '@cubebox/core'
import { StackedChart } from './StackedChart'
import { topNWithOther } from '@/lib/cost/helpers'

export interface Column {
  key: string
  label: string
  align?: 'left' | 'right'
  render?: (row: SummaryRow) => React.ReactNode
}

export interface SummaryRow {
  bucket: string
  cost_amount_micro: number
  call_count: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
  currency: string
}

interface Props {
  title: string
  timeseries: TimeseriesResponse
  tableRows: SummaryRow[]
  palette: string[]
  topN: number
  columns: Column[]
  showAllInitially?: boolean
}

export function StackedSection({
  title,
  timeseries,
  tableRows,
  palette,
  topN,
  columns,
  showAllInitially,
}: Props) {
  const t = useTranslations('adminInsights.cost')
  const [showAll, setShowAll] = useState(!!showAllInitially)

  const { kept, otherCount } = topNWithOther(tableRows, topN, (r) => r.cost_amount_micro)
  const visible = showAll
    ? [...tableRows].sort((a, b) => b.cost_amount_micro - a.cost_amount_micro)
    : kept

  return (
    <section className="space-y-2">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold">{title}</h2>
      </div>
      <div className="rounded-md border bg-card p-3">
        <StackedChart data={timeseries} palette={palette} />
      </div>
      <table className="w-full text-xs tabular-nums">
        <thead>
          <tr className="text-muted-foreground">
            {columns.map((c) => (
              <th
                key={c.key}
                className={c.align === 'right' ? 'text-right px-2 py-1.5' : 'text-left px-2 py-1.5'}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {visible.map((row) => (
            <tr key={row.bucket} className="border-b border-border/40 last:border-0">
              {columns.map((c) => {
                const content = c.render
                  ? c.render(row)
                  : ((row as unknown as Record<string, unknown>)[c.key] as React.ReactNode)
                return (
                  <td
                    key={c.key}
                    className={c.align === 'right' ? 'text-right px-2 py-1.5' : 'px-2 py-1.5'}
                  >
                    {content}
                  </td>
                )
              })}
            </tr>
          ))}
          {!showAll && otherCount > 0 && (
            <tr>
              <td
                colSpan={columns.length}
                className="text-center text-muted-foreground italic py-2 cursor-pointer hover:underline"
                onClick={() => setShowAll(true)}
              >
                {t('showAll', { count: otherCount })}
              </td>
            </tr>
          )}
          {showAll && tableRows.length > topN && (
            <tr>
              <td
                colSpan={columns.length}
                className="text-center text-muted-foreground italic py-2 cursor-pointer hover:underline"
                onClick={() => setShowAll(false)}
              >
                {t('showLess')}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </section>
  )
}
