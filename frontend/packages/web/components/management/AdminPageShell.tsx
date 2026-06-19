import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

interface AdminPageShellProps {
  title: string
  description?: ReactNode
  /** at most one primary action, right-aligned in the header */
  action?: ReactNode
  children: ReactNode
  /** content max-width; defaults to the single-column admin width */
  className?: string
}

/**
 * Standard layout for admin single-column pages (General, Members, Sandbox
 * policy, …). The header separator spans the full pane, but the header's
 * title/action sit in the same centered column as the body, so the header
 * lines up with the content width below it.
 */
export function AdminPageShell({
  title,
  description,
  action,
  children,
  className,
}: AdminPageShellProps) {
  return (
    <div className="flex h-full flex-col">
      <header className="shrink-0 border-b border-border px-6 pt-5 pb-4">
        <div className="mx-auto flex w-full max-w-3xl items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
            {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
          </div>
          {action}
        </div>
      </header>
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className={cn('mx-auto flex w-full max-w-3xl flex-col gap-6', className)}>
          {children}
        </div>
      </div>
    </div>
  )
}
