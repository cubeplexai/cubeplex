'use client'

import { useState } from 'react'
import { Archive } from 'lucide-react'
import type { MemoryItem, MemoryType } from '@cubebox/core'
import { Badge } from '@/components/ui/badge'

interface MemoryItemCardProps {
  item: MemoryItem
  onArchive: (id: string) => Promise<void>
}

const TYPE_LABELS: Record<MemoryType, string> = {
  preference: 'Preference',
  project_fact: 'Fact',
  procedure: 'Procedure',
  correction: 'Correction',
  decision: 'Decision',
  org_policy: 'Org Policy',
}

const TYPE_COLORS: Record<MemoryType, string> = {
  preference: 'bg-info-surface text-info-fg',
  project_fact: 'bg-success-surface text-success-fg',
  procedure: 'bg-muted text-muted-foreground',
  correction: 'bg-warning-surface text-warning-fg',
  decision: 'bg-warning-surface text-warning-fg',
  org_policy: 'bg-danger-surface text-danger-fg',
}

function formatRelativeDate(dateStr: string): string {
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffDays = Math.floor(diffMs / 86400000)
  if (diffDays < 1) return 'Today'
  if (diffDays === 1) return 'Yesterday'
  if (diffDays < 30) return `${diffDays}d ago`
  const diffMonths = Math.floor(diffDays / 30)
  if (diffMonths < 12) return `${diffMonths}mo ago`
  return `${Math.floor(diffMonths / 12)}y ago`
}

export function MemoryItemCard({ item, onArchive }: MemoryItemCardProps) {
  const [archiving, setArchiving] = useState(false)

  const handleArchive = async () => {
    setArchiving(true)
    try {
      await onArchive(item.id)
    } finally {
      setArchiving(false)
    }
  }

  const confidencePct = Math.round(item.confidence * 100)
  const confidenceColor =
    item.confidence >= 0.8
      ? 'text-success-fg'
      : item.confidence >= 0.5
        ? 'text-warning-fg'
        : 'text-muted-foreground'

  return (
    <div className="group flex flex-col gap-2 rounded-xl border border-border bg-card px-4 py-3 shadow-sm transition-shadow hover:shadow-md">
      {/* Header row */}
      <div className="flex items-start gap-2">
        <div className="flex flex-1 flex-wrap items-center gap-1.5 min-w-0">
          <span
            className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${TYPE_COLORS[item.type]}`}
          >
            {TYPE_LABELS[item.type]}
          </span>
          {item.status === 'archived' && (
            <Badge variant="outline" className="text-[11px] text-muted-foreground">
              Archived
            </Badge>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2 ml-1">
          <span className={`text-xs font-medium tabular-nums ${confidenceColor}`}>
            {confidencePct}%
          </span>
          {item.status === 'active' && (
            <button
              onClick={handleArchive}
              disabled={archiving}
              className="opacity-0 group-hover:opacity-60 hover:!opacity-100 transition-opacity p-0.5 text-muted-foreground hover:text-foreground disabled:opacity-30"
              aria-label="Archive memory"
            >
              <Archive className="size-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* Content */}
      <p className="text-sm leading-relaxed text-foreground">{item.content}</p>

      {/* Footer */}
      <div className="flex items-center gap-1 text-[11px] text-muted-foreground/60">
        <span>Updated {formatRelativeDate(item.updated_at)}</span>
        {item.source_conversation_id && (
          <>
            <span>·</span>
            <span>from conversation</span>
          </>
        )}
      </div>
    </div>
  )
}
