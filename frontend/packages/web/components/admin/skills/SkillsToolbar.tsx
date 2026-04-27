'use client'

import { Search, Upload } from 'lucide-react'
import type { SkillFilters, SkillSource } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

interface SkillsToolbarProps {
  filters: SkillFilters
  onFiltersChange: (next: SkillFilters) => void
  onUploadClick: () => void
}

const SOURCE_OPTIONS: { value: SkillSource | 'all'; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'preinstalled', label: '内置' },
  { value: 'uploaded', label: '组织上传' },
]

const INSTALLED_OPTIONS: { value: 'all' | 'installed' | 'uninstalled'; label: string }[] = [
  { value: 'all', label: '全部状态' },
  { value: 'installed', label: '已安装' },
  { value: 'uninstalled', label: '未安装' },
]

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
  const sourceValue: SkillSource | 'all' = filters.source ?? 'all'
  const installedValue: 'all' | 'installed' | 'uninstalled' =
    filters.installed === true ? 'installed' : filters.installed === false ? 'uninstalled' : 'all'

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border/70 px-4 py-3">
      <div className="relative min-w-[180px] flex-1">
        <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/70" />
        <Input
          type="search"
          placeholder="搜索 skill 名称或描述…"
          value={filters.q ?? ''}
          onChange={(e) => onFiltersChange({ ...filters, q: e.target.value || undefined })}
          className="pl-7"
          aria-label="搜索 skill"
        />
      </div>

      <PillGroup
        ariaLabel="按来源过滤"
        options={SOURCE_OPTIONS}
        value={sourceValue}
        onChange={(next) =>
          onFiltersChange({ ...filters, source: next === 'all' ? undefined : next })
        }
      />

      <PillGroup
        ariaLabel="按安装状态过滤"
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
        上传 skill
      </Button>
    </div>
  )
}
