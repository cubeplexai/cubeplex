import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

interface RailCardProps {
  title: ReactNode
  /** leading visual (avatar / logo) shown to the left of the content */
  leading?: ReactNode
  /** status pill / badge shown next to the title */
  badge?: ReactNode
  /** primary secondary line (e.g. schedule, source) */
  secondary?: ReactNode
  /** dimmer meta line below the secondary line */
  meta?: ReactNode
  /** trailing actions (e.g. a `...` menu); clicks here don't select the card */
  actions?: ReactNode
  selected?: boolean
  onSelect?: () => void
  className?: string
  'data-testid'?: string
}

/**
 * The one list-item style for every list-detail rail (triggers, scheduled
 * tasks, …). Gives a single selected state (left accent bar + primary tint),
 * hover, padding and click target so the rails look and behave the same.
 */
export function RailCard({
  title,
  leading,
  badge,
  secondary,
  meta,
  actions,
  selected,
  onSelect,
  className,
  'data-testid': testId,
}: RailCardProps) {
  return (
    <div
      role={onSelect ? 'button' : undefined}
      tabIndex={onSelect ? 0 : undefined}
      onClick={onSelect}
      onKeyDown={
        onSelect
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onSelect()
              }
            }
          : undefined
      }
      data-testid={testId}
      className={cn(
        'group relative rounded-xl border px-4 py-3 transition-colors',
        onSelect && 'cursor-pointer',
        selected
          ? 'border-primary/40 bg-primary/5'
          : 'border-border hover:border-border/80 hover:bg-muted/30',
        className,
      )}
    >
      {selected && (
        <span className="absolute left-0 top-1/2 h-8 w-0.5 -translate-x-px -translate-y-1/2 rounded-r-full bg-primary" />
      )}
      <div className="flex items-start justify-between gap-2">
        {leading && <div className="shrink-0">{leading}</div>}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-semibold">{title}</span>
            {badge}
          </div>
          {secondary && (
            <div className="mt-0.5 truncate text-xs text-muted-foreground">{secondary}</div>
          )}
          {meta && <div className="mt-1 text-[11px] text-muted-foreground/70">{meta}</div>}
        </div>
        {actions && (
          <div className="shrink-0" onClick={(e) => e.stopPropagation()}>
            {actions}
          </div>
        )}
      </div>
    </div>
  )
}
