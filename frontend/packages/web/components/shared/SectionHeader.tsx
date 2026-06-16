import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

/**
 * Unified content width for workspace settings & list panes. Content is
 * left-aligned (not centered) so list-detail views can break out into a
 * two-column layout without the content jumping horizontally.
 */
export const PANE_CONTENT_WIDTH = 'max-w-4xl'

/** Narrower width for single-column settings pages that don't need two columns. */
export const SETTINGS_CONTENT_WIDTH = 'max-w-3xl'

interface SectionHeaderProps {
  title: string
  description?: string
  /** at most one primary action, pinned to the content's right edge */
  action?: ReactNode
  /**
   * Width of the title row so the action aligns with the page body's right edge:
   * `true` (default) → {@link PANE_CONTENT_WIDTH}; a Tailwind `max-w-*` class →
   * that width (match the body); `false` → full pane width.
   */
  contained?: boolean | string
  className?: string
}

/**
 * Shared header for every workspace pane (settings tabs, scheduled tasks,
 * triggers, …). The separator spans the full pane; the title + action sit
 * inside `w-full` (capped at the unified width when `contained`) so the
 * action button lands in a stable place instead of drifting with content.
 */
export function SectionHeader({
  title,
  description,
  action,
  contained = true,
  className,
}: SectionHeaderProps) {
  const widthClass =
    contained === false ? undefined : typeof contained === 'string' ? contained : PANE_CONTENT_WIDTH
  return (
    <header className={cn('shrink-0 border-b border-border/70 px-6 py-4', className)}>
      <div className={cn('flex w-full items-center justify-between gap-4', widthClass)}>
        <div className="min-w-0">
          <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
          {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
        </div>
        {action && <div className="shrink-0">{action}</div>}
      </div>
    </header>
  )
}
