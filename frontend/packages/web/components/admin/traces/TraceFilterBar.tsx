'use client'

import { useTranslations } from 'next-intl'
import type { TraceFilterValues } from './types'

interface Props {
  value: TraceFilterValues
  onChange: (next: TraceFilterValues) => void
}

export function TraceFilterBar({ value, onChange }: Props) {
  const t = useTranslations('adminTraces.filters')
  const field = (k: keyof TraceFilterValues, label: string, type = 'text') => (
    <label className="flex flex-col gap-1 text-xs text-muted-foreground">
      <span>{label}</span>
      <input
        type={type}
        value={(value[k] as string | number | undefined) ?? ''}
        onChange={(e) => onChange({ ...value, [k]: e.target.value || undefined })}
        className="rounded border border-border bg-card px-2 py-1 text-sm text-foreground"
      />
    </label>
  )
  const numField = (k: 'min_duration_ms' | 'max_duration_ms', label: string) => (
    <label className="flex flex-col gap-1 text-xs text-muted-foreground">
      <span>{label}</span>
      <input
        type="number"
        min={0}
        value={(value[k] as number | undefined) ?? ''}
        onChange={(e) => {
          const raw = e.target.value
          if (raw === '') {
            onChange({ ...value, [k]: undefined })
            return
          }
          const n = Number(raw)
          if (Number.isFinite(n)) {
            onChange({ ...value, [k]: n })
          }
          // Invalid (NaN/Infinity): ignore — browser type=number already constrains most cases.
        }}
        className="rounded border border-border bg-card px-2 py-1 text-sm text-foreground w-28"
      />
    </label>
  )
  return (
    <div className="flex flex-wrap gap-3 border-b border-border bg-card/40 px-4 py-3">
      {field('workspace_id', t('workspace'))}
      {field('user_id', t('user'))}
      {field('conversation_id', t('conversation'))}
      {field('model', t('model'))}
      {field('run_id', t('runId'))}
      {field('start', t('from'), 'datetime-local')}
      {field('end', t('to'), 'datetime-local')}
      {numField('min_duration_ms', t('minDurationMs'))}
      {numField('max_duration_ms', t('maxDurationMs'))}
    </div>
  )
}
