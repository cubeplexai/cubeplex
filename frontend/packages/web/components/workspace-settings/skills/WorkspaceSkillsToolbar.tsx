'use client'

import { Plus, Search } from 'lucide-react'
import type { SkillSource } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import type { WorkspaceSkillFilters } from '@/hooks/useWorkspaceSkillsCatalog'

interface WorkspaceSkillsToolbarProps {
  filters: WorkspaceSkillFilters
  onFiltersChange: (next: WorkspaceSkillFilters) => void
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

const SOURCE_OPTIONS: { value: SkillSource | 'all'; label: string }[] = [
  { value: 'all', label: 'All sources' },
  { value: 'preinstalled', label: 'Preinstalled' },
  { value: 'uploaded', label: 'Uploaded' },
]

const STATE_OPTIONS: { value: 'all' | 'enabled' | 'disabled' | 'available'; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'enabled', label: 'Enabled' },
  { value: 'disabled', label: 'Disabled' },
  { value: 'available', label: 'Available' },
]

export function WorkspaceSkillsToolbar({
  filters,
  onFiltersChange,
  onAddClick,
}: WorkspaceSkillsToolbarProps) {
  const sourceValue: SkillSource | 'all' = filters.source ?? 'all'
  const stateValue = filters.state ?? 'all'

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border/70 px-4 py-3">
      <div className="relative min-w-[180px] flex-1">
        <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/70" />
        <Input
          type="search"
          placeholder="Search by name or description"
          value={filters.q ?? ''}
          onChange={(e) => onFiltersChange({ ...filters, q: e.target.value || undefined })}
          className="pl-7"
          aria-label="Search skills"
        />
      </div>

      <PillGroup
        ariaLabel="Filter by source"
        options={SOURCE_OPTIONS}
        value={sourceValue}
        onChange={(next) =>
          onFiltersChange({ ...filters, source: next === 'all' ? undefined : next })
        }
      />

      <PillGroup
        ariaLabel="Filter by state"
        options={STATE_OPTIONS}
        value={stateValue}
        onChange={(next) => onFiltersChange({ ...filters, state: next })}
      />

      <Button size="sm" onClick={onAddClick} className="ml-auto">
        <Plus className="size-3.5" />
        Add skill
      </Button>
    </div>
  )
}
