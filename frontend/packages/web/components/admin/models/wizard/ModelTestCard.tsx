'use client'

import { useTranslations } from 'next-intl'
import { Check, Minus, TriangleAlert, X } from 'lucide-react'
import type { ProbeResult, ProbeStep } from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

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
      ? 'border-green-500/40 bg-green-500/5 text-green-700 dark:text-green-300'
      : step.status === 'warn'
        ? 'border-amber-500/40 bg-amber-500/5 text-amber-700 dark:text-amber-300'
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
      title={step.detail ?? step.error?.message ?? step.name}
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
  pass: 'bg-green-500/10 text-green-700 dark:text-green-300',
  warn: 'bg-amber-500/10 text-amber-700 dark:text-amber-300',
  fail: 'bg-destructive/10 text-destructive',
  unavailable: 'bg-destructive/10 text-destructive',
}

export function ModelTestCard({ state, onRetest }: ModelTestCardProps) {
  const t = useTranslations('adminModels.wizard.test')
  const failed = state.overall === 'fail' || state.overall === 'unavailable'
  const reason = state.steps.find((s) => s.status === 'fail')?.error?.message

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
      {failed && (
        <div className="flex items-center justify-between gap-2">
          {reason && <span className="truncate text-xs text-destructive">{reason}</span>}
          {onRetest && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="ml-auto"
              onClick={onRetest}
            >
              {t('retest')}
            </Button>
          )}
        </div>
      )}
    </div>
  )
}
