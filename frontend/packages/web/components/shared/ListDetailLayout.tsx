'use client'

import type { ReactNode } from 'react'

import { useMediaQuery } from '@/hooks/useMediaQuery'
import { cn } from '@/lib/utils'

interface ListDetailLayoutProps {
  /** Always rendered as the list (fixed-width rail on desktop, full on mobile). */
  list: ReactNode
  /** Detail for the selected row; null shows the placeholder (desktop only). */
  detail: ReactNode | null
  /** Shown on the right when nothing is selected (desktop). */
  placeholder: ReactNode
  /** Whether a row is selected — drives the mobile full-screen overlay. */
  selected: boolean
  railClassName?: string
}

/**
 * Standard list-detail layout.
 *
 * Desktop (≥768px): a fixed-width list rail + a flex detail area; selecting a
 * row swaps the right side from the placeholder to the detail. The list rail
 * never resizes, so the layout doesn't jump.
 *
 * Mobile (<768px): a single column — the list fills the pane; selecting a row
 * opens the detail as a full-screen overlay whose own back control clears the
 * selection. Matches the AppShell mobile-drawer convention.
 */
export function ListDetailLayout({
  list,
  detail,
  placeholder,
  selected,
  railClassName,
}: ListDetailLayoutProps) {
  const isDesktop = useMediaQuery('(min-width: 768px)', true)

  if (!isDesktop) {
    return (
      <>
        <div className="flex-1 overflow-y-auto px-4 py-4">{list}</div>
        {selected && detail && (
          <div className="fixed inset-0 z-30 flex flex-col bg-background animate-in slide-in-from-right duration-slow">
            {detail}
          </div>
        )}
      </>
    )
  }

  return (
    <div className="flex flex-1 overflow-hidden">
      <aside
        className={cn(
          'w-[360px] shrink-0 overflow-y-auto border-r border-border/70 px-4 py-4',
          railClassName,
        )}
      >
        {list}
      </aside>
      <div className="flex flex-1 overflow-hidden">
        {detail ?? (
          <div className="flex flex-1 items-center justify-center p-8 text-center text-sm text-muted-foreground">
            {placeholder}
          </div>
        )}
      </div>
    </div>
  )
}
