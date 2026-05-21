'use client'

import { useTranslations } from 'next-intl'
import { CheckCircle2, Loader2, XCircle } from 'lucide-react'
import type { ProbeStep } from '@cubebox/core'
import { cn } from '@/lib/utils'

interface LivenessRowProps {
  step: ProbeStep | null
  running: boolean
}

export function LivenessRow({ step, running }: LivenessRowProps) {
  const t = useTranslations('adminModels.wizard.test')
  const status = step?.status ?? null
  const ok = status === 'pass' || status === 'warn'

  return (
    <div
      className={cn(
        'flex items-center gap-2.5 rounded-lg border px-3 py-2.5',
        ok
          ? 'border-green-500/40 bg-green-500/5'
          : status === 'fail'
            ? 'border-destructive/40 bg-destructive/5'
            : 'border-border/70',
      )}
    >
      {!step && running ? (
        <Loader2 className="size-4 animate-spin text-muted-foreground" />
      ) : ok ? (
        <CheckCircle2 className="size-4 text-green-600 dark:text-green-400" />
      ) : status === 'fail' ? (
        <XCircle className="size-4 text-destructive" />
      ) : (
        <Loader2 className="size-4 animate-spin text-muted-foreground" />
      )}
      <span className="flex-1 text-sm font-medium">{t('liveness')}</span>
      {step?.latency_ms != null && (
        <span className="text-xs text-muted-foreground">{step.latency_ms} ms</span>
      )}
      {step?.error?.message && (
        <span className="truncate text-xs text-destructive">{step.error.message}</span>
      )}
      {!step && <span className="text-xs text-muted-foreground">{t('livenessPending')}</span>}
    </div>
  )
}
