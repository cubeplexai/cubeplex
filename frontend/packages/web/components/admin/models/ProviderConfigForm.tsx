'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { AlertTriangle, ChevronDown } from 'lucide-react'
import type {
  Provider,
  ProviderCreate,
  ProviderPreset,
  ProviderUpdate,
  WireApi,
} from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { cn } from '@/lib/utils'
import { CapabilityEditor } from './wizard/CapabilityEditor'

const PROVIDER_TYPES: readonly WireApi[] = [
  'openai-completions',
  'openai-responses',
  'anthropic-messages',
] as const

// Radio choices for the editable auth field. bearer_token shares the api_key
// wire shape, so it folds into 'api_key' in the radio (matching the old dialog).
type AuthChoice = 'api_key' | 'none'

interface ProviderConfigFormProps {
  mode: 'create' | 'edit'
  // create: seeds fields; provider_type is locked to preset.api; auth derived
  // from preset.auth.mode; capability seeded from preset.capability.
  preset?: ProviderPreset
  // edit: seeds from the existing row; provider_type/auth editable; key optional.
  provider?: Provider
  saving: boolean
  error: string | null
  submitLabel: string
  onSubmit: (body: ProviderCreate | ProviderUpdate) => void
}

// Map preset auth mode → backend auth_type. Backend _validate_auth_creds
// expects "bearer_token" (not "bearer"). oauth/iam are not yet supported.
function authTypeForPreset(
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

export function ProviderConfigForm({
  mode,
  preset,
  provider,
  saving,
  error,
  submitLabel,
  onSubmit,
}: ProviderConfigFormProps) {
  const t = useTranslations('adminModels')
  const tc = useTranslations('adminModels.wizard.configure')
  const isCreate = mode === 'create'

  // Create: auth is fixed by the preset (and may be unsupported). Edit: auth is
  // user-editable via the radio (api_key / none).
  const presetAuthType = preset ? authTypeForPreset(preset.auth.mode) : null
  const supported = isCreate ? presetAuthType !== null : true

  const [name, setName] = useState(() =>
    isCreate ? (preset?.display_name ?? '') : (provider?.name ?? ''),
  )
  const [baseUrl, setBaseUrl] = useState(() =>
    isCreate ? (preset?.base_url ?? '') : (provider?.base_url ?? ''),
  )
  const [providerType, setProviderType] = useState<WireApi>(() => {
    if (isCreate) return (preset?.api ?? 'openai-completions') as WireApi
    return (provider?.provider_type ?? 'openai-completions') as WireApi
  })
  const [authChoice, setAuthChoice] = useState<AuthChoice>(() => {
    if (isCreate) return presetAuthType === 'none' ? 'none' : 'api_key'
    // bearer_token folds into api_key for the radio (same wire shape).
    return provider?.auth_type === 'none' ? 'none' : 'api_key'
  })
  const [apiKey, setApiKey] = useState('')
  const [capability, setCapability] = useState<Record<string, unknown>>(() =>
    isCreate ? (preset?.capability ?? {}) : (provider?.capability ?? {}),
  )
  const [logoUrl, setLogoUrl] = useState(() => (isCreate ? '' : (provider?.logo_url ?? '')))
  const [extraHeaders, setExtraHeaders] = useState(() =>
    !isCreate && provider?.extra_headers && Object.keys(provider.extra_headers).length > 0
      ? JSON.stringify(provider.extra_headers, null, 2)
      : '',
  )
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [headersError, setHeadersError] = useState<string | null>(null)

  // Effective auth_type sent to the backend.
  const authType: 'api_key' | 'bearer_token' | 'none' = isCreate
    ? (presetAuthType ?? 'none')
    : authChoice === 'none'
      ? 'none'
      : 'api_key'
  const needsKey = authType === 'api_key' || authType === 'bearer_token'
  // Key required in create (when the auth needs one); optional in edit.
  const keyRequired = isCreate && needsKey

  const canSubmit =
    supported &&
    name.trim() !== '' &&
    baseUrl.trim() !== '' &&
    (!keyRequired || apiKey.trim() !== '')

  function handleSubmit() {
    if (!canSubmit) return
    let parsedHeaders: Record<string, unknown> | undefined
    if (extraHeaders.trim()) {
      try {
        parsedHeaders = JSON.parse(extraHeaders) as Record<string, unknown>
      } catch {
        setHeadersError(t('extraHeadersInvalid'))
        return
      }
    }
    setHeadersError(null)

    if (isCreate && preset) {
      const body: ProviderCreate = {
        name: name.trim(),
        provider_type: providerType,
        base_url: baseUrl.trim(),
        auth_type: authType,
        api_key: needsKey ? apiKey : null,
        preset_slug: preset.slug,
        capability,
        model_capability_overrides: preset.model_capability_overrides,
        logo_url: logoUrl.trim() || null,
        extra_headers: parsedHeaders,
      }
      onSubmit(body)
    } else {
      const body: ProviderUpdate = {
        name: name.trim() || null,
        provider_type: providerType,
        base_url: baseUrl.trim() || null,
        auth_type: authType,
        // Blank = keep the existing key.
        api_key: apiKey.trim() || null,
        logo_url: logoUrl.trim() || null,
        extra_headers: parsedHeaders ?? null,
        capability,
        model_capability_overrides: provider?.model_capability_overrides ?? {},
      }
      onSubmit(body)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="pcf-name">{t('name')}</Label>
        <Input id="pcf-name" value={name} onChange={(e) => setName(e.target.value)} />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="pcf-base-url">{t('baseUrl')}</Label>
        <Input id="pcf-base-url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="pcf-provider-type">{t('providerType')}</Label>
        {isCreate ? (
          <Input id="pcf-provider-type" value={providerType} readOnly disabled />
        ) : (
          <select
            id="pcf-provider-type"
            value={providerType}
            onChange={(e) => setProviderType(e.target.value as WireApi)}
            className="h-8 w-full rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            {PROVIDER_TYPES.map((pt) => (
              <option key={pt} value={pt}>
                {pt}
              </option>
            ))}
          </select>
        )}
      </div>

      {!supported && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2.5 text-xs text-amber-700 dark:text-amber-300">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          <span>{tc('unsupportedAuth')}</span>
        </div>
      )}

      {supported && !isCreate && (
        <div className="flex flex-col gap-1.5">
          <Label>{t('authType')}</Label>
          <RadioGroup
            value={authChoice}
            onValueChange={(v) => setAuthChoice(v as AuthChoice)}
            className="grid grid-cols-2 gap-2"
          >
            {(['api_key', 'none'] as AuthChoice[]).map((opt) => (
              <label
                key={opt}
                className={cn(
                  'flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2.5 text-sm transition-colors',
                  authChoice === opt
                    ? 'border-primary/40 bg-primary/5'
                    : 'border-border/70 hover:border-border',
                )}
              >
                <RadioGroupItem value={opt} />
                <span className="flex-1 text-sm">
                  {opt === 'api_key' ? t('authApiKey') : t('authNone')}
                </span>
              </label>
            ))}
          </RadioGroup>
        </div>
      )}

      {supported && needsKey && (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="pcf-api-key">{isCreate ? tc('apiKey') : t('apiKey')}</Label>
          <Input
            id="pcf-api-key"
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={isCreate ? 'sk-…' : t('apiKeyEditHint')}
          />
          <span className="text-[11px] text-muted-foreground">
            {isCreate ? tc('apiKeyRequired') : t('apiKeyEditHint')}
          </span>
        </div>
      )}

      {supported && isCreate && !needsKey && (
        <p className="text-xs text-muted-foreground">{tc('noAuth')}</p>
      )}

      {supported && (
        <div className="rounded-lg border border-border/70">
          <button
            type="button"
            onClick={() => setAdvancedOpen((v) => !v)}
            aria-expanded={advancedOpen}
            className="flex w-full items-center justify-between px-3 py-2.5 text-xs font-medium text-muted-foreground hover:text-foreground"
          >
            {tc('advanced')}
            <ChevronDown
              className={cn('size-4 transition-transform', advancedOpen && 'rotate-180')}
            />
          </button>
          {advancedOpen && (
            <div className="flex flex-col gap-4 border-t border-border/70 p-3">
              <CapabilityEditor
                value={capability}
                onChange={setCapability}
                allowTemplate={isCreate ? preset?.category === 'custom' : true}
              />
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="pcf-logo-url">{t('logoUrl')}</Label>
                <Input
                  id="pcf-logo-url"
                  value={logoUrl}
                  onChange={(e) => setLogoUrl(e.target.value)}
                  placeholder="https://example.com/logo.png"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="pcf-extra-headers">{t('extraHeaders')}</Label>
                <textarea
                  id="pcf-extra-headers"
                  value={extraHeaders}
                  onChange={(e) => setExtraHeaders(e.target.value)}
                  placeholder='{"X-Custom-Header": "value"}'
                  rows={3}
                  className="h-20 w-full rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                />
              </div>
            </div>
          )}
        </div>
      )}

      {(error ?? headersError) && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-1.5 text-xs text-destructive">
          {error ?? headersError}
        </div>
      )}

      <div className="flex justify-end">
        <Button
          type="button"
          size="sm"
          disabled={!canSubmit || saving}
          onClick={handleSubmit}
          data-testid="provider-config-submit"
        >
          {saving ? t('saving') : submitLabel}
        </Button>
      </div>
    </div>
  )
}
