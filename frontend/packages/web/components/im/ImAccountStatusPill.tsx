'use client'

import { AlertTriangle, CheckCircle2, MinusCircle, PauseCircle } from 'lucide-react'
import { useTranslations } from 'next-intl'

import type { ImConnectionState } from '@cubeplex/core'
import { cn } from '@/lib/utils'

interface Props {
  connectionState: ImConnectionState
  enabled: boolean
  className?: string
}

/**
 * Pill that shows a bot's runtime state. Shape + text + color together
 * meet color-independent a11y; ``enabled=false`` overrides connection state.
 */
export function ImAccountStatusPill({
  connectionState,
  enabled,
  className,
}: Props): React.ReactElement {
  const t = useTranslations('im.status')
  if (!enabled) {
    return (
      <span
        role="status"
        className={cn(
          'inline-flex items-center gap-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground',
          className,
        )}
      >
        <PauseCircle className="size-3" />
        {t('disabled')}
      </span>
    )
  }
  if (connectionState === 'connected') {
    return (
      <span
        role="status"
        className={cn(
          'inline-flex items-center gap-1 rounded-full bg-success-solid/10 px-1.5 py-0.5 text-[10px] font-medium text-success-fg',
          className,
        )}
      >
        <CheckCircle2 className="size-3" />
        {t('connected')}
      </span>
    )
  }
  if (connectionState === 'never_connected') {
    return (
      <span
        role="status"
        className={cn(
          'inline-flex items-center gap-1 rounded-full bg-destructive/10 px-1.5 py-0.5 text-[10px] font-medium text-destructive',
          className,
        )}
      >
        <AlertTriangle className="size-3" />
        {t('never')}
      </span>
    )
  }
  return (
    <span
      role="status"
      className={cn(
        'inline-flex items-center gap-1 rounded-full bg-warning-solid/10 px-1.5 py-0.5 text-[10px] font-medium text-warning-fg',
        className,
      )}
    >
      <MinusCircle className="size-3" />
      {t('disconnected')}
    </span>
  )
}
