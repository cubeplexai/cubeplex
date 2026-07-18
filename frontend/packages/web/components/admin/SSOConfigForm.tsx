'use client'

/**
 * SSO configuration form (OIDC / SAML).
 *
 * Renders a single form for creating or updating an org's SSO connection.
 * Uses plain useState + per-field validation (matching the existing admin
 * forms in this package). All backend error codes flow through
 * `error.code` → i18n key under `adminAuthentication.form.errors.*`.
 *
 * SP redirect URI / ACS URL / SP entity ID are deterministic from
 * `frontend_base_url` (see backend/cubeplex/api/routes/v1/sso.py) so we
 * compute them client-side and surface them prominently — the admin
 * pastes them into their IdP.
 */

import { useCallback, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { Copy, Search } from 'lucide-react'
import {
  ApiError,
  createApiClient,
  createSsoConnection,
  discoverOidcEndpoints,
  updateSsoConnection,
  type SsoConnectionResponse,
} from '@cubeplex/core'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

interface SSOConfigFormProps {
  connection: SsoConnectionResponse | null
  orgSlug: string
  onUpdated: (next: SsoConnectionResponse) => void
}

type Protocol = 'oidc' | 'saml'
type Provisioning = 'auto' | 'invite_only'
type SamlPreset = 'generic' | 'okta' | 'azure' | 'google'

type KnownErrorKey =
  | 'errors.sso_already_configured'
  | 'errors.invalid_issuer_url'
  | 'errors.oidc_discovery_failed'
  | 'errors.oidc_discovery_refused'
  | 'errors.invalid_config'
  | 'errors.config_missing_fields'
  | 'errors.config_url_refused'
  | 'errors.client_secret_required_for_oidc'
  | 'errors.client_secret_empty'
  | 'errors.deactivate_before_delete'
  | 'errors.invalid_status_transition'
  | 'errors.app_base_url_missing_scheme'
  | 'errors.unauthorized'
  | 'errors.forbidden'

const KNOWN_ERROR_CODES = new Map<string, KnownErrorKey>([
  ['sso_already_configured', 'errors.sso_already_configured'],
  ['invalid_issuer_url', 'errors.invalid_issuer_url'],
  ['oidc_discovery_failed', 'errors.oidc_discovery_failed'],
  ['oidc_discovery_refused', 'errors.oidc_discovery_refused'],
  ['invalid_config', 'errors.invalid_config'],
  ['config_missing_fields', 'errors.config_missing_fields'],
  ['config_url_refused', 'errors.config_url_refused'],
  ['client_secret_required_for_oidc', 'errors.client_secret_required_for_oidc'],
  ['client_secret_empty', 'errors.client_secret_empty'],
  ['deactivate_before_delete', 'errors.deactivate_before_delete'],
  ['invalid_status_transition', 'errors.invalid_status_transition'],
  ['app_base_url_missing_scheme', 'errors.app_base_url_missing_scheme'],
  ['unauthorized', 'errors.unauthorized'],
  ['forbidden', 'errors.forbidden'],
])

function mapErrorCode(code: string | null): KnownErrorKey | null {
  return code ? (KNOWN_ERROR_CODES.get(code) ?? null) : null
}

type NameIdLabelKey =
  'saml.nameIdEmail' | 'saml.nameIdPersistent' | 'saml.nameIdTransient' | 'saml.nameIdUnspecified'

const SAML_NAMEID_FORMATS: { value: string; labelKey: NameIdLabelKey }[] = [
  {
    value: 'urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress',
    labelKey: 'saml.nameIdEmail',
  },
  {
    value: 'urn:oasis:names:tc:SAML:2.0:nameid-format:persistent',
    labelKey: 'saml.nameIdPersistent',
  },
  {
    value: 'urn:oasis:names:tc:SAML:2.0:nameid-format:transient',
    labelKey: 'saml.nameIdTransient',
  },
  {
    value: 'urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified',
    labelKey: 'saml.nameIdUnspecified',
  },
]

const SAML_PRESETS: Record<SamlPreset, { id: string; email: string; name: string }> = {
  generic: { id: 'NameID', email: 'email', name: 'displayName' },
  okta: { id: 'NameID', email: 'email', name: 'name' },
  azure: {
    id: 'http://schemas.microsoft.com/identity/claims/objectidentifier',
    email: 'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress',
    name: 'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/displayname',
  },
  google: { id: 'NameID', email: 'email', name: 'displayName' },
}

interface FormState {
  protocol: Protocol
  displayName: string
  provisioning: Provisioning

  // OIDC
  issuer: string
  authorizationEndpoint: string
  tokenEndpoint: string
  userinfoEndpoint: string
  jwksUri: string
  clientId: string
  clientSecret: string
  scopes: string

  // SAML
  idpEntityId: string
  idpSsoUrl: string
  idpCertificate: string
  nameIdFormat: string
  samlPreset: SamlPreset

  // Attribute mapping
  attrId: string
  attrEmail: string
  attrName: string
}

function buildInitialState(connection: SsoConnectionResponse | null): FormState {
  if (!connection) {
    return {
      protocol: 'oidc',
      displayName: '',
      provisioning: 'auto',
      issuer: '',
      authorizationEndpoint: '',
      tokenEndpoint: '',
      userinfoEndpoint: '',
      jwksUri: '',
      clientId: '',
      clientSecret: '',
      scopes: 'openid email profile',
      idpEntityId: '',
      idpSsoUrl: '',
      idpCertificate: '',
      nameIdFormat: SAML_NAMEID_FORMATS[0].value,
      samlPreset: 'generic',
      attrId: 'sub',
      attrEmail: 'email',
      attrName: 'name',
    }
  }
  const cfg = connection.config as Record<string, unknown>
  const attrMap = (cfg.attribute_mapping as Record<string, string> | undefined) ?? {}
  const scopes = Array.isArray(cfg.scopes) ? (cfg.scopes as string[]).join(' ') : ''
  const isOidc = connection.protocol === 'oidc'
  return {
    protocol: connection.protocol === 'saml' ? 'saml' : 'oidc',
    displayName: connection.display_name,
    provisioning: connection.provisioning === 'invite_only' ? 'invite_only' : 'auto',
    issuer: typeof cfg.issuer === 'string' ? cfg.issuer : '',
    authorizationEndpoint:
      typeof cfg.authorization_endpoint === 'string' ? cfg.authorization_endpoint : '',
    tokenEndpoint: typeof cfg.token_endpoint === 'string' ? cfg.token_endpoint : '',
    userinfoEndpoint: typeof cfg.userinfo_endpoint === 'string' ? cfg.userinfo_endpoint : '',
    jwksUri: typeof cfg.jwks_uri === 'string' ? cfg.jwks_uri : '',
    clientId: typeof cfg.client_id === 'string' ? cfg.client_id : '',
    clientSecret: '',
    scopes: scopes || 'openid email profile',
    idpEntityId: typeof cfg.idp_entity_id === 'string' ? cfg.idp_entity_id : '',
    idpSsoUrl: typeof cfg.idp_sso_url === 'string' ? cfg.idp_sso_url : '',
    idpCertificate: typeof cfg.idp_certificate === 'string' ? cfg.idp_certificate : '',
    nameIdFormat:
      typeof cfg.name_id_format === 'string' ? cfg.name_id_format : SAML_NAMEID_FORMATS[0].value,
    samlPreset: 'generic',
    attrId: attrMap.id ?? (isOidc ? 'sub' : 'NameID'),
    attrEmail: attrMap.email ?? 'email',
    attrName: attrMap.name ?? (isOidc ? 'name' : 'displayName'),
  }
}

function originOf(): string {
  if (typeof window === 'undefined') return ''
  return window.location.origin
}

async function copyToClipboard(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    // ignore — environments without clipboard permission silently no-op
  }
}

export function SSOConfigForm({ connection, orgSlug, onUpdated }: SSOConfigFormProps) {
  const t = useTranslations('adminAuthentication.form')
  const tCommon = useTranslations('adminAuthentication')
  const client = useMemo(() => createApiClient(''), [])
  const [state, setState] = useState<FormState>(() => buildInitialState(connection))
  const [discovering, setDiscovering] = useState(false)
  const [saving, setSaving] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [topError, setTopError] = useState<string | null>(null)

  const isEdit = connection !== null

  // Deterministic SP URLs — mirror backend/api/routes/v1/sso.py.
  const origin = originOf()
  const redirectUri = `${origin}/api/v1/auth/sso/oidc/callback`
  const spAcsUrl = `${origin}/api/v1/auth/sso/saml/acs`
  const spEntityId = orgSlug ? `${origin}/saml/${orgSlug}` : ''
  const spMetadataUrl = connection ? `${origin}/api/v1/auth/sso/saml/metadata/${connection.id}` : ''

  const update = useCallback(<K extends keyof FormState>(key: K, value: FormState[K]) => {
    setState((s) => ({ ...s, [key]: value }))
  }, [])

  const onDiscover = useCallback(async () => {
    if (!state.issuer.trim()) {
      setErrors((e) => ({ ...e, issuer: t('validation.issuerRequired') }))
      return
    }
    setDiscovering(true)
    setTopError(null)
    try {
      const out = await discoverOidcEndpoints(client, state.issuer.trim())
      setState((s) => ({
        ...s,
        authorizationEndpoint: out.authorization_endpoint,
        tokenEndpoint: out.token_endpoint,
        userinfoEndpoint: out.userinfo_endpoint ?? '',
        jwksUri: out.jwks_uri ?? '',
      }))
      toast.success(t('oidc.discoverSuccess'))
    } catch (err) {
      const msg = errorMessage(err, t)
      setTopError(t('oidc.discoverFailed', { message: msg }))
    } finally {
      setDiscovering(false)
    }
  }, [client, state.issuer, t])

  const validate = useCallback((): Record<string, string> => {
    const next: Record<string, string> = {}
    if (!state.displayName.trim()) next.displayName = t('validation.displayNameRequired')
    if (state.protocol === 'oidc') {
      if (!state.issuer.trim()) next.issuer = t('validation.issuerRequired')
      if (!state.clientId.trim()) next.clientId = t('validation.clientIdRequired')
      if (!isEdit && !state.clientSecret.trim()) {
        next.clientSecret = t('validation.clientSecretRequired')
      }
      if (
        !state.authorizationEndpoint.trim() ||
        !state.tokenEndpoint.trim() ||
        !state.jwksUri.trim()
      ) {
        next.endpoints = t('validation.endpointsRequired')
      }
    } else {
      if (!state.idpEntityId.trim()) next.idpEntityId = t('validation.idpEntityIdRequired')
      if (!state.idpSsoUrl.trim()) next.idpSsoUrl = t('validation.idpSsoUrlRequired')
      if (!state.idpCertificate.trim()) {
        next.idpCertificate = t('validation.idpCertificateRequired')
      }
    }
    return next
  }, [state, isEdit, t])

  const buildConfig = useCallback((): Record<string, unknown> => {
    if (state.protocol === 'oidc') {
      const scopes = state.scopes
        .split(/[\s,]+/)
        .map((s) => s.trim())
        .filter(Boolean)
      return {
        issuer: state.issuer.trim(),
        authorization_endpoint: state.authorizationEndpoint.trim(),
        token_endpoint: state.tokenEndpoint.trim(),
        userinfo_endpoint: state.userinfoEndpoint.trim() || null,
        jwks_uri: state.jwksUri.trim(),
        client_id: state.clientId.trim(),
        scopes: scopes.length ? scopes : ['openid', 'email', 'profile'],
        attribute_mapping: {
          id: state.attrId.trim() || 'sub',
          email: state.attrEmail.trim() || 'email',
          name: state.attrName.trim() || 'name',
        },
      }
    }
    return {
      idp_entity_id: state.idpEntityId.trim(),
      idp_sso_url: state.idpSsoUrl.trim(),
      idp_certificate: state.idpCertificate.trim(),
      name_id_format: state.nameIdFormat,
      attribute_mapping: {
        id: state.attrId.trim() || 'NameID',
        email: state.attrEmail.trim() || 'email',
        name: state.attrName.trim() || 'displayName',
      },
    }
  }, [state])

  const onSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault()
      const v = validate()
      setErrors(v)
      setTopError(null)
      if (Object.keys(v).length > 0) return
      setSaving(true)
      try {
        const config = buildConfig()
        const next = isEdit
          ? await updateSsoConnection(client, connection.id, {
              display_name: state.displayName.trim(),
              provisioning: state.provisioning,
              config,
            })
          : await createSsoConnection(client, {
              protocol: state.protocol,
              display_name: state.displayName.trim(),
              provisioning: state.provisioning,
              config,
              client_secret: state.clientSecret.trim() || undefined,
            })
        onUpdated(next)
        toast.success(t('saved'))
      } catch (err) {
        const code = err instanceof ApiError ? err.code : null
        const mapped = mapErrorCode(code)
        if (mapped) {
          setTopError(t(mapped))
        } else {
          setTopError(t('saveFailedGeneric', { message: errorMessage(err, t) }))
        }
      } finally {
        setSaving(false)
      }
    },
    [validate, buildConfig, isEdit, client, connection, state, onUpdated, t],
  )

  const applyPreset = useCallback((preset: SamlPreset) => {
    setState((s) => ({
      ...s,
      samlPreset: preset,
      attrId: SAML_PRESETS[preset].id,
      attrEmail: SAML_PRESETS[preset].email,
      attrName: SAML_PRESETS[preset].name,
    }))
  }, [])

  return (
    <form onSubmit={onSubmit} className="space-y-6">
      <section className="rounded-xl border border-border/70 bg-card shadow-sm">
        <header className="border-b border-border px-5 py-3.5">
          <h2 className="text-sm font-medium">{t('protocol')}</h2>
        </header>
        <div className="space-y-5 p-5">
          <RadioGroup
            value={state.protocol}
            onValueChange={(v) => update('protocol', (v ?? 'oidc') as Protocol)}
            className="grid-cols-2 sm:grid-cols-2"
            disabled={isEdit}
          >
            <ProtocolOption
              value="oidc"
              checked={state.protocol === 'oidc'}
              title={t('protocolOidc')}
              hint="OAuth 2.0 + OpenID Connect"
            />
            <ProtocolOption
              value="saml"
              checked={state.protocol === 'saml'}
              title={t('protocolSaml')}
              hint="SAML 2.0 SP-initiated"
            />
          </RadioGroup>

          <div className="space-y-2">
            <Label htmlFor="sso-display-name">{t('displayName')}</Label>
            <Input
              id="sso-display-name"
              value={state.displayName}
              onChange={(e) => update('displayName', e.target.value)}
              placeholder={t('displayNamePlaceholder')}
              data-testid="sso-display-name"
              aria-invalid={Boolean(errors.displayName)}
            />
            <p className="text-xs text-muted-foreground">{t('displayNameHelp')}</p>
            {errors.displayName && <p className="text-xs text-destructive">{errors.displayName}</p>}
          </div>

          <div className="space-y-2">
            <Label>{t('provisioning')}</Label>
            <RadioGroup
              value={state.provisioning}
              onValueChange={(v) => update('provisioning', (v ?? 'auto') as Provisioning)}
              className="gap-2"
            >
              <ProtocolOption
                value="auto"
                checked={state.provisioning === 'auto'}
                title={t('provisioningAuto')}
                hint={t('provisioningAutoHelp')}
                compact
              />
              <ProtocolOption
                value="invite_only"
                checked={state.provisioning === 'invite_only'}
                title={t('provisioningInvite')}
                hint={t('provisioningInviteHelp')}
                compact
              />
            </RadioGroup>
          </div>
        </div>
      </section>

      {state.protocol === 'oidc' ? (
        <section
          className="rounded-xl border border-border/70 bg-card shadow-sm"
          data-testid="sso-oidc-section"
        >
          <header className="border-b border-border px-5 py-3.5">
            <h2 className="text-sm font-medium">{t('oidc.section')}</h2>
          </header>
          <div className="space-y-5 p-5">
            <div className="space-y-2">
              <Label htmlFor="oidc-issuer">{t('oidc.issuer')}</Label>
              <div className="flex gap-2">
                <Input
                  id="oidc-issuer"
                  value={state.issuer}
                  onChange={(e) => update('issuer', e.target.value)}
                  placeholder={t('oidc.issuerPlaceholder')}
                  data-testid="sso-issuer"
                  aria-invalid={Boolean(errors.issuer)}
                />
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => void onDiscover()}
                  disabled={discovering || !state.issuer.trim()}
                  data-testid="sso-discover"
                  className="gap-1.5"
                >
                  <Search className="size-3.5" />
                  {discovering ? t('oidc.discovering') : t('oidc.discover')}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">{t('oidc.discoverHelp')}</p>
              {errors.issuer && <p className="text-xs text-destructive">{errors.issuer}</p>}
            </div>

            <FieldRow
              label={t('oidc.authorizationEndpoint')}
              id="oidc-authz"
              value={state.authorizationEndpoint}
              onChange={(v) => update('authorizationEndpoint', v)}
            />
            <FieldRow
              label={t('oidc.tokenEndpoint')}
              id="oidc-token"
              value={state.tokenEndpoint}
              onChange={(v) => update('tokenEndpoint', v)}
            />
            <FieldRow
              label={t('oidc.userinfoEndpoint')}
              id="oidc-userinfo"
              value={state.userinfoEndpoint}
              onChange={(v) => update('userinfoEndpoint', v)}
              optional
            />
            <FieldRow
              label={t('oidc.jwksUri')}
              id="oidc-jwks"
              value={state.jwksUri}
              onChange={(v) => update('jwksUri', v)}
            />
            {errors.endpoints && <p className="text-xs text-destructive">{errors.endpoints}</p>}

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="oidc-client-id">{t('oidc.clientId')}</Label>
                <Input
                  id="oidc-client-id"
                  value={state.clientId}
                  onChange={(e) => update('clientId', e.target.value)}
                  data-testid="sso-client-id"
                  aria-invalid={Boolean(errors.clientId)}
                />
                {errors.clientId && <p className="text-xs text-destructive">{errors.clientId}</p>}
              </div>
              <div className="space-y-2">
                <Label htmlFor="oidc-client-secret">{t('oidc.clientSecret')}</Label>
                <Input
                  id="oidc-client-secret"
                  type="password"
                  name="oidc-client-secret"
                  value={state.clientSecret}
                  onChange={(e) => update('clientSecret', e.target.value)}
                  placeholder={isEdit ? t('oidc.clientSecretPlaceholder') : ''}
                  autoComplete="new-password"
                  autoCapitalize="off"
                  autoCorrect="off"
                  spellCheck={false}
                  data-testid="sso-client-secret"
                  aria-invalid={Boolean(errors.clientSecret)}
                />
                {errors.clientSecret && (
                  <p className="text-xs text-destructive">{errors.clientSecret}</p>
                )}
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="oidc-scopes">{t('oidc.scopes')}</Label>
              <Input
                id="oidc-scopes"
                value={state.scopes}
                onChange={(e) => update('scopes', e.target.value)}
              />
              <p className="text-xs text-muted-foreground">{t('oidc.scopesHelp')}</p>
            </div>

            <CopyField
              label={t('oidc.redirectUri')}
              value={redirectUri}
              help={t('oidc.redirectUriHelp')}
            />
          </div>
        </section>
      ) : (
        <section
          className="rounded-xl border border-border/70 bg-card shadow-sm"
          data-testid="sso-saml-section"
        >
          <header className="border-b border-border px-5 py-3.5">
            <h2 className="text-sm font-medium">{t('saml.section')}</h2>
          </header>
          <div className="space-y-5 p-5">
            <p className="text-xs text-muted-foreground">{t('saml.metadataUploadTodo')}</p>
            {/* TODO(sso-saml-metadata-upload): add backend endpoint to parse uploaded
                IdP metadata XML. See docs/dev/plans/2026-06-17-sso.md Task 10. */}

            <FieldRow
              label={t('saml.idpEntityId')}
              id="saml-entity"
              value={state.idpEntityId}
              onChange={(v) => update('idpEntityId', v)}
              error={errors.idpEntityId}
            />
            <FieldRow
              label={t('saml.idpSsoUrl')}
              id="saml-sso-url"
              value={state.idpSsoUrl}
              onChange={(v) => update('idpSsoUrl', v)}
              error={errors.idpSsoUrl}
            />

            <div className="space-y-2">
              <Label htmlFor="saml-cert">{t('saml.idpCertificate')}</Label>
              <Textarea
                id="saml-cert"
                rows={6}
                value={state.idpCertificate}
                onChange={(e) => update('idpCertificate', e.target.value)}
                placeholder={t('saml.idpCertificatePlaceholder')}
                className="font-mono text-xs"
                aria-invalid={Boolean(errors.idpCertificate)}
              />
              {errors.idpCertificate && (
                <p className="text-xs text-destructive">{errors.idpCertificate}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label>{t('saml.nameIdFormat')}</Label>
              <Select
                value={state.nameIdFormat}
                onValueChange={(v) => update('nameIdFormat', v ?? state.nameIdFormat)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SAML_NAMEID_FORMATS.map((f) => (
                    <SelectItem key={f.value} value={f.value}>
                      {t(f.labelKey)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <CopyField label={t('saml.spEntityId')} value={spEntityId} />
            <CopyField label={t('saml.spAcsUrl')} value={spAcsUrl} />
            {spMetadataUrl && <CopyField label={t('saml.spMetadata')} value={spMetadataUrl} />}
          </div>
        </section>
      )}

      <section className="rounded-xl border border-border/70 bg-card shadow-sm">
        <header className="border-b border-border px-5 py-3.5">
          <h2 className="text-sm font-medium">{t('attributeMapping.section')}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">{t('attributeMapping.help')}</p>
        </header>
        <div className="space-y-5 p-5">
          {state.protocol === 'saml' && (
            <div className="space-y-2">
              <Label>{t('attributeMapping.preset')}</Label>
              <Select value={state.samlPreset} onValueChange={(v) => applyPreset(v as SamlPreset)}>
                <SelectTrigger className="sm:w-64">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="generic">{t('attributeMapping.presetGeneric')}</SelectItem>
                  <SelectItem value="okta">{t('attributeMapping.presetOkta')}</SelectItem>
                  <SelectItem value="azure">{t('attributeMapping.presetAzure')}</SelectItem>
                  <SelectItem value="google">{t('attributeMapping.presetGoogle')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}
          <div className="grid gap-4 sm:grid-cols-3">
            <FieldRow
              label={t('attributeMapping.idKey')}
              id="attr-id"
              value={state.attrId}
              onChange={(v) => update('attrId', v)}
            />
            <FieldRow
              label={t('attributeMapping.emailKey')}
              id="attr-email"
              value={state.attrEmail}
              onChange={(v) => update('attrEmail', v)}
            />
            <FieldRow
              label={t('attributeMapping.nameKey')}
              id="attr-name"
              value={state.attrName}
              onChange={(v) => update('attrName', v)}
            />
          </div>
        </div>
      </section>

      {topError && (
        <div
          role="alert"
          className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive"
          data-testid="sso-form-error"
        >
          {topError}
        </div>
      )}

      <div className="flex items-center justify-end gap-2">
        <Button type="submit" disabled={saving} data-testid="sso-save">
          {saving ? (isEdit ? t('saving') : t('creating')) : isEdit ? t('save') : t('create')}
        </Button>
      </div>

      {/* Hidden helper element so suppressing "tCommon unused" never bites
          if we later remove the top-level translator reference. */}
      <span className="sr-only">{tCommon('title')}</span>
    </form>
  )
}

function ProtocolOption({
  value,
  checked,
  title,
  hint,
  compact,
}: {
  value: string
  checked: boolean
  title: string
  hint: string
  compact?: boolean
}) {
  return (
    <label
      className={`flex items-start gap-3 rounded-lg border px-3 py-2.5 cursor-pointer transition-colors ${
        checked
          ? 'border-primary bg-primary/5'
          : 'border-border hover:border-foreground/30 hover:bg-accent/30'
      }`}
    >
      <RadioGroupItem value={value} className="mt-0.5" />
      <span className="flex flex-col gap-0.5">
        <span className="text-sm font-medium">{title}</span>
        <span className={`text-xs text-muted-foreground ${compact ? '' : ''}`}>{hint}</span>
      </span>
    </label>
  )
}

function FieldRow({
  label,
  id,
  value,
  onChange,
  optional,
  error,
}: {
  label: string
  id: string
  value: string
  onChange: (v: string) => void
  optional?: boolean
  error?: string
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>
        {label}
        {optional && <span className="ml-1 text-xs text-muted-foreground">·</span>}
      </Label>
      <Input
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        data-testid={`sso-field-${id}`}
        aria-invalid={Boolean(error)}
      />
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  )
}

function CopyField({ label, value, help }: { label: string; value: string; help?: string }) {
  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <div className="flex items-center gap-2">
        <Input
          value={value}
          readOnly
          className="font-mono text-xs"
          onFocus={(e) => e.target.select()}
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void copyToClipboard(value)}
          aria-label="Copy"
        >
          <Copy className="size-3.5" />
        </Button>
      </div>
      {help && <p className="text-xs text-muted-foreground">{help}</p>}
    </div>
  )
}

function errorMessage(err: unknown, _t: unknown): string {
  if (err instanceof ApiError) {
    return err.message || err.code || `HTTP ${err.status}`
  }
  if (err instanceof Error) return err.message
  return String(err)
}
