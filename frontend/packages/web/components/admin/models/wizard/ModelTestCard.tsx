'use client'

import { useTranslations } from 'next-intl'
import { Check, Minus, TriangleAlert, X } from 'lucide-react'
import type { ProbeResult, ProbeStep } from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { formatProbeDetail } from '@/lib/probeDetail'

export interface ModelTestState extends ProbeResult {
  model_db_id: string
  display_name: string
}

interface ModelTestCardProps {
  state: ModelTestState
  onRetest?: () => void
}

function StepChip({ step }: { step: ProbeStep }) {
  const tone =
    step.status === 'pass'
      ? 'border-success-border bg-success-surface text-success-fg'
      : step.status === 'warn'
        ? 'border-warning-border bg-warning-surface text-warning-fg'
        : step.status === 'fail'
          ? 'border-destructive/40 bg-destructive/5 text-destructive'
          : 'border-border/70 bg-muted/30 text-muted-foreground'
  const Icon =
    step.status === 'pass'
      ? Check
      : step.status === 'warn'
        ? TriangleAlert
        : step.status === 'fail'
          ? X
          : Minus
  return (
    <span
      title={formatProbeDetail(step.detail ?? step.error?.message) || step.name}
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium',
        tone,
      )}
    >
      <Icon className="size-3" />
      {step.name}
    </span>
  )
}

const OUTCOME_TONE: Record<ProbeResult['overall'], string> = {
  pass: 'bg-success-surface text-success-fg',
  warn: 'bg-warning-surface text-warning-fg',
  fail: 'bg-destructive/10 text-destructive',
  unavailable: 'bg-destructive/10 text-destructive',
}

export function ModelTestCard({ state, onRetest }: ModelTestCardProps) {
  const t = useTranslations('adminModels.wizard.test')
  // Surface the *why* for any non-passing check (warn → degraded, fail → blocked).
  // Without this, a "degraded" outcome shows only coloured chips and no reason.
  const issues = state.steps.filter((s) => s.status === 'warn' || s.status === 'fail')
  const showRetest =
    onRetest &&
    (state.overall === 'fail' || state.overall === 'unavailable' || state.overall === 'warn')

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border/70 bg-card p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="truncate text-sm font-medium">{state.display_name}</p>
        <Badge className={cn('border-transparent font-medium', OUTCOME_TONE[state.overall])}>
          {t(`outcome.${state.overall}`)}
        </Badge>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {state.steps.map((s) => (
          <StepChip key={s.name} step={s} />
        ))}
      </div>
      {issues.length > 0 && (
        <ul className="flex flex-col gap-1 border-t border-border/60 pt-2">
          {issues.map((s) => {
            const isFail = s.status === 'fail'
            const detail = formatProbeDetail(s.detail ?? s.error?.message)
            return (
              <li key={s.name} className="flex items-start gap-1.5 text-xs">
                {isFail ? (
                  <X className="mt-0.5 size-3 shrink-0 text-destructive" />
                ) : (
                  <TriangleAlert className="mt-0.5 size-3 shrink-0 text-warning-fg" />
                )}
                <span className={cn(isFail ? 'text-destructive' : 'text-warning-fg')}>
                  <span className="font-medium">{s.name}</span>
                  {detail ? ` — ${detail}` : ''}
                </span>
              </li>
            )
          })}
        </ul>
      )}
      {showRetest && (
        <Button type="button" variant="outline" size="sm" className="ml-auto" onClick={onRetest}>
          {t('retest')}
        </Button>
      )}
    </div>
  )
}
