'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { AlertTriangle, ChevronDown } from 'lucide-react'
import {
  createProvider,
  type ApiClient,
  type ProviderCreate,
  type ProviderPreset,
} from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'
import { CapabilityEditor } from './CapabilityEditor'

interface ConfigureStepProps {
  client: ApiClient
  preset: ProviderPreset
  onProviderCreated: (providerId: string) => void
}

// Map preset auth mode → backend auth_type. Backend _validate_auth_creds
// expects "bearer_token" (not "bearer"). oauth/iam are not yet supported.
function authTypeFor(
  mode: ProviderPreset['auth']['mode'],
): 'api_key' | 'bearer_token' | 'none' | null {
  switch (mode) {
    case 'api_key':
      return 'api_key'
    case 'bearer':
      return 'bearer_token'
    case 'none':
      return 'none'
    case 'oauth':
    case 'iam':
      return null
  }
}

export function ConfigureStep({ client, preset, onProviderCreated }: ConfigureStepProps) {
  const t = useTranslations('adminModels.wizard.configure')
  const tw = useTranslations('adminModels.wizard')
  const authType = authTypeFor(preset.auth.mode)
  const needsKey = authType === 'api_key' || authType === 'bearer_token'
  const supported = authType !== null

  const [name, setName] = useState(preset.display_name)
  const [baseUrl, setBaseUrl] = useState(preset.base_url)
  const [apiKey, setApiKey] = useState('')
  const [capability, setCapability] = useState<Record<string, unknown>>(preset.capability)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const canSubmit =
    supported && name.trim() !== '' && baseUrl.trim() !== '' && (!needsKey || apiKey.trim() !== '')

  async function handleNext() {
    if (!canSubmit || authType === null) return
    setSaving(true)
    setError(null)
    try {
      const body: ProviderCreate = {
        name: name.trim(),
        provider_type: preset.api,
        base_url: baseUrl.trim(),
        auth_type: authType,
        api_key: needsKey ? apiKey : null,
        preset_slug: preset.slug,
        capability,
        model_capability_overrides: preset.model_capability_overrides,
      }
      const provider = await createProvider(client, body)
      onProviderCreated(provider.id)
    } catch (e) {
      setError((e as Error).message || t('createFailed'))
      setSaving(false)
    }
  }

  return (
    <div className="mx-auto flex max-w-xl flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="cfg-name">{t('name')}</Label>
        <Input id="cfg-name" value={name} onChange={(e) => setName(e.target.value)} />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="cfg-base-url">{t('baseUrl')}</Label>
        <Input id="cfg-base-url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
      </div>

      {!supported && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2.5 text-xs text-amber-700 dark:text-amber-300">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          <span>{t('unsupportedAuth')}</span>
        </div>
      )}

      {supported && needsKey && (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="cfg-api-key">{t('apiKey')}</Label>
          <Input
            id="cfg-api-key"
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="sk-…"
          />
          <span className="text-[11px] text-muted-foreground">{t('apiKeyRequired')}</span>
        </div>
      )}

      {supported && !needsKey && <p className="text-xs text-muted-foreground">{t('noAuth')}</p>}

      {supported && (
        <div className="rounded-lg border border-border/70">
          <button
            type="button"
            onClick={() => setAdvancedOpen((v) => !v)}
            aria-expanded={advancedOpen}
            className="flex w-full items-center justify-between px-3 py-2.5 text-xs font-medium text-muted-foreground hover:text-foreground"
          >
            {t('advanced')}
            <ChevronDown
              className={cn('size-4 transition-transform', advancedOpen && 'rotate-180')}
            />
          </button>
          {advancedOpen && (
            <div className="border-t border-border/70 p-3">
              <CapabilityEditor
                value={capability}
                onChange={setCapability}
                allowTemplate={preset.category === 'custom'}
              />
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-1.5 text-xs text-destructive">
          {error}
        </div>
      )}

      <div className="flex justify-end">
        <Button
          type="button"
          size="sm"
          disabled={!canSubmit || saving}
          onClick={() => void handleNext()}
        >
          {saving ? t('creating') : tw('next')}
        </Button>
      </div>
    </div>
  )
}
