'use client'

import { useCallback } from 'react'
import { useTranslations } from 'next-intl'
import { FilterCombobox, type FilterComboboxOption } from './FilterCombobox'
import type { FilterOptionKind, TraceFilterValues } from './types'
import { getAdminFilterOptions, getAdminTraceTagValues } from '@/lib/api/admin-traces'

interface Props {
  value: TraceFilterValues
  onChange: (next: TraceFilterValues) => void
}

// Map a Postgres filter-options kind to a list/typeahead loader returning
// {value: id, label: name} pairs.
function useFilterOptionsLoader(kind: FilterOptionKind) {
  return useCallback(
    async (_q: string, signal: AbortSignal): Promise<FilterComboboxOption[]> => {
      const opts = await getAdminFilterOptions(kind, _q || undefined, signal)
      return opts.map((o) => ({ value: o.id, label: o.name }))
    },
    [kind],
  )
}

// model is low-cardinality and sourced from Tempo tag-values; the value is its
// own label.
function useModelLoader() {
  return useCallback(async (_q: string, signal: AbortSignal): Promise<FilterComboboxOption[]> => {
    const values = await getAdminTraceTagValues('gen_ai.request.model', signal)
    return values.map((v) => ({ value: v, label: v }))
  }, [])
}

export function TraceFilterBar({ value, onChange }: Props) {
  const t = useTranslations('adminTraces.filters')
  const loadWorkspace = useFilterOptionsLoader('workspace')
  const loadUser = useFilterOptionsLoader('user')
  const loadConversation = useFilterOptionsLoader('conversation')
  const loadModel = useModelLoader()

  const field = (k: keyof TraceFilterValues, label: string, type = 'text') => (
    <label className="flex flex-col gap-1 text-xs text-muted-foreground">
      <span>{label}</span>
      <input
        type={type}
        value={(value[k] as string | number | undefined) ?? ''}
        onChange={(e) => onChange({ ...value, [k]: e.target.value || undefined })}
        className="h-8 w-44 rounded border border-border bg-card px-2 py-1 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
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
          // Invalid (NaN/Infinity): ignore - browser type=number already constrains most cases.
        }}
        className="h-8 w-28 rounded border border-border bg-card px-2 py-1 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
      />
    </label>
  )

  return (
    <div className="flex flex-wrap gap-3 border-b border-border bg-card/40 px-4 py-3">
      <FilterCombobox
        label={t('workspace')}
        value={value.workspace_id}
        onChange={(v) => onChange({ ...value, workspace_id: v })}
        loadOptions={loadWorkspace}
        mode="list"
        placeholder={t('searchPlaceholder')}
      />
      <FilterCombobox
        label={t('user')}
        value={value.user_id}
        onChange={(v) => onChange({ ...value, user_id: v })}
        loadOptions={loadUser}
        mode="typeahead"
        placeholder={t('searchPlaceholder')}
      />
      <FilterCombobox
        label={t('conversation')}
        value={value.conversation_id}
        onChange={(v) => onChange({ ...value, conversation_id: v })}
        loadOptions={loadConversation}
        mode="typeahead"
        placeholder={t('searchPlaceholder')}
      />
      <FilterCombobox
        label={t('model')}
        value={value.model}
        onChange={(v) => onChange({ ...value, model: v })}
        loadOptions={loadModel}
        mode="list"
        placeholder={t('searchPlaceholder')}
      />
      {field('run_id', t('runId'))}
      {field('start', t('from'), 'datetime-local')}
      {field('end', t('to'), 'datetime-local')}
      {numField('min_duration_ms', t('minDurationMs'))}
      {numField('max_duration_ms', t('maxDurationMs'))}
    </div>
  )
}
