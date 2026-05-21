'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  createProvider,
  updateProvider,
  type ApiClient,
  type ProviderCreate,
  type ProviderPreset,
  type ProviderUpdate,
} from '@cubebox/core'
import { ProviderConfigForm } from '../ProviderConfigForm'

interface ConfigureStepProps {
  client: ApiClient
  preset: ProviderPreset
  // Set once the provider has been created (e.g. user went forward then back to
  // this step). When present, Next updates the existing row instead of creating
  // a second provider (which would 409 on the name or orphan a row).
  existingProviderId?: string | null
  onProviderCreated: (providerId: string) => void
}

export function ConfigureStep({
  client,
  preset,
  existingProviderId,
  onProviderCreated,
}: ConfigureStepProps) {
  const t = useTranslations('adminModels.wizard.configure')
  const tw = useTranslations('adminModels.wizard')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(body: ProviderCreate | ProviderUpdate) {
    setSaving(true)
    setError(null)
    try {
      if (existingProviderId) {
        // Revisit: update the already-created provider instead of creating again.
        await updateProvider(client, existingProviderId, body as ProviderUpdate)
        onProviderCreated(existingProviderId)
      } else {
        const provider = await createProvider(client, body as ProviderCreate)
        onProviderCreated(provider.id)
      }
    } catch (e) {
      setError((e as Error).message || t('createFailed'))
      setSaving(false)
    }
  }

  return (
    <div className="mx-auto w-full max-w-xl">
      <ProviderConfigForm
        mode="create"
        preset={preset}
        saving={saving}
        error={error}
        submitLabel={tw('next')}
        onSubmit={(body) => void handleSubmit(body)}
      />
    </div>
  )
}
