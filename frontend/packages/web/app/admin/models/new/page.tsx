'use client'

import { useMemo, useReducer } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { createApiClient } from '@cubebox/core'
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

  function finish() {
    router.push('/admin/models')
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
              />
            )}
            {state.step === 3 && state.vendor && state.selectedPresetKey && state.providerId && (
              <ModelsStep
                client={client}
                vendor={state.vendor}
                presetKey={state.selectedPresetKey}
                providerId={state.providerId}
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

          <footer className="flex items-center justify-between border-t border-border/70 px-6 py-3">
            <Button type="button" variant="ghost" size="sm" onClick={finish}>
              {t('cancel')}
            </Button>
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
