'use client'

import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { CostFilters, RangePreset } from '@/hooks/useCostData'

interface Props {
  filters: CostFilters
  onChange: (next: CostFilters) => void
  availableWorkspaces: { id: string; name: string }[]
  availableModels: string[]
}

const RANGES: RangePreset[] = ['7d', '30d', '90d']

export function InsightsFilterSidebar({
  filters,
  onChange,
  availableWorkspaces,
  availableModels,
}: Props) {
  const t = useTranslations('adminInsights.filters')

  function toggle<T>(list: T[], v: T): T[] {
    return list.includes(v) ? list.filter((x) => x !== v) : [...list, v]
  }

  return (
    <aside
      className="w-52 shrink-0 border-r border-border/70 bg-card/40 p-3 text-xs space-y-5
                 overflow-y-auto"
      aria-label="filters"
    >
      <section>
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {t('range')}
        </p>
        <div className="grid grid-cols-3 gap-1">
          {RANGES.map((r) => (
            <Button
              key={r}
              size="sm"
              variant={filters.range === r ? 'default' : 'outline'}
              className="h-7 text-xs"
              onClick={() => onChange({ ...filters, range: r })}
            >
              {r}
            </Button>
          ))}
        </div>
      </section>

      {availableWorkspaces.length > 0 && (
        <section>
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {t('workspaces')}
          </p>
          <div className="space-y-1">
            {availableWorkspaces.map((w) => {
              const on = filters.workspaceIds.includes(w.id)
              return (
                <button
                  key={w.id}
                  onClick={() =>
                    onChange({ ...filters, workspaceIds: toggle(filters.workspaceIds, w.id) })
                  }
                  className={cn(
                    'w-full rounded-md px-2 py-1 text-left text-xs',
                    on ? 'bg-primary/10 text-primary' : 'hover:bg-muted',
                  )}
                >
                  {w.name}
                </button>
              )
            })}
          </div>
        </section>
      )}

      {availableModels.length > 0 && (
        <section>
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {t('models')}
          </p>
          <div className="space-y-1">
            {availableModels.map((m) => {
              const on = filters.models.includes(m)
              return (
                <button
                  key={m}
                  onClick={() => onChange({ ...filters, models: toggle(filters.models, m) })}
                  className={cn(
                    'w-full rounded-md px-2 py-1 text-left text-[11px] font-mono',
                    on ? 'bg-primary/10 text-primary' : 'hover:bg-muted',
                  )}
                >
                  {m}
                </button>
              )
            })}
          </div>
        </section>
      )}

      <section>
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {t('granularity')}
        </p>
        <div className="grid grid-cols-2 gap-1">
          {(['day', 'week'] as const).map((g) => (
            <Button
              key={g}
              size="sm"
              variant={filters.granularity === g ? 'default' : 'outline'}
              className="h-7 text-xs"
              onClick={() => onChange({ ...filters, granularity: g })}
            >
              {t(g)}
            </Button>
          ))}
        </div>
      </section>
    </aside>
  )
}
