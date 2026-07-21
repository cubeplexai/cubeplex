'use client'

import { useCallback } from 'react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { FilterCombobox, type FilterComboboxOption } from './FilterCombobox'
import type { FilterOptionKind, TimeRangePreset, TraceFilterValues } from './types'
import { getAdminFilterOptions, getAdminTraceTagValues } from '@/lib/api/admin-traces'

interface Props {
  value: TraceFilterValues
  onChange: (next: TraceFilterValues) => void
  preset: TimeRangePreset
  onPresetChange: (next: TimeRangePreset) => void
  // Resolved names for deep-linked workspace_id/user_id/conversation_id (e.g.
  // from a shared URL), so the combobox shows a name instead of the raw id.
  workspaceLabel?: string
  userLabel?: string
  conversationLabel?: string
}

const PRESETS: TimeRangePreset[] = ['1h', '1d', '7d', 'custom']
const LIMIT_OPTIONS = [25, 50, 100]

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

export function TraceFilterBar({
  value,
  onChange,
  preset,
  onPresetChange,
  workspaceLabel,
  userLabel,
  conversationLabel,
}: Props) {
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

  return (
    <div className="flex flex-col gap-3 border-b border-border bg-card/40 px-4 py-3">
      <div className="flex flex-wrap gap-3">
        <FilterCombobox
          label={t('workspace')}
          value={value.workspace_id}
          onChange={(v) => onChange({ ...value, workspace_id: v })}
          loadOptions={loadWorkspace}
          mode="list"
          placeholder={t('searchPlaceholder')}
          initialLabel={workspaceLabel}
        />
        <FilterCombobox
          label={t('user')}
          value={value.user_id}
          onChange={(v) => onChange({ ...value, user_id: v })}
          loadOptions={loadUser}
          mode="typeahead"
          placeholder={t('searchPlaceholder')}
          initialLabel={userLabel}
        />
        <FilterCombobox
          label={t('conversation')}
          value={value.conversation_id}
          onChange={(v) => onChange({ ...value, conversation_id: v })}
          loadOptions={loadConversation}
          mode="typeahead"
          placeholder={t('searchPlaceholder')}
          initialLabel={conversationLabel}
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
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1 text-xs text-muted-foreground">
          <span>{t('range')}</span>
          <div className="flex gap-1">
            {PRESETS.map((p) => (
              <Button
                key={p}
                type="button"
                size="sm"
                variant={preset === p ? 'default' : 'outline'}
                onClick={() => onPresetChange(p)}
              >
                {t(`rangePreset.${p}`)}
              </Button>
            ))}
          </div>
        </div>
        {preset === 'custom' && (
          <>
            {field('start', t('from'), 'datetime-local')}
            {field('end', t('to'), 'datetime-local')}
          </>
        )}
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          <span>{t('limit')}</span>
          <Select
            value={String(value.limit ?? LIMIT_OPTIONS[1])}
            onValueChange={(v) => onChange({ ...value, limit: Number(v) })}
          >
            <SelectTrigger size="sm" className="w-20">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {LIMIT_OPTIONS.map((n) => (
                <SelectItem key={n} value={String(n)}>
                  {n}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
      </div>
    </div>
  )
}
