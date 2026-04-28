'use client'

import { useEffect, useMemo, useState } from 'react'
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

  if (loading) return <div className="p-6 text-sm text-muted-foreground">加载中…</div>
  if (error) return <div className="p-6 text-sm text-destructive">{error}</div>
  if (!summary) return null

  const totalCost = formatCostUsd(summary.total_cost_amount_micro, summary.currency)
  const dateRange = `${summary.from_date} — ${summary.to_date}`

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">成本概览</h1>
          <p className="text-xs text-muted-foreground mt-0.5">{dateRange}</p>
        </div>
        <a
          href={buildExportUrl()}
          download
          className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))}
        >
          导出全 org CSV ↓
        </a>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-4 max-w-lg">
        <div className="rounded-lg border border-border bg-card p-4 space-y-1">
          <p className="text-xs text-muted-foreground">总费用</p>
          <p className="text-lg font-semibold tabular-nums">{totalCost}</p>
        </div>
        <div className="rounded-lg border border-border bg-card p-4 space-y-1">
          <p className="text-xs text-muted-foreground">总调用次数</p>
          <p className="text-lg font-semibold tabular-nums">
            {summary.total_calls.toLocaleString()}
          </p>
        </div>
      </div>

      {/* By Workspace table */}
      <section className="space-y-2">
        <h2 className="text-sm font-medium">按 Workspace</h2>
        {summary.by_workspace.length === 0 ? (
          <p className="text-sm text-muted-foreground">本月暂无数据</p>
        ) : (
          <div className="rounded-lg border border-border overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Workspace</TableHead>
                  <TableHead className="text-right">调用次数</TableHead>
                  <TableHead className="text-right">费用</TableHead>
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {summary.by_workspace.map((row) => (
                  <TableRow key={row.bucket}>
                    <TableCell className="font-mono text-xs">{row.bucket}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {row.call_count.toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatCostUsd(row.cost_amount_micro, row.currency)}
                    </TableCell>
                    <TableCell>
                      <a
                        href={buildExportUrl(row.bucket)}
                        download
                        title="导出 CSV"
                        className="text-muted-foreground hover:text-foreground text-xs underline underline-offset-2"
                      >
                        CSV
                      </a>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </section>

      {/* By Model table */}
      <section className="space-y-2">
        <h2 className="text-sm font-medium">按 Model</h2>
        {summary.by_model.length === 0 ? (
          <p className="text-sm text-muted-foreground">本月暂无数据</p>
        ) : (
          <div className="rounded-lg border border-border overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Model</TableHead>
                  <TableHead className="text-right">调用次数</TableHead>
                  <TableHead className="text-right">费用</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {summary.by_model.map((row) => (
                  <TableRow key={row.bucket}>
                    <TableCell className="font-mono text-xs">{row.bucket}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {row.call_count.toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatCostUsd(row.cost_amount_micro, row.currency)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </section>
    </div>
  )
}
