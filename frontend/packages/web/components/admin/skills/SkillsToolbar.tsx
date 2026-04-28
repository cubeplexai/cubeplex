'use client'

import { Search, Upload } from 'lucide-react'
import { useTranslations } from 'next-intl'
import type { SkillFilters, SkillSource } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

interface SkillsToolbarProps {
  filters: SkillFilters
  onFiltersChange: (next: SkillFilters) => void
  onUploadClick: () => void
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

export function SkillsToolbar({ filters, onFiltersChange, onUploadClick }: SkillsToolbarProps) {
  const t = useTranslations('adminSkills')

  const SOURCE_OPTIONS: { value: SkillSource | 'all'; label: string }[] = [
    { value: 'all', label: t('sourceAll') },
    { value: 'preinstalled', label: t('sourcePreinstalled') },
    { value: 'uploaded', label: t('sourceUploaded') },
  ]

  const INSTALLED_OPTIONS: { value: 'all' | 'installed' | 'uninstalled'; label: string }[] = [
    { value: 'all', label: t('statusAll') },
    { value: 'installed', label: t('statusInstalled') },
    { value: 'uninstalled', label: t('statusUninstalled') },
  ]

  const sourceValue: SkillSource | 'all' = filters.source ?? 'all'
  const installedValue: 'all' | 'installed' | 'uninstalled' =
    filters.installed === true ? 'installed' : filters.installed === false ? 'uninstalled' : 'all'

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border/70 px-4 py-3">
      <div className="relative min-w-[180px] flex-1">
        <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/70" />
        <Input
          type="search"
          placeholder={t('searchPlaceholder')}
          value={filters.q ?? ''}
          onChange={(e) => onFiltersChange({ ...filters, q: e.target.value || undefined })}
          className="pl-7"
          aria-label={t('searchAriaLabel')}
        />
      </div>

      <PillGroup
        ariaLabel={t('filterBySource')}
        options={SOURCE_OPTIONS}
        value={sourceValue}
        onChange={(next) =>
          onFiltersChange({ ...filters, source: next === 'all' ? undefined : next })
        }
      />

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

      <Button size="sm" onClick={onUploadClick} className="ml-auto">
        <Upload className="size-3.5" />
        {t('uploadButton')}
      </Button>
    </div>
  )
}
