'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import { AlertTriangle, ChevronDown } from 'lucide-react'
import type { Provider, ProviderCreate, ProviderUpdate, WireApi } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { cn } from '@/lib/utils'
import { CapabilityEditor } from './wizard/CapabilityEditor'
import type { ConfigFormValues } from './wizard/wizardMachine'

const PROVIDER_TYPES: readonly WireApi[] = [
  'openai-completions',
  'openai-responses',
  'anthropic-messages',
] as const

function slugifyTs(name: string): string {
  const s = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return s || 'provider'
}

// Radio choices for the editable auth field. bearer_token shares the api_key
// wire shape, so it folds into 'api_key' in the radio (matching the old dialog).
type AuthChoice = 'api_key' | 'none'

// Create-mode seed: a single catalog endpoint selected in the wizard's step 2.
// (The catalog no longer carries auth — auth defaults to api_key. Capability is
// prefilled from the endpoint and only sent back when the user overrides it;
// otherwise the server resolves it from preset_key.)
export interface CreatePreset {
  display_name: string
  base_url: string
  provider_type: WireApi
  /** The endpoint's preset_key, recorded as Provider.preset_slug. */
  preset_key: string
  category: 'saas' | 'oss-framework' | 'custom'
  /** Resolved capability for the chosen endpoint; prefills the editor. */
  capability: Record<string, unknown>
}

interface ProviderConfigFormProps {
  mode: 'create' | 'edit'
  // create: seeds fields from the selected endpoint; provider_type is locked to
  // the endpoint protocol; auth defaults to api_key; capability resolves
  // server-side from preset_key.
  preset?: CreatePreset
  // edit: seeds from the existing row; provider_type/auth editable; key optional.
  provider?: Provider
  // create: restore previously-entered values (wizard step revisit) instead of
  // re-seeding from the preset; emit edits so the wizard can persist them.
  initialValues?: ConfigFormValues | null
  onValuesChange?: (values: ConfigFormValues) => void
  saving: boolean
  error: string | null
  submitLabel: string
  onSubmit: (body: ProviderCreate | ProviderUpdate) => void
}

export function ProviderConfigForm({
  mode,
  preset,
  provider,
  initialValues,
  onValuesChange,
  saving,
  error,
  submitLabel,
  onSubmit,
}: ProviderConfigFormProps) {
  const t = useTranslations('adminModels')
  const tc = useTranslations('adminModels.wizard.configure')
  const isCreate = mode === 'create'

  // Create: auth defaults to api_key (the catalog no longer carries auth).
  // Edit: auth is user-editable via the radio (api_key / none).
  const supported = true

  // In create mode, a restored draft (wizard revisit) wins over preset defaults.
  const seed = isCreate ? initialValues : null
  const [name, setName] = useState(() =>
    isCreate ? (seed?.name ?? preset?.display_name ?? '') : (provider?.name ?? ''),
  )
  const [slug, setSlug] = useState(() =>
    isCreate ? (seed?.slug ?? slugifyTs(preset?.display_name ?? '')) : (provider?.slug ?? ''),
  )
  const [slugTouched, setSlugTouched] = useState(() => seed?.slugTouched ?? false)
  const [baseUrl, setBaseUrl] = useState(() =>
    isCreate ? (seed?.baseUrl ?? preset?.base_url ?? '') : (provider?.base_url ?? ''),
  )
  const [providerType, setProviderType] = useState<WireApi>(() => {
    if (isCreate) return (preset?.provider_type ?? 'openai-completions') as WireApi
    return (provider?.provider_type ?? 'openai-completions') as WireApi
  })
  const [authChoice, setAuthChoice] = useState<AuthChoice>(() => {
    if (isCreate) return seed?.authChoice ?? 'api_key'
    // bearer_token folds into api_key for the radio (same wire shape).
    return provider?.auth_type === 'none' ? 'none' : 'api_key'
  })
  const [apiKey, setApiKey] = useState(() => seed?.apiKey ?? '')
  const [capability, setCapability] = useState<Record<string, unknown>>(() =>
    isCreate ? (seed?.capability ?? preset?.capability ?? {}) : (provider?.capability ?? {}),
  )
  // The endpoint's resolved capability as first prefilled. On create we only send
  // capability when the user edits it away from this; an untouched value lets the
  // server resolve it from preset_slug (keeps the provider row preset-tracked).
  const presetCapabilityJson = useMemo(
    () => JSON.stringify(preset?.capability ?? {}),
    [preset?.capability],
  )
  const [logoUrl, setLogoUrl] = useState(() =>
    isCreate ? (seed?.logoUrl ?? '') : (provider?.logo_url ?? ''),
  )
  const [extraHeaders, setExtraHeaders] = useState(() =>
    isCreate
      ? (seed?.extraHeaders ?? '')
      : provider?.extra_headers && Object.keys(provider.extra_headers).length > 0
        ? JSON.stringify(provider.extra_headers, null, 2)
        : '',
  )
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [headersError, setHeadersError] = useState<string | null>(null)

  // Emit the current values so the wizard can persist them across step changes.
  // Use a ref for the callback so its (unstable) identity doesn't retrigger the
  // effect — it must fire on value changes only, never on parent re-renders.
  const onValuesChangeRef = useRef(onValuesChange)
  // eslint-disable-next-line react-hooks/refs
  onValuesChangeRef.current = onValuesChange
  useEffect(() => {
    if (!isCreate) return
    onValuesChangeRef.current?.({
      name,
      slug,
      slugTouched,
      baseUrl,
      apiKey,
      authChoice,
      capability,
      logoUrl,
      extraHeaders,
    })
  }, [
    isCreate,
    name,
    slug,
    slugTouched,
    baseUrl,
    apiKey,
    authChoice,
    capability,
    logoUrl,
    extraHeaders,
  ])

  // Effective auth_type sent to the backend. The catalog no longer carries
  // per-preset auth, so the user picks it (api_key default / none) in both
  // modes — this preserves no-auth semantics for keyless endpoints (ollama,
  // lm-studio, tgi, …) instead of forcing a dummy key (codex P1).
  const authType: 'api_key' | 'bearer_token' | 'none' = authChoice === 'none' ? 'none' : 'api_key'
  const needsKey = authType !== 'none'
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
        slug: slug.trim() || undefined,
        provider_type: providerType,
        base_url: baseUrl.trim(),
        auth_type: authType,
        api_key: needsKey ? apiKey : null,
        preset_slug: preset.preset_key,
        // Capability is prefilled from the endpoint. Send it only when the user
        // edited it away from the preset default; otherwise let the server
        // resolve it from preset_slug.
        capability: JSON.stringify(capability) !== presetCapabilityJson ? capability : undefined,
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
        <Input
          id="pcf-name"
          value={name}
          onChange={(e) => {
            setName(e.target.value)
            if (isCreate && !slugTouched) setSlug(slugifyTs(e.target.value))
          }}
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="cfg-slug">{t('slug')}</Label>
        <Input
          id="cfg-slug"
          value={slug}
          onChange={(e) => {
            setSlug(e.target.value)
            setSlugTouched(true)
          }}
          disabled={!isCreate}
          aria-label={t('slug')}
        />
        {isCreate && <span className="text-[11px] text-muted-foreground">{t('slugHint')}</span>}
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
        <div className="flex items-start gap-2 rounded-lg border border-warning-border bg-warning-surface px-3 py-2.5 text-xs text-warning-fg">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          <span>{tc('unsupportedAuth')}</span>
        </div>
      )}

      {supported && (
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
            name="provider-api-key"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={isCreate ? 'sk-…' : t('apiKeyEditHint')}
            autoComplete="new-password"
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
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
