'use client'

import { Search } from 'lucide-react'
import { useTranslations } from 'next-intl'
import type { AdminCatalogFilter, MCPTemplateScope } from '@cubebox/core'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

interface MCPToolbarProps {
  search: string
  onSearchChange: (value: string) => void
  filter: AdminCatalogFilter
  onFilterChange: (value: AdminCatalogFilter) => void
  source: MCPTemplateScope | 'all'
  onSourceChange: (value: MCPTemplateScope | 'all') => void
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

export function MCPToolbar({
  search,
  onSearchChange,
  filter,
  onFilterChange,
  source,
  onSourceChange,
}: MCPToolbarProps) {
  const t = useTranslations('mcpAdmin')

  const FILTER_OPTIONS: { value: AdminCatalogFilter; label: string }[] = [
    { value: 'in_use', label: t('filterInUse') },
    { value: 'needs_attention', label: t('filterNeedsAttention') },
    { value: 'org_credential', label: t('filterOrgCredential') },
    { value: 'unused', label: t('filterUnused') },
    { value: 'all', label: t('filterAll') },
  ]

  const SOURCE_OPTIONS: { value: MCPTemplateScope | 'all'; label: string }[] = [
    { value: 'all', label: t('filterAllSources') },
    { value: 'global', label: t('sourceFilterGlobal') },
    { value: 'org', label: t('sourceFilterOrg') },
    { value: 'workspace', label: t('sourceFilterWorkspace') },
  ]

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border/70 px-4 py-3">
      <div className="relative min-w-[180px] flex-1">
        <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/70" />
        <Input
          type="search"
          placeholder={t('catalogSearchPlaceholder')}
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          name="mcp-admin-search"
          autoComplete="off"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          className="pl-7"
          aria-label={t('catalogSearchAriaLabel')}
        />
      </div>

      <PillGroup
        ariaLabel={t('catalogFilterAriaLabel')}
        options={FILTER_OPTIONS}
        value={filter}
        onChange={onFilterChange}
      />

      <PillGroup
        ariaLabel={t('catalogSourceAriaLabel')}
        options={SOURCE_OPTIONS}
        value={source}
        onChange={onSourceChange}
      />
    </div>
  )
}
