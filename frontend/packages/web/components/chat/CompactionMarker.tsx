'use client'

import { useTranslations } from 'next-intl'
import { Layers2 } from 'lucide-react'
import { cn } from '@/lib/utils'

type Props = {
  /** Optional compact source label (manual / auto). */
  source?: string
  className?: string
}

/**
 * Timeline divider for a force-compact (or future auto-compact) event.
 * Not a user bubble — does not render the /compact command text.
 */
export function CompactionMarker({ source, className }: Props): React.ReactElement {
  const t = useTranslations('chat.compactionMarker')
  const label = source === 'manual' ? t('manual') : source === 'auto' ? t('auto') : t('label')

  return (
    <div
      role="status"
      data-testid="compaction-marker"
      className={cn('flex items-center gap-3 py-2', className)}
      aria-label={label}
    >
      <div className="h-px flex-1 bg-border" />
      <span
        className={cn(
          'inline-flex items-center gap-1.5 rounded-full border border-border',
          'bg-muted/40 px-2.5 py-0.5 text-[11px] text-muted-foreground',
        )}
      >
        <Layers2 aria-hidden className="size-3" />
        {label}
      </span>
      <div className="h-px flex-1 bg-border" />
    </div>
  )
}

/** True when a history row is a compaction timeline marker. */
export function isCompactionMarkerMessage(msg: {
  role: string
  metadata?: Record<string, unknown> | null
}): boolean {
  if (msg.role !== 'user') return false
  const meta = msg.metadata
  if (!meta || meta.synthetic !== true) return false
  return meta.synthetic_source === 'compaction' || meta.kind === 'compaction'
}
