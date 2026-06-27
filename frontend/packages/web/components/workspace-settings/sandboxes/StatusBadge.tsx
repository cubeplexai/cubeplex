'use client'

import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'

/** Status → tailwind tone classes (semantic surface/fg tokens, globals.css). */
const STATUS_TONE: Record<string, string> = {
  running: 'bg-success-surface text-success-fg',
  paused: 'bg-warning-surface text-warning-fg',
  pausing: 'bg-warning-surface text-warning-fg',
  resuming: 'bg-warning-surface text-warning-fg',
  provisioning: 'bg-info-surface text-info-fg',
  terminated: 'bg-muted text-muted-foreground',
  failed: 'bg-destructive/10 text-destructive',
  kill_pending: 'bg-warning-surface text-warning-fg',
}

type StatusLabelKey =
  | 'statusRunning'
  | 'statusPaused'
  | 'statusPausing'
  | 'statusResuming'
  | 'statusStarting'
  | 'statusOff'
  | 'statusFailed'
  | 'statusStopping'
  | 'statusUnknown'

const STATUS_LABEL_KEY: Record<string, StatusLabelKey> = {
  running: 'statusRunning',
  paused: 'statusPaused',
  pausing: 'statusPausing',
  resuming: 'statusResuming',
  provisioning: 'statusStarting',
  terminated: 'statusOff',
  failed: 'statusFailed',
  kill_pending: 'statusStopping',
}

/**
 * Colored pill for a sandbox runtime status. `terminated` renders as "Off"
 * (muted) — the row is alive but the container is stopped, matching the
 * user-facing model (spec §7.5).
 */
export function StatusBadge({ status }: { status: string }): React.ReactElement {
  const t = useTranslations('wsSandboxes')
  const tone = STATUS_TONE[status] ?? 'bg-muted text-muted-foreground'
  const labelKey: StatusLabelKey = STATUS_LABEL_KEY[status] ?? 'statusUnknown'
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-medium',
        tone,
      )}
    >
      {t(labelKey)}
    </span>
  )
}
