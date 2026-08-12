'use client'

import { cn } from '@/lib/utils'

type ComposerOverlayShellProps = {
  children: React.ReactNode
  className?: string
  /** listbox / dialog a11y attrs */
  role?: string
  'aria-label'?: string
  'aria-activedescendant'?: string
  id?: string
  'data-testid'?: string
}

/**
 * Shared floating panel above the composer (slash palette, skills, MCP).
 * Uses existing popover tokens — no parallel design system.
 */
export function ComposerOverlayShell({
  children,
  className,
  role,
  id,
  'aria-label': ariaLabel,
  'aria-activedescendant': ariaActiveDescendant,
  'data-testid': testId,
}: ComposerOverlayShellProps): React.ReactElement {
  return (
    <div
      id={id}
      role={role}
      aria-label={ariaLabel}
      aria-activedescendant={ariaActiveDescendant}
      data-testid={testId}
      className={cn(
        'absolute bottom-full left-0 right-0 z-50 mb-1.5 flex max-h-72 flex-col',
        'overflow-hidden rounded-xl border border-border bg-popover text-popover-foreground shadow-md',
        className,
      )}
    >
      {children}
    </div>
  )
}
