'use client'

import { useState, type ReactNode } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { Card } from '@/components/ui/card'

interface Props {
  title: string
  icon?: ReactNode
  defaultOpen?: boolean
  children: ReactNode
}

// Shared collapsible card shell for the span detail panel (LlmCard, ToolCard,
// SpanDetail's turn/raw-attributes blocks) so they all look like one system
// instead of each hand-rolling a border box. Conditional-render collapse
// (not components/ui/collapsible.tsx, which has no actual show/hide wiring).
export function Section({ title, icon, defaultOpen = true, children }: Props) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <Card className="gap-0 py-0">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between gap-2 px-4 py-2.5 text-left"
      >
        <span className="flex items-center gap-2 text-sm font-medium">
          {icon}
          {title}
        </span>
        {open ? (
          <ChevronDown className="size-4 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-4 text-muted-foreground" />
        )}
      </button>
      {open && <div className="border-t border-border/60 px-4 py-3">{children}</div>}
    </Card>
  )
}
