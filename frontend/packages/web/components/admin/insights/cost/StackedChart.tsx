'use client'

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { TimeseriesResponse } from '@cubeplex/core'

interface Props {
  data: TimeseriesResponse
  palette: string[]
  height?: number
  formatValue?: (micro: number) => string
}

interface PivotRow {
  date: string
  [bucket: string]: number | string
}

function pivot(data: TimeseriesResponse): { rows: PivotRow[]; buckets: string[] } {
  const buckets = data.series.map((s) => s.bucket)
  const datesSet = new Set<string>()
  data.series.forEach((s) => s.points.forEach((p) => datesSet.add(p.date)))
  const dates = [...datesSet].sort()
  const rows: PivotRow[] = dates.map((date) => {
    const row: PivotRow = { date }
    data.series.forEach((s) => {
      const pt = s.points.find((p) => p.date === date)
      row[s.bucket] = pt ? pt.cost_amount_micro / 1_000_000 : 0
    })
    return row
  })
  return { rows, buckets }
}

export function StackedChart({ data, palette, height = 200, formatValue }: Props) {
  const { rows, buckets } = pivot(data)
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="date" tick={{ fontSize: 10 }} axisLine={false} tickLine={false} />
        <YAxis
          tick={{ fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          width={50}
          tickFormatter={(v: number | string) =>
            typeof v === 'number' ? `$${v.toFixed(0)}` : String(v)
          }
        />
        <Tooltip
          formatter={(v) => {
            if (typeof v !== 'number') return '—'
            return formatValue ? formatValue(v * 1_000_000) : `$${v.toFixed(2)}`
          }}
        />
        {buckets.map((b, i) => (
          <Area
            key={b}
            type="monotone"
            dataKey={b}
            stackId="cost"
            stroke={palette[Math.min(i, palette.length - 1)]}
            fill={palette[Math.min(i, palette.length - 1)]}
            fillOpacity={0.7}
            strokeWidth={1.2}
          />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  )
}
