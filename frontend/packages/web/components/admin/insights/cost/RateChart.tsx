'use client'

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

export interface RateSeries {
  bucket: string
  points: { date: string; rate: number | null }[]
  color: string
}

interface Props {
  series: RateSeries[]
  orgAvg?: { date: string; rate: number | null }[]
  height?: number
}

interface PivotRow {
  date: string
  [bucket: string]: number | string | null
}

export function RateChart({ series, orgAvg, height = 200 }: Props) {
  const datesSet = new Set<string>()
  series.forEach((s) => s.points.forEach((p) => datesSet.add(p.date)))
  orgAvg?.forEach((p) => datesSet.add(p.date))
  const dates = [...datesSet].sort()
  const rows: PivotRow[] = dates.map((date) => {
    const row: PivotRow = { date }
    series.forEach((s) => {
      const pt = s.points.find((p) => p.date === date)
      row[s.bucket] = pt && pt.rate !== null ? pt.rate * 100 : null
    })
    if (orgAvg) {
      const pt = orgAvg.find((p) => p.date === date)
      row.__avg = pt && pt.rate !== null ? pt.rate * 100 : null
    }
    return row
  })
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="date" tick={{ fontSize: 10 }} axisLine={false} tickLine={false} />
        <YAxis
          domain={[0, 100]}
          tick={{ fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          width={36}
          tickFormatter={(v) => `${v}%`}
        />
        <Tooltip formatter={(v) => (typeof v === 'number' ? `${v.toFixed(1)}%` : '—')} />
        {series.map((s) => (
          <Line
            key={s.bucket}
            type="monotone"
            dataKey={s.bucket}
            stroke={s.color}
            strokeWidth={1.8}
            dot={false}
            connectNulls
          />
        ))}
        {orgAvg && (
          <Line
            type="monotone"
            dataKey="__avg"
            stroke="hsl(var(--muted-foreground))"
            strokeWidth={1.2}
            strokeDasharray="4 4"
            dot={false}
            connectNulls
          />
        )}
      </LineChart>
    </ResponsiveContainer>
  )
}
