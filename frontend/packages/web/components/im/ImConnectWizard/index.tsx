'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { createApiClient } from '@cubebox/core'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'

import { StepPlatform } from './steps/StepPlatform'
import { useConnectMutation } from './useConnectMutation'
import type { FormState, PlatformDescriptor } from './platforms/types'

type DynamicT = (key: string, values?: Record<string, string | number>) => string

interface Props {
  wsId: string
  open: boolean
  onClose: () => void
  onSuccess: () => void
}

export function ImConnectWizard({ wsId, open, onClose, onSuccess }: Props): React.ReactElement {
  const t = useTranslations() as unknown as DynamicT
  const client = useMemo(() => createApiClient(''), [])
  const [platform, setPlatform] = useState<PlatformDescriptor | null>(null)
  const [stepIdx, setStepIdx] = useState(0)
  const [form, setForm] = useState<FormState>({
    delivery_mode: 'long_connection',
    domain: 'feishu',
  })
  const mut = useConnectMutation(client, wsId)

  function handleClose(): void {
    setPlatform(null)
    setStepIdx(0)
    setForm({ delivery_mode: 'long_connection', domain: 'feishu' })
    onClose()
  }

  async function handleNext(): Promise<void> {
    if (!platform) return
    const isLast = stepIdx === platform.steps.length - 1
    if (isLast) {
      const out = await mut.submit(platform.buildPayload(form))
      if (out) {
        toast.success(t('im.success.toast.connected'))
        onSuccess()
        handleClose()
      }
    } else {
      setStepIdx(stepIdx + 1)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && handleClose()}>
      <DialogContent role="dialog" aria-labelledby="wizard-title">
        <DialogHeader>
          <DialogTitle id="wizard-title">{t('im.wizard.title')}</DialogTitle>
        </DialogHeader>

        {!platform ? (
          <StepPlatform
            onPick={(p) => {
              setPlatform(p)
              setStepIdx(0)
            }}
          />
        ) : (
          <>
            <ol role="list" className="flex gap-2 text-xs">
              {platform.steps.map((s, i) => (
                <li
                  key={s.key}
                  aria-current={i === stepIdx ? 'step' : undefined}
                  className={i === stepIdx ? 'font-semibold' : 'text-muted-foreground'}
                >
                  {i + 1}. {t(s.labelKey)}
                </li>
              ))}
            </ol>

            {mut.error?.shape === 'banner' && (
              <Alert variant="destructive">
                <AlertDescription>{t(mut.error.messageKey)}</AlertDescription>
              </Alert>
            )}

            {(() => {
              const stepDef = platform.steps[stepIdx]
              const Step = stepDef.Component
              const extraProps = stepDef.key === 'verify' ? { busy: mut.busy } : {}
              return (
                <Step
                  descriptor={platform}
                  form={form}
                  onChange={(patch) => {
                    const merged: FormState = { ...form }
                    for (const [k, v] of Object.entries(patch)) {
                      if (v !== undefined) merged[k] = v
                    }
                    setForm(merged)
                  }}
                  onNext={handleNext}
                  {...extraProps}
                />
              )
            })()}

            <div className="flex justify-end gap-2">
              {stepIdx > 0 && (
                <Button variant="outline" onClick={() => setStepIdx(stepIdx - 1)}>
                  Back
                </Button>
              )}
              <Button
                onClick={handleNext}
                disabled={
                  mut.busy ||
                  !!(
                    platform.steps[stepIdx].canAdvance && !platform.steps[stepIdx].canAdvance!(form)
                  )
                }
              >
                {stepIdx === platform.steps.length - 1 ? t('im.action.connect') : 'Next'}
              </Button>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
