'use client'

import { Plus, Search } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

export type ProviderKind = 'all' | 'system' | 'custom'

interface ModelsToolbarProps {
  query: string
  kind: ProviderKind
  onQueryChange: (next: string) => void
  onKindChange: (next: ProviderKind) => void
  onAddClick: () => void
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

export function ModelsToolbar({
  query,
  kind,
  onQueryChange,
  onKindChange,
  onAddClick,
}: ModelsToolbarProps) {
  const t = useTranslations('adminModels')

  const KIND_OPTIONS: { value: ProviderKind; label: string }[] = [
    { value: 'all', label: t('kindAll') },
    { value: 'system', label: t('kindSystem') },
    { value: 'custom', label: t('kindCustom') },
  ]

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border/70 px-4 py-3">
      <div className="relative min-w-[180px] flex-1">
        <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/70" />
        <Input
          type="search"
          placeholder={t('searchPlaceholder')}
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          className="pl-7"
          aria-label={t('searchAriaLabel')}
        />
      </div>

      <PillGroup
        ariaLabel={t('filterByKind')}
        options={KIND_OPTIONS}
        value={kind}
        onChange={onKindChange}
      />

      <Button size="sm" onClick={onAddClick} className="ml-auto">
        <Plus className="size-3.5" />
        {t('addProvider')}
      </Button>
    </div>
  )
}
