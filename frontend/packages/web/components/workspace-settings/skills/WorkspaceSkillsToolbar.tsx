'use client'

import { useEffect, useState } from 'react'
import { Plus, Search } from 'lucide-react'
import type { SkillSource } from '@cubeplex/core'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import type { WorkspaceSkillFilters } from '@/hooks/useWorkspaceSkillsCatalog'

interface WorkspaceSkillsToolbarProps {
  filters: WorkspaceSkillFilters
  onFiltersChange: (next: WorkspaceSkillFilters) => void
  onAddClick: () => void
  onSearch?: (q: string) => void
}

function PillGroup<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  options: { value: T; label: string }[]
  value: T
  onChange: (next: T) => void
  ariaLabel: string
}) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className="inline-flex items-center gap-0.5 rounded-lg border border-border bg-muted/30 p-0.5"
    >
      {options.map((opt) => {
        const active = opt.value === value
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            className={cn(
              'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
              active
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}

export function WorkspaceSkillsToolbar({
  filters,
  onFiltersChange,
  onAddClick,
  onSearch,
}: WorkspaceSkillsToolbarProps) {
  const t = useTranslations('wsSettings.skillsToolbar')
  const sourceValue: SkillSource | 'all' | 'external' = filters.externalOnly
    ? 'external'
    : (filters.source ?? 'all')
  const stateValue = filters.state ?? 'all'
  const [draft, setDraft] = useState(filters.q ?? '')

  // Sync draft when parent clears the query (e.g. on unmount/reset).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!filters.q) setDraft('')
  }, [filters.q])

  function commitSearch() {
    const q = draft.trim()
    onFiltersChange({ ...filters, q: q || undefined })
    if (q) onSearch?.(q)
  }

  const sourceOptions: { value: SkillSource | 'all' | 'external'; label: string }[] = [
    { value: 'all', label: t('sourceAll') },
    { value: 'preinstalled', label: t('sourcePreinstalled') },
    { value: 'uploaded', label: t('sourceUploaded') },
    { value: 'external', label: t('sourceExternal') },
  ]

  const stateOptions: { value: 'all' | 'enabled' | 'disabled' | 'available'; label: string }[] = [
    { value: 'all', label: t('stateAll') },
    { value: 'enabled', label: t('stateEnabled') },
    { value: 'disabled', label: t('stateDisabled') },
    { value: 'available', label: t('stateAvailable') },
  ]

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border/70 px-4 py-3">
      <div className="relative min-w-[180px] flex-1">
        <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/70" />
        <Input
          type="search"
          placeholder={t('searchPlaceholder')}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) commitSearch()
          }}
          className="pl-7 pr-7"
          aria-label={t('searchAria')}
        />
        {draft && (
          <button
            type="button"
            onClick={commitSearch}
            aria-label="Search"
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground/70 hover:text-foreground"
          >
            <Search className="size-3.5" />
          </button>
        )}
      </div>

      <PillGroup
        ariaLabel={t('filterSourceAria')}
        options={sourceOptions}
        value={sourceValue}
        onChange={(next) => {
          if (next === 'external') {
            onFiltersChange({ ...filters, source: undefined, externalOnly: true })
          } else {
            onFiltersChange({
              ...filters,
              source: next === 'all' ? undefined : (next as SkillSource),
              externalOnly: false,
            })
          }
        }}
      />

      {!filters.externalOnly && (
        <PillGroup
          ariaLabel={t('filterStateAria')}
          options={stateOptions}
          value={stateValue}
          onChange={(next) => onFiltersChange({ ...filters, state: next })}
        />
      )}

      <Button size="sm" onClick={onAddClick} className="ml-auto">
        <Plus className="size-3.5" />
        {t('addSkill')}
      </Button>
    </div>
  )
}
