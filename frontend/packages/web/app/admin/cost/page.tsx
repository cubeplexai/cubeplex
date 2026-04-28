'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { buildExportUrl, createApiClient, fetchCostSummary, formatCostUsd } from '@cubebox/core'
import type { CostSummaryResponse } from '@cubebox/core'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { buttonVariants } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export default function CostPage() {
  const t = useTranslations('adminCost')
  const client = useMemo(() => createApiClient(''), [])
  const [summary, setSummary] = useState<CostSummaryResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchCostSummary(client, {})
      .then(setSummary)
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : String(err)
        setError(msg)
      })
      .finally(() => setLoading(false))
  }, [client])

  if (loading) return <div className="p-6 text-sm text-muted-foreground">{t('loading')}</div>
  if (error) return <div className="p-6 text-sm text-destructive">{error}</div>
  if (!summary) return null

  const totalCost = formatCostUsd(summary.total_cost_amount_micro, summary.currency)
  const dateRange = `${summary.from_date} — ${summary.to_date}`

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">{t('heading')}</h1>
          <p className="text-xs text-muted-foreground mt-0.5">{dateRange}</p>
        </div>
        <a
          href={buildExportUrl()}
          download
          className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))}
        >
          {t('exportOrgCsv')}
        </a>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-4 max-w-lg">
        <div className="rounded-lg border border-border bg-card p-4 space-y-1">
          <p className="text-xs text-muted-foreground">{t('totalCost')}</p>
          <p className="text-lg font-semibold tabular-nums">{totalCost}</p>
        </div>
        <div className="rounded-lg border border-border bg-card p-4 space-y-1">
          <p className="text-xs text-muted-foreground">{t('totalCalls')}</p>
          <p className="text-lg font-semibold tabular-nums">
            {summary.total_calls.toLocaleString()}
          </p>
        </div>
      </div>

      {/* By Workspace table */}
      <section className="space-y-2">
        <h2 className="text-sm font-medium">{t('byWorkspace')}</h2>
        <div className="rounded-lg border border-border overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Workspace</TableHead>
                <TableHead className="text-right">{t('callCount')}</TableHead>
                <TableHead className="text-right">{t('inputTokens')}</TableHead>
                <TableHead className="text-right">{t('outputTokens')}</TableHead>
                <TableHead className="text-right">{t('cost')}</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {summary.by_workspace.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center text-muted-foreground">
                    {t('noData')}
                  </TableCell>
                </TableRow>
              ) : (
                summary.by_workspace.map((row) => (
                  <TableRow key={row.bucket}>
                    <TableCell className="font-mono text-xs">{row.bucket}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {row.call_count.toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {row.input_tokens.toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {row.output_tokens.toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatCostUsd(row.cost_amount_micro, row.currency)}
                    </TableCell>
                    <TableCell>
                      <a
                        href={buildExportUrl(row.bucket)}
                        download
                        title={t('exportCsv')}
                        className="text-muted-foreground hover:text-foreground text-xs underline underline-offset-2"
                      >
                        CSV
                      </a>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </section>

      {/* By Model table */}
      <section className="space-y-2">
        <h2 className="text-sm font-medium">{t('byModel')}</h2>
        <div className="rounded-lg border border-border overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t('providerModel')}</TableHead>
                <TableHead className="text-right">{t('callCount')}</TableHead>
                <TableHead className="text-right">{t('inputTokens')}</TableHead>
                <TableHead className="text-right">{t('outputTokens')}</TableHead>
                <TableHead className="text-right">{t('cost')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {summary.by_model.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center text-muted-foreground">
                    {t('noData')}
                  </TableCell>
                </TableRow>
              ) : (
                summary.by_model.map((row) => (
                  <TableRow key={row.bucket}>
                    <TableCell className="font-mono text-xs">{row.bucket}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {row.call_count.toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {row.input_tokens.toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {row.output_tokens.toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatCostUsd(row.cost_amount_micro, row.currency)}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </section>
    </div>
  )
}
