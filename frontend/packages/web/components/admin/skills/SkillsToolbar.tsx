'use client'

import { useEffect, useState } from 'react'
import { Search, Upload } from 'lucide-react'
import { useTranslations } from 'next-intl'
import type { SkillFilters, SkillSource } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

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

interface SkillsToolbarProps {
  filters: SkillFilters
  onFiltersChange: (next: SkillFilters) => void
  onUploadClick: () => void
  onExternalSearch: (q: string) => void
}

export function SkillsToolbar({
  filters,
  onFiltersChange,
  onUploadClick,
  onExternalSearch,
}: SkillsToolbarProps) {
  const t = useTranslations('adminSkills')
  const externalOnly = filters.externalOnly ?? false
  const [draft, setDraft] = useState(filters.q ?? '')

  useEffect(() => {
    if (!filters.q) setDraft('')
  }, [filters.q])

  function commitSearch() {
    const q = draft.trim()
    onFiltersChange({ ...filters, q: q || undefined })
    if (q) onExternalSearch(q)
  }

  const SOURCE_OPTIONS: { value: SkillSource | 'all' | 'external'; label: string }[] = [
    { value: 'all', label: t('sourceAll') },
    { value: 'preinstalled', label: t('sourcePreinstalled') },
    { value: 'uploaded', label: t('sourceUploaded') },
    { value: 'external', label: t('sourceExternal') },
  ]

  const INSTALLED_OPTIONS: { value: 'all' | 'installed' | 'uninstalled'; label: string }[] = [
    { value: 'all', label: t('statusAll') },
    { value: 'installed', label: t('statusInstalled') },
    { value: 'uninstalled', label: t('statusUninstalled') },
  ]

  const sourceValue: SkillSource | 'all' | 'external' = externalOnly
    ? 'external'
    : (filters.source ?? 'all')
  const installedValue: 'all' | 'installed' | 'uninstalled' =
    filters.installed === true ? 'installed' : filters.installed === false ? 'uninstalled' : 'all'

  function handleSourceChange(next: SkillSource | 'all' | 'external') {
    if (next === 'external') {
      onFiltersChange({ ...filters, source: undefined, externalOnly: true })
    } else {
      onFiltersChange({
        ...filters,
        externalOnly: false,
        source: next === 'all' ? undefined : (next as SkillSource),
      })
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border/70 px-4 py-3">
      <div className="relative min-w-[180px] flex-1">
        <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/70" />
        <Input
          type="search"
          placeholder={externalOnly ? t('externalSearchPlaceholder') : t('searchPlaceholder')}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) commitSearch()
          }}
          className="pl-7 pr-7"
          aria-label={t('searchAriaLabel')}
        />
        {draft && (
          <button
            type="button"
            onClick={commitSearch}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground/70 hover:text-foreground"
          >
            <Search className="size-3.5" />
          </button>
        )}
      </div>

      <PillGroup
        ariaLabel={t('filterBySource')}
        options={SOURCE_OPTIONS}
        value={sourceValue}
        onChange={handleSourceChange}
      />

      {!externalOnly && (
        <PillGroup
          ariaLabel={t('filterByStatus')}
          options={INSTALLED_OPTIONS}
          value={installedValue}
          onChange={(next) =>
            onFiltersChange({
              ...filters,
              installed: next === 'all' ? undefined : next === 'installed',
            })
          }
        />
      )}

      {!externalOnly && (
        <Button size="sm" onClick={onUploadClick} className="ml-auto">
          <Upload className="size-3.5" />
          {t('uploadButton')}
        </Button>
      )}
    </div>
  )
}
