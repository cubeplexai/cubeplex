'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  ApiError,
  createProvider,
  updateProvider,
  type ApiClient,
  type EndpointPreset,
  type ProviderCreate,
  type ProviderUpdate,
  type VendorPreset,
} from '@cubeplex/core'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'
import { ProviderConfigForm, type CreatePreset } from '../ProviderConfigForm'
import type { ConfigDraft, ConfigFormValues } from './wizardMachine'

interface ConfigureStepProps {
  client: ApiClient
  vendor: VendorPreset
  selectedPresetKey: string | null
  onSelectEndpoint: (presetKey: string) => void
  // Set once the provider has been created (revisit case): update instead of
  // creating a second row.
  existingProviderId?: string | null
  onProviderCreated: (providerId: string) => void
  // Persisted form values, so stepping back into this step restores them.
  configDraft: ConfigDraft | null
  onConfigDraftChange: (draft: ConfigDraft) => void
}

function uniq<T>(xs: T[]): T[] {
  return [...new Set(xs)]
}

export function ConfigureStep({
  client,
  vendor,
  selectedPresetKey,
  onSelectEndpoint,
  existingProviderId,
  onProviderCreated,
  configDraft,
  onConfigDraftChange,
}: ConfigureStepProps) {
  const t = useTranslations('adminModels.wizard.configure')
  const tw = useTranslations('adminModels.wizard')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // The currently-selected endpoint: the one matching selectedPresetKey, else
  // the vendor's first endpoint.
  const endpoint: EndpointPreset =
    vendor.endpoints.find((e) => e.preset_key === selectedPresetKey) ?? vendor.endpoints[0]

  const [region, setRegion] = useState(endpoint.region)
  const [protocol, setProtocol] = useState<string>(endpoint.protocol)
  const [plan, setPlan] = useState<string | null>(endpoint.plan)

  const regions = useMemo(() => uniq(vendor.endpoints.map((e) => e.region)), [vendor])
  const protocols = useMemo(
    () => uniq(vendor.endpoints.filter((e) => e.region === region).map((e) => e.protocol)),
    [vendor, region],
  )
  const plans = useMemo(
    () =>
      vendor.endpoints
        .filter((e) => e.region === region && e.protocol === protocol)
        .map((e) => e.plan),
    [vendor, region, protocol],
  )

  // The endpoint resolved from the three selectors (falls back to first match).
  const chosen: EndpointPreset =
    vendor.endpoints.find(
      (e) => e.region === region && e.protocol === protocol && e.plan === plan,
    ) ??
    vendor.endpoints.find((e) => e.region === region && e.protocol === protocol) ??
    endpoint

  // Keep the wizard state's selectedPresetKey in sync with the chosen endpoint.
  useEffect(() => {
    if (chosen.preset_key !== selectedPresetKey) onSelectEndpoint(chosen.preset_key)
  }, [chosen.preset_key, selectedPresetKey, onSelectEndpoint])

  const createPreset: CreatePreset = {
    display_name: vendor.display_name,
    base_url: chosen.base_url,
    provider_type: chosen.protocol,
    preset_key: chosen.preset_key,
    category: vendor.category,
    capability: chosen.capability,
  }

  async function handleSubmit(body: ProviderCreate | ProviderUpdate) {
    setSaving(true)
    setError(null)
    try {
      if (existingProviderId) {
        await updateProvider(client, existingProviderId, body as ProviderUpdate)
        onProviderCreated(existingProviderId)
      } else {
        const provider = await createProvider(client, body as ProviderCreate)
        onProviderCreated(provider.id)
      }
    } catch (e) {
      setError(errorMessage(e))
      setSaving(false)
    }
  }

  // Map the backend's stable error code (ApiError.code) to a human message —
  // otherwise a slug/name conflict surfaces as a bare "HTTP 409".
  function errorMessage(e: unknown): string {
    if (e instanceof ApiError) {
      switch (e.code) {
        case 'provider_slug_conflict':
          return t('errors.provider_slug_conflict')
        case 'provider_name_conflict':
          return t('errors.provider_name_conflict')
        case 'invalid_provider_slug':
          return t('errors.invalid_provider_slug')
        case 'provider_oauth_not_implemented':
          return t('errors.provider_oauth_not_implemented')
      }
    }
    return (e as Error).message || t('createFailed')
  }

  const showSelectors = regions.length > 1 || protocols.length > 1 || plans.length > 1

  return (
    <div className="mx-auto flex w-full max-w-xl flex-col gap-4">
      {showSelectors && (
        <div className="grid grid-cols-3 gap-3 rounded-lg border border-border/70 p-3">
          <Selector
            label={t('region')}
            value={region}
            options={regions.map((r) => ({ value: r, label: r }))}
            onChange={(v) => {
              setRegion(v)
              // reset downstream choices to the first valid value
              const proto = vendor.endpoints.find((e) => e.region === v)?.protocol
              if (proto) setProtocol(proto)
              const pl = vendor.endpoints.find((e) => e.region === v && e.protocol === proto)?.plan
              setPlan(pl ?? null)
            }}
          />
          <Selector
            label={t('protocol')}
            value={protocol}
            options={protocols.map((p) => ({ value: p, label: p }))}
            onChange={(v) => {
              setProtocol(v)
              const pl = vendor.endpoints.find((e) => e.region === region && e.protocol === v)?.plan
              setPlan(pl ?? null)
            }}
          />
          <Selector
            label={t('plan')}
            value={plan ?? ''}
            disabled={plans.length <= 1}
            options={plans.map((p) => ({ value: p ?? '', label: p ?? '—' }))}
            onChange={(v) => setPlan(v || null)}
          />
        </div>
      )}

      <ProviderConfigForm
        key={chosen.preset_key}
        mode="create"
        preset={createPreset}
        // Restore the draft only when it belongs to the current endpoint; a
        // different endpoint should fall back to that endpoint's defaults.
        initialValues={configDraft?.presetKey === chosen.preset_key ? configDraft : null}
        onValuesChange={(values: ConfigFormValues) =>
          onConfigDraftChange({ ...values, presetKey: chosen.preset_key })
        }
        saving={saving}
        error={error}
        submitLabel={tw('next')}
        onSubmit={(body) => void handleSubmit(body)}
      />
    </div>
  )
}

function Selector({
  label,
  value,
  options,
  onChange,
  disabled,
}: {
  label: string
  value: string
  options: { value: string; label: string }[]
  onChange: (v: string) => void
  disabled?: boolean
}) {
  const id = `endpoint-sel-${label.toLowerCase()}`
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <select
        id={id}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className={cn(
          'h-8 w-full rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50',
          disabled && 'opacity-60',
        )}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  )
}
