'use client'

import { useTranslations } from 'next-intl'
import { Layers2, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

type Props = {
  /** Optional compact source label (manual / auto). Ignored while pending. */
  source?: string
  /** In-flight compact. Not a history row — the open page draws this itself. */
  pending?: boolean
  className?: string
}

/**
 * Timeline divider for a force-compact (or future auto-compact) event.
 * Not a user bubble — does not render the /compact command text.
 */
export function CompactionMarker({
  source,
  pending = false,
  className,
}: Props): React.ReactElement {
  const t = useTranslations('chat.compactionMarker')
  const label = pending
    ? t('pending')
    : source === 'manual'
      ? t('manual')
      : source === 'auto'
        ? t('auto')
        : t('label')

  return (
    <div
      role="status"
      data-testid="compaction-marker"
      className={cn('flex items-center gap-3 py-2', className)}
      aria-busy={pending || undefined}
      aria-label={label}
    >
      <div className="h-px flex-1 bg-border" />
      <span
        className={cn(
          'inline-flex items-center gap-1.5 rounded-full border border-border',
          'bg-muted/40 px-2.5 py-0.5 text-[11px] text-muted-foreground',
        )}
      >
        {pending ? (
          <Loader2 aria-hidden className="size-3 animate-spin" />
        ) : (
          <Layers2 aria-hidden className="size-3" />
        )}
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

/**
 * The waiting divider sits under history only while this page is compacting
 * and the tail is not already the saved marker. That avoids a one-frame
 * double line when the marker is appended before the flag clears.
 */
export function showCompactionPendingLine(
  compacting: boolean,
  lastMessage: { role: string; metadata?: Record<string, unknown> | null } | null | undefined,
): boolean {
  if (!compacting) return false
  if (lastMessage != null && isCompactionMarkerMessage(lastMessage)) return false
  return true
}
