'use client'

import * as React from 'react'
import { useState } from 'react'
import { useTranslations } from 'next-intl'
import type { TimeseriesResponse } from '@cubeplex/core'
import { StackedChart } from './StackedChart'
import { topNWithOther } from '@/lib/cost/helpers'

export interface Column {
  key: string
  label: string
  align?: 'left' | 'right'
  render: (row: SummaryRow) => React.ReactNode
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
  const tInsights = useTranslations('adminInsights')
  const [showAll, setShowAll] = useState(!!showAllInitially)

  const { kept, otherCount } = topNWithOther(tableRows, topN, (r) => r.cost_amount_micro)
  const visible = showAll
    ? [...tableRows].sort((a, b) => b.cost_amount_micro - a.cost_amount_micro)
    : kept

  if (tableRows.length === 0) {
    return (
      <section className="space-y-2">
        <h2 className="text-sm font-semibold">{title}</h2>
        <p className="rounded-md border border-dashed bg-muted/20 px-4 py-6 text-center text-xs text-muted-foreground">
          {tInsights('noData')}
        </p>
      </section>
    )
  }

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
              {columns.map((c) => (
                <td
                  key={c.key}
                  className={c.align === 'right' ? 'text-right px-2 py-1.5' : 'px-2 py-1.5'}
                >
                  {c.render(row)}
                </td>
              ))}
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

function fmtUsd(micro: number): string {
  return `$${(micro / 1_000_000).toFixed(2)}`
}

export function defaultCostColumns(
  t: ReturnType<typeof useTranslations>,
  kind: 'workspace' | 'model' | 'user',
): Column[] {
  const base: Column[] = [
    { key: 'bucket', label: t(`columns.${kind}`), render: (r) => r.bucket },
    {
      key: 'call_count',
      label: t('columns.calls'),
      align: 'right',
      render: (r) => r.call_count.toLocaleString(),
    },
    {
      key: 'input_tokens',
      label: t('columns.input'),
      align: 'right',
      render: (r) => r.input_tokens.toLocaleString(),
    },
    {
      key: 'output_tokens',
      label: t('columns.output'),
      align: 'right',
      render: (r) => r.output_tokens.toLocaleString(),
    },
  ]
  if (kind === 'model') {
    base.push({
      key: 'cache_rw',
      label: t('columns.cacheRw'),
      align: 'right',
      render: (r) =>
        `${(r.cache_read_tokens / 1e6).toFixed(2)}M / ${(r.cache_write_tokens / 1e6).toFixed(2)}M`,
    })
  }
  base.push({
    key: 'cost_amount_micro',
    label: t('columns.cost'),
    align: 'right',
    render: (r) => fmtUsd(r.cost_amount_micro),
  })
  return base
}
