'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Eye, EyeOff, ExternalLink, Loader2 } from 'lucide-react'
import {
  useMcpStore,
  type ApiClient,
  type MCPAdminConnector,
  type MCPAuthMethod,
  type MCPCatalogStaticFormField,
} from '@cubebox/core'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

const OAUTH_ORIGIN_KEY = 'mcp_oauth_origin'

interface MCPCatalogInstallPanelProps {
  connector: MCPAdminConnector
  client: ApiClient
  wsId: string
  onInstalled: (installId: string) => void
}

function defaultAuthMethod(supported: MCPAuthMethod[]): MCPAuthMethod {
  if (supported.includes('oauth')) return 'oauth'
  if (supported.includes('static')) return 'static'
  return 'none'
}

function persistOAuthOrigin(): void {
  if (typeof window === 'undefined') return
  try {
    window.sessionStorage.setItem(
      OAUTH_ORIGIN_KEY,
      window.location.pathname + window.location.search,
    )
  } catch {
    // sessionStorage may be unavailable; non-fatal.
  }
}

interface StaticFieldRowProps {
  field: MCPCatalogStaticFormField
  value: string
  onChange: (next: string) => void
  showSecretLabel: string
  hideSecretLabel: string
}

function StaticFieldRow({
  field,
  value,
  onChange,
  showSecretLabel,
  hideSecretLabel,
}: StaticFieldRowProps) {
  const [reveal, setReveal] = useState(false)
  const inputType = field.secret && !reveal ? 'password' : 'text'

  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={`catalog-static-${field.name}`}>
        {field.label}
        <span className="ml-0.5 text-destructive">*</span>
      </Label>
      <div className="flex gap-2">
        <Input
          id={`catalog-static-${field.name}`}
          type={inputType}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.placeholder}
          autoComplete={field.secret ? 'new-password' : 'off'}
          required
        />
        {field.secret && (
          <Button
            type="button"
            variant="outline"
            size="icon"
            onClick={() => setReveal((r) => !r)}
            aria-label={reveal ? hideSecretLabel : showSecretLabel}
          >
            {reveal ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
          </Button>
        )}
      </div>
      {field.helper_url && (
        <a
          href={field.helper_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
        >
          <ExternalLink className="size-3" />
        </a>
      )}
    </div>
  )
}

export function MCPCatalogInstallPanel({
  connector,
  client,
  wsId,
  onInstalled,
}: MCPCatalogInstallPanelProps) {
  const t = useTranslations('mcpAdmin')
  const installFromCatalog = useMcpStore((s) => s.installFromCatalog)
  const startOAuth = useMcpStore((s) => s.startOAuth)

  const supported = useMemo(
    () => connector.supported_auth_methods ?? [],
    [connector.supported_auth_methods],
  )
  const staticFields = useMemo(
    () => connector.static_form_fields ?? [],
    [connector.static_form_fields],
  )

  const [activeMethod, setActiveMethod] = useState<MCPAuthMethod>(() =>
    defaultAuthMethod(supported),
  )
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(staticFields.map((f) => [f.name, ''])),
  )

  useEffect(() => {
    setActiveMethod(defaultAuthMethod(supported))
    setValues(Object.fromEntries(staticFields.map((f) => [f.name, ''])))
    setError(null)
  }, [connector.id, supported, staticFields])

  const allStaticFilled = staticFields.every((f) => (values[f.name] ?? '').trim().length > 0)
  const multiField = staticFields.length > 1

  async function handleOAuth(): Promise<void> {
    if (!connector.catalog_id) return
    setSubmitting(true)
    setError(null)
    try {
      const result = await installFromCatalog(client, wsId, connector.catalog_id, {
        auth_method: 'oauth',
      })
      persistOAuthOrigin()
      const oauth = await startOAuth(client, result.install_id)
      if (typeof window !== 'undefined') {
        window.location.href = oauth.authorize_url
      }
    } catch (err) {
      setError((err as Error).message)
      setSubmitting(false)
    }
  }

  async function handleStaticSubmit(): Promise<void> {
    if (!connector.catalog_id) return
    if (multiField) {
      setError(t('catalogMultiFieldUnsupported'))
      return
    }
    if (!allStaticFilled) return
    setSubmitting(true)
    setError(null)
    try {
      const fieldName = staticFields[0]?.name ?? 'token'
      const credentialPlaintext = (values[fieldName] ?? '').trim()
      const result = await installFromCatalog(client, wsId, connector.catalog_id, {
        auth_method: 'static',
        credential_plaintext: credentialPlaintext,
      })
      onInstalled(result.install_id)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  async function handleNoneInstall(): Promise<void> {
    if (!connector.catalog_id) return
    setSubmitting(true)
    setError(null)
    try {
      const result = await installFromCatalog(client, wsId, connector.catalog_id, {
        auth_method: 'none',
      })
      onInstalled(result.install_id)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex w-full flex-col gap-4 p-6" data-testid="mcp-admin-catalog-install">
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-xl font-semibold tracking-tight">{connector.name}</h3>
          {connector.provider && (
            <Badge variant="outline" className="text-[11px]">
              {connector.provider}
            </Badge>
          )}
        </div>
        {connector.description && (
          <p className="text-sm leading-relaxed text-muted-foreground">{connector.description}</p>
        )}
      </header>

      {error && (
        <Alert variant="destructive">
          <AlertTitle>{t('catalogInstallError')}</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <Card>
        <CardHeader>
          <CardTitle>{t('catalogInstallTitle')}</CardTitle>
        </CardHeader>
        <CardContent>
          <Tabs
            value={activeMethod}
            onValueChange={(value: unknown) => setActiveMethod(value as MCPAuthMethod)}
          >
            {supported.length > 1 && (
              <TabsList className="mb-4 w-fit">
                {supported.includes('oauth') && (
                  <TabsTrigger value="oauth">{t('catalogAuthOAuth')}</TabsTrigger>
                )}
                {supported.includes('static') && (
                  <TabsTrigger value="static">{t('catalogAuthStatic')}</TabsTrigger>
                )}
                {supported.includes('none') && (
                  <TabsTrigger value="none">{t('catalogAuthNone')}</TabsTrigger>
                )}
              </TabsList>
            )}

            {supported.includes('oauth') && (
              <TabsContent value="oauth" className="flex flex-col gap-4">
                <p className="text-sm text-muted-foreground">{t('catalogOAuthNotice')}</p>
                <div className="flex justify-end">
                  <Button type="button" disabled={submitting} onClick={() => void handleOAuth()}>
                    {submitting ? (
                      <Loader2 data-icon="inline-start" className="animate-spin" />
                    ) : null}
                    {t('catalogInstallOAuthButton')}
                  </Button>
                </div>
              </TabsContent>
            )}

            {supported.includes('static') && (
              <TabsContent value="static" className="flex flex-col gap-4">
                {multiField && (
                  <Alert>
                    <AlertDescription>{t('catalogMultiFieldUnsupported')}</AlertDescription>
                  </Alert>
                )}
                {staticFields.length === 0 ? (
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="catalog-static-token">{t('catalogStaticTokenLabel')}</Label>
                    <Input
                      id="catalog-static-token"
                      type="password"
                      value={values['token'] ?? ''}
                      onChange={(e) => setValues((prev) => ({ ...prev, token: e.target.value }))}
                      autoComplete="new-password"
                      required
                    />
                  </div>
                ) : (
                  staticFields.map((field) => (
                    <StaticFieldRow
                      key={field.name}
                      field={field}
                      value={values[field.name] ?? ''}
                      onChange={(next) => setValues((prev) => ({ ...prev, [field.name]: next }))}
                      showSecretLabel={t('revealSecret')}
                      hideSecretLabel={t('hideSecret')}
                    />
                  ))
                )}
                <div className="flex justify-end">
                  <Button
                    type="button"
                    disabled={
                      submitting ||
                      multiField ||
                      (staticFields.length === 0
                        ? !(values['token'] ?? '').trim()
                        : !allStaticFilled)
                    }
                    onClick={() => void handleStaticSubmit()}
                  >
                    {submitting ? (
                      <Loader2 data-icon="inline-start" className="animate-spin" />
                    ) : null}
                    {t('catalogInstallStaticButton')}
                  </Button>
                </div>
              </TabsContent>
            )}

            {supported.includes('none') && (
              <TabsContent value="none" className="flex flex-col gap-4">
                <p className="text-sm text-muted-foreground">{t('catalogNoneNotice')}</p>
                <div className="flex justify-end">
                  <Button
                    type="button"
                    disabled={submitting}
                    onClick={() => void handleNoneInstall()}
                  >
                    {submitting ? (
                      <Loader2 data-icon="inline-start" className="animate-spin" />
                    ) : null}
                    {t('catalogInstallNoneButton')}
                  </Button>
                </div>
              </TabsContent>
            )}
          </Tabs>
        </CardContent>
      </Card>
    </div>
  )
}
