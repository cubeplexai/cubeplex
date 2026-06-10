'use client'

import { useTranslations } from 'next-intl'
import { Check } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { WizardStep } from './wizardMachine'

const STEPS: { step: WizardStep; key: 'preset' | 'configure' | 'models' | 'test' }[] = [
  { step: 1, key: 'preset' },
  { step: 2, key: 'configure' },
  { step: 3, key: 'models' },
  { step: 4, key: 'test' },
]

interface WizardStepRailProps {
  current: WizardStep
}

export function WizardStepRail({ current }: WizardStepRailProps) {
  const t = useTranslations('adminModels.wizard')

  return (
    <nav aria-label="wizard-steps" className="flex flex-col gap-1">
      {STEPS.map(({ step, key }, i) => {
        const done = step < current
        const active = step === current
        return (
          <div key={key} className="flex items-stretch gap-3">
            <div className="flex flex-col items-center">
              <span
                className={cn(
                  'flex size-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold transition-colors',
                  done && 'border-primary bg-primary text-primary-foreground',
                  active && 'border-primary bg-accent text-foreground',
                  !done && !active && 'border-border bg-card text-muted-foreground',
                )}
                data-state={active ? 'active' : done ? 'done' : 'idle'}
              >
                {done ? <Check className="size-3.5" /> : step}
              </span>
              {i < STEPS.length - 1 && (
                <span
                  className={cn('my-1 w-px flex-1', done ? 'bg-primary/40' : 'bg-border')}
                  aria-hidden
                />
              )}
            </div>
            <div className="pb-5 pt-0.5">
              <p
                className={cn(
                  'text-sm font-medium leading-tight',
                  active ? 'text-foreground' : 'text-muted-foreground',
                )}
              >
                {t(`step.${key}.label`)}
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground/80">{t(`step.${key}.hint`)}</p>
            </div>
          </div>
        )
      })}
    </nav>
  )
}
