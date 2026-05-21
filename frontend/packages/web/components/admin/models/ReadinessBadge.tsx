import { useTranslations } from 'next-intl'
import type { Readiness } from '@cubebox/core'

const MAP = {
  ready: { dot: 'bg-green-500', key: 'ready' },
  degraded: { dot: 'bg-amber-500', key: 'degraded' },
  stale: { dot: 'bg-amber-400', key: 'stale' },
  provider_error: { dot: 'bg-red-500', key: 'providerError' },
  model_error: { dot: 'bg-red-500', key: 'modelError' },
  unavailable: { dot: 'bg-zinc-400', key: 'unavailable' },
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
