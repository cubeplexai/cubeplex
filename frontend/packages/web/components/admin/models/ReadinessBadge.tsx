import { useTranslations } from 'next-intl'
import type { Readiness } from '@cubebox/core'

const MAP = {
  ready: { dot: 'bg-success-solid', key: 'ready' },
  degraded: { dot: 'bg-warning-solid', key: 'degraded' },
  stale: { dot: 'bg-warning-solid', key: 'stale' },
  provider_error: { dot: 'bg-danger-solid', key: 'providerError' },
  auth_error: { dot: 'bg-danger-solid', key: 'authError' },
  model_error: { dot: 'bg-danger-solid', key: 'modelError' },
  unavailable: { dot: 'bg-faint', key: 'unavailable' },
} as const satisfies Record<Readiness, { dot: string; key: string }>

interface ReadinessBadgeProps {
  readiness: Readiness
}

export function ReadinessBadge({ readiness }: ReadinessBadgeProps) {
  const t = useTranslations('adminModels.readiness')
  const m = MAP[readiness]
  return (
    <span title={t(m.key)} className="inline-flex items-center gap-1.5">
      <span className={`size-2 rounded-full ${m.dot}`} />
      <span className="text-xs text-muted-foreground">{t(m.key)}</span>
    </span>
  )
}
