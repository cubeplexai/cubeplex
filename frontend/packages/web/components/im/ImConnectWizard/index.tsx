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
import { ALL_PLATFORMS } from './platforms'
import type { FormState, PlatformDescriptor } from './platforms/types'

function defaultsFor(p: PlatformDescriptor): FormState {
  const out: FormState = {}
  for (const f of p.credentialFields) {
    if (f.default !== undefined) out[f.key] = f.default
  }
  return out
}

type DynamicT = (key: string, values?: Record<string, string | number>) => string

interface Props {
  wsId: string
  open: boolean
  /** When set to a live platform id, skip the platform-picker and open its config directly. */
  initialPlatformId?: string
  onClose: () => void
  onSuccess: () => void
}

export function ImConnectWizard({
  wsId,
  open,
  initialPlatformId,
  onClose,
  onSuccess,
}: Props): React.ReactElement {
  const t = useTranslations() as unknown as DynamicT
  const client = useMemo(() => createApiClient(''), [])
  const initialPlatform = useMemo(() => {
    if (!initialPlatformId) return null
    const p = ALL_PLATFORMS.find((x) => x.id === initialPlatformId)
    return p && p.live ? p : null
  }, [initialPlatformId])
  const [platform, setPlatform] = useState<PlatformDescriptor | null>(initialPlatform)
  const [stepIdx, setStepIdx] = useState(0)
  const [form, setForm] = useState<FormState>(() =>
    initialPlatform ? defaultsFor(initialPlatform) : {},
  )
  const mut = useConnectMutation(client, wsId)

  function handleClose(): void {
    // Don't reset platform/stepIdx/form here — the wizard is unmounted by the parent
    // (`{wizardOpen && <ImConnectWizard ...>}`), so state resets naturally on next open.
    // Resetting in-place causes the dialog content to flash back to the platform picker
    // during the close animation.
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
      }
    } else {
      setStepIdx(stepIdx + 1)
    }
  }

  return (
    <Dialog open={open} disablePointerDismissal onOpenChange={(o) => !o && handleClose()}>
      <DialogContent
        role="dialog"
        aria-labelledby="wizard-title"
        className={platform ? 'sm:max-w-lg' : 'sm:max-w-md'}
      >
        <DialogHeader>
          <DialogTitle id="wizard-title">{t('im.wizard.title')}</DialogTitle>
        </DialogHeader>

        {!platform ? (
          <StepPlatform
            onPick={(p) => {
              setPlatform(p)
              setStepIdx(0)
              setForm(defaultsFor(p))
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
                  wsId={wsId}
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
