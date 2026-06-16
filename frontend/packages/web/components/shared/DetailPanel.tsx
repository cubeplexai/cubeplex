import type { ReactNode } from 'react'
import { ArrowLeft } from 'lucide-react'

import { cn } from '@/lib/utils'

interface DetailPanelProps {
  title: ReactNode
  /** status badge shown next to the title */
  badge?: ReactNode
  /** small line under the title (e.g. schedule summary, created date) */
  subtitle?: ReactNode
  /** right-aligned primary actions (delete, pause, …) */
  actions?: ReactNode
  /** when provided, a back/collapse control is shown on the left of the header */
  onBack?: () => void
  backLabel?: string
  children: ReactNode
  className?: string
}

/**
 * The one detail-panel shell for every list-detail page. Gives a consistent
 * header (back control + title + badge + subtitle + actions) and a scrolling
 * body, so trigger/scheduled/… details share the same chrome. On mobile the
 * back control returns to the list; on desktop it collapses to the placeholder.
 */
export function DetailPanel({
  title,
  badge,
  subtitle,
  actions,
  onBack,
  backLabel,
  children,
  className,
}: DetailPanelProps) {
  return (
    <div className={cn('flex h-full flex-col overflow-hidden', className)}>
      <div className="flex shrink-0 flex-col gap-2 border-b border-border/70 px-6 py-3 sm:flex-row sm:items-center sm:gap-3">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          {onBack && (
            <button
              type="button"
              onClick={onBack}
              aria-label={backLabel ?? 'Back'}
              className="grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <ArrowLeft className="size-4" />
            </button>
          )}
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-lg font-semibold tracking-tight">{title}</h2>
              {badge}
            </div>
            {subtitle && (
              <p className="mt-0.5 truncate text-xs text-muted-foreground">{subtitle}</p>
            )}
          </div>
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
      <div className="flex-1 overflow-y-auto px-6 py-5">{children}</div>
    </div>
  )
}
