'use client'

import { useTranslations } from 'next-intl'
import { CheckCircle2, Loader2, XCircle } from 'lucide-react'
import type { ProbeStep } from '@cubebox/core'
import { cn } from '@/lib/utils'
import { formatProbeDetail } from '@/lib/probeDetail'

interface LivenessRowProps {
  step: ProbeStep | null
  running: boolean
}

export function LivenessRow({ step, running }: LivenessRowProps) {
  const t = useTranslations('adminModels.wizard.test')
  const status = step?.status ?? null
  const ok = status === 'pass' || status === 'warn'
  // Show the failure reason in full (wrapping) rather than a clipped raw blob.
  const reason = status === 'fail' ? formatProbeDetail(step?.detail ?? step?.error?.message) : ''

  return (
    <div
      className={cn(
        'flex flex-col gap-1.5 rounded-lg border px-3 py-2.5',
        ok
          ? 'border-success-border bg-success-surface'
          : status === 'fail'
            ? 'border-destructive/40 bg-destructive/5'
            : 'border-border/70',
      )}
    >
      <div className="flex items-center gap-2.5">
        {!step && running ? (
          <Loader2 className="size-4 shrink-0 animate-spin text-muted-foreground" />
        ) : ok ? (
          <CheckCircle2 className="size-4 shrink-0 text-success-fg" />
        ) : status === 'fail' ? (
          <XCircle className="size-4 shrink-0 text-destructive" />
        ) : (
          <Loader2 className="size-4 shrink-0 animate-spin text-muted-foreground" />
        )}
        <span className="flex-1 text-sm font-medium">{t('liveness')}</span>
        {step?.latency_ms != null && (
          <span className="text-xs text-muted-foreground">{step.latency_ms} ms</span>
        )}
        {!step && <span className="text-xs text-muted-foreground">{t('livenessPending')}</span>}
      </div>
      {reason && <p className="pl-[26px] text-xs break-words text-destructive">{reason}</p>}
    </div>
  )
}
