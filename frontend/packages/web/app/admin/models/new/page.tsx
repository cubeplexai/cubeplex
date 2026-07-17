'use client'

import { useMemo, useReducer, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { createApiClient, deleteProvider } from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { WizardStepRail } from '@/components/admin/models/wizard/WizardStepRail'
import { PresetPicker } from '@/components/admin/models/wizard/PresetPicker'
import { ConfigureStep } from '@/components/admin/models/wizard/ConfigureStep'
import { ModelsStep } from '@/components/admin/models/wizard/ModelsStep'
import { TestStep } from '@/components/admin/models/wizard/TestStep'
import { initialWizardState, wizardReducer } from '@/components/admin/models/wizard/wizardMachine'

export default function AddProviderWizardPage() {
  const t = useTranslations('adminModels.wizard')
  const router = useRouter()
  const client = useMemo(() => createApiClient(''), [])
  const [state, dispatch] = useReducer(wizardReducer, initialWizardState)
  const [confirmingDiscard, setConfirmingDiscard] = useState(false)
  const [discarding, setDiscarding] = useState(false)

  // Leave the wizard but keep the provider (created at step 2). Its models stay
  // disabled until a passing test enables them — the user can finish from the
  // models page later. Also the success path (TestStep "Finish").
  function finish() {
    router.push('/admin/models')
  }

  // Throw away a provider created in this wizard session (and its models), then
  // leave. Best-effort: navigate even if the delete fails so the user isn't stuck.
  async function discard() {
    if (!state.providerId) {
      finish()
      return
    }
    setDiscarding(true)
    try {
      await deleteProvider(client, state.providerId)
    } catch {
      // ignore — nothing actionable for the user on a cleanup delete
    } finally {
      router.push('/admin/models')
    }
  }

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border/70 px-6 py-4">
        <h2 className="text-lg font-semibold tracking-tight">{t('title')}</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{t('subtitle')}</p>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <aside className="w-[240px] shrink-0 border-r border-border/70 bg-card/20 px-5 py-6">
          <WizardStepRail current={state.step} />
        </aside>

        <section className="flex flex-1 flex-col overflow-hidden">
          <div className="flex-1 overflow-y-auto px-6 py-6">
            {state.step === 1 && (
              <PresetPicker
                client={client}
                selectedVendor={state.vendor?.vendor ?? null}
                onPickVendor={(vendor) => dispatch({ type: 'pickVendor', vendor })}
              />
            )}
            {state.step === 2 && state.vendor && (
              <ConfigureStep
                client={client}
                vendor={state.vendor}
                selectedPresetKey={state.selectedPresetKey}
                onSelectEndpoint={(presetKey) => dispatch({ type: 'selectEndpoint', presetKey })}
                existingProviderId={state.providerId}
                onProviderCreated={(id) => {
                  dispatch({ type: 'providerCreated', providerId: id })
                  dispatch({ type: 'next' })
                }}
                configDraft={state.configDraft}
                onConfigDraftChange={(draft) => dispatch({ type: 'setConfigDraft', draft })}
              />
            )}
            {state.step === 3 && state.vendor && state.selectedPresetKey && state.providerId && (
              <ModelsStep
                client={client}
                vendor={state.vendor}
                presetKey={state.selectedPresetKey}
                providerId={state.providerId}
                existingModels={state.models}
                onModelsCreated={(models) => {
                  dispatch({ type: 'modelsCreated', models })
                  dispatch({ type: 'next' })
                }}
              />
            )}
            {state.step === 4 && state.providerId && (
              <TestStep
                client={client}
                providerId={state.providerId}
                modelDbIds={state.models.map((m) => m.id)}
                modelLabels={Object.fromEntries(state.models.map((m) => [m.id, m.display_name]))}
                onFinish={finish}
              />
            )}
          </div>

          <footer className="flex items-center justify-between gap-3 border-t border-border/70 px-6 py-3">
            <div className="flex items-center gap-2">
              {state.providerId == null ? (
                // Nothing persisted yet — plain cancel just leaves.
                <Button type="button" variant="ghost" size="sm" onClick={finish}>
                  {t('cancel')}
                </Button>
              ) : confirmingDiscard ? (
                <>
                  <span className="text-xs text-muted-foreground">{t('discardPrompt')}</span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="text-destructive hover:text-destructive"
                    disabled={discarding}
                    onClick={() => void discard()}
                  >
                    {t('discardYes')}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={discarding}
                    onClick={() => setConfirmingDiscard(false)}
                  >
                    {t('discardNo')}
                  </Button>
                </>
              ) : (
                // Provider exists: discard (delete) or keep it disabled and finish later.
                <>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="text-destructive hover:text-destructive"
                    onClick={() => setConfirmingDiscard(true)}
                  >
                    {t('discard')}
                  </Button>
                  <Button type="button" variant="ghost" size="sm" onClick={finish}>
                    {t('finishLater')}
                  </Button>
                </>
              )}
            </div>
            <div className="flex items-center gap-2">
              {state.step > 1 && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => dispatch({ type: 'back' })}
                >
                  {t('back')}
                </Button>
              )}
              {state.step === 1 && (
                <Button
                  type="button"
                  size="sm"
                  disabled={!state.vendor}
                  onClick={() => dispatch({ type: 'next' })}
                >
                  {t('next')}
                </Button>
              )}
            </div>
          </footer>
        </section>
      </div>
    </div>
  )
}
