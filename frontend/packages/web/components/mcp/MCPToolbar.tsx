'use client'

import { Search } from 'lucide-react'
import { useTranslations } from 'next-intl'
import type { MCPConnectorFilter } from '@cubebox/core'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

interface MCPToolbarProps {
  search: string
  onSearchChange: (value: string) => void
  filter: MCPConnectorFilter
  onFilterChange: (value: MCPConnectorFilter) => void
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

export function MCPToolbar({ search, onSearchChange, filter, onFilterChange }: MCPToolbarProps) {
  const t = useTranslations('mcpAdmin')

  const FILTER_OPTIONS: { value: MCPConnectorFilter; label: string }[] = [
    { value: 'all', label: t('filterAll') },
    { value: 'installed', label: t('filterInstalled') },
    { value: 'available', label: t('filterAvailable') },
    { value: 'custom', label: t('filterCustom') },
  ]

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border/70 px-4 py-3">
      <div className="relative min-w-[180px] flex-1">
        <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/70" />
        <Input
          type="search"
          placeholder={t('searchPlaceholder')}
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          name="mcp-admin-search"
          autoComplete="off"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          className="pl-7"
          aria-label={t('searchAriaLabel')}
        />
      </div>

      <PillGroup
        ariaLabel={t('filterByStatus')}
        options={FILTER_OPTIONS}
        value={filter}
        onChange={onFilterChange}
      />
    </div>
  )
}
