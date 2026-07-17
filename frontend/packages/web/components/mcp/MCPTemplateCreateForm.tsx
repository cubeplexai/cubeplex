'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { CheckCircle2, Eye, EyeOff, Loader2, XCircle } from 'lucide-react'
import {
  adminCreateTemplate,
  adminTestConnection,
  ApiError,
  type ApiClient,
  type MCPAuthMethod,
  type MCPCredentialScope,
  type MCPTemplate,
  type MCPTransport,
  type TestConnectionResult,
} from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

/** Translate a submit-time error into human copy. Backend returns
 *  `{"detail":{"code":"..."}}` for domain errors; `ApiError.message` is only
 *  meaningful when the backend sends a `message` field (most 409/422 don't).
 *  Fall back to a per-code i18n string, then to the code itself, then to
 *  the raw error message. Never leave the user staring at bare "HTTP 409". */
type FormT = ReturnType<typeof useTranslations<'mcpAdmin'>>
function errorMessage(err: unknown, t: FormT): string {
  if (err instanceof ApiError && err.code) {
    // Known codes we translate; unknown codes render as `code (HTTP N)`.
    switch (err.code) {
      case 'connector_name_conflict':
        return t('errorNameConflict')
      case 'server_url_taken_in_org': {
        const detail = (err.detail ?? {}) as { colliding_template_name?: string | null }
        return t('errorUrlTaken', {
          other: detail.colliding_template_name ?? t('errorUrlTakenFallbackName'),
        })
      }
      default:
        return `${err.code} (HTTP ${err.status})`
    }
  }
  return (err as Error).message || 'Unknown error'
}

export interface CreateTemplateBody {
  name: string
  server_url: string
  transport: MCPTransport
  auth_method: MCPAuthMethod
  default_credential_policy: MCPCredentialScope
}

export interface EditTemplateInitial {
  name: string
  server_url: string
  transport: MCPTransport
  auth_method: MCPAuthMethod
  /** When true, disable server_url/transport inputs (connector already exists). */
  lockConnectivity: boolean
}

interface MCPTemplateCreateFormProps {
  client: ApiClient
  onCreated?: (template: MCPTemplate) => void
  /** 'admin' (default) shows the org-level create title; 'workspace' shows the ws-level title. */
  variant?: 'admin' | 'workspace'
  /** Custom submit handler; if provided, replaces adminCreateTemplate call.
   *  Return void when the parent owns post-submit lifecycle (edit mode). */
  onSubmit?: (body: CreateTemplateBody) => Promise<MCPTemplate | void>
  /** When set, form runs in edit mode: prefilled values, no auth_method / credential
   *  inputs, and server_url/transport disabled when initial.lockConnectivity=true. */
  initial?: EditTemplateInitial
  onCancel?: () => void
}

export function MCPTemplateCreateForm({
  client,
  onCreated,
  variant = 'admin',
  onSubmit,
  initial,
  onCancel,
}: MCPTemplateCreateFormProps) {
  const t = useTranslations('mcpAdmin')
  const isEdit = initial !== undefined
  const [name, setName] = useState(initial?.name ?? '')
  const [serverUrl, setServerUrl] = useState(initial?.server_url ?? '')
  const [transport, setTransport] = useState<MCPTransport>(initial?.transport ?? 'streamable_http')
  const [authMethod, setAuthMethod] = useState<MCPAuthMethod>(initial?.auth_method ?? 'static')
  const [credentialPlaintext, setCredentialPlaintext] = useState('')
  const [revealSecret, setRevealSecret] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<TestConnectionResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  function handleAuthMethodChange(next: MCPAuthMethod): void {
    setAuthMethod(next)
  }

  const credentialFieldsNeeded = authMethod === 'static'
  const canSubmit =
    !submitting &&
    name.trim().length > 0 &&
    serverUrl.trim().length > 0 &&
    (!credentialFieldsNeeded || credentialPlaintext.length > 0)

  async function handleTest(): Promise<void> {
    setTesting(true)
    setTestResult(null)
    try {
      const res = await adminTestConnection(client, {
        server_url: serverUrl.trim(),
        transport,
        auth_method: authMethod,
        credential_plaintext:
          authMethod === 'static' && credentialPlaintext ? credentialPlaintext : null,
      })
      setTestResult(res)
    } catch (err) {
      setTestResult({
        ok: false,
        tool_count: 0,
        error_code: 'request_failed',
        error_message: (err as Error).message,
      })
    } finally {
      setTesting(false)
    }
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      const body: CreateTemplateBody = {
        name: name.trim(),
        server_url: serverUrl.trim(),
        transport,
        auth_method: authMethod,
        default_credential_policy: (authMethod === 'none'
          ? 'none'
          : variant === 'workspace'
            ? 'workspace'
            : 'org') as MCPCredentialScope,
      }
      const created = onSubmit ? await onSubmit(body) : await adminCreateTemplate(client, body)
      if (created && onCreated) onCreated(created)
    } catch (err) {
      setError(errorMessage(err, t))
    } finally {
      setSubmitting(false)
    }
  }

  const titleKey = isEdit
    ? 'customEditTitle'
    : variant === 'workspace'
      ? 'customCreateWorkspaceTitle'
      : 'customCreateTitle'
  const subtitleKey = isEdit
    ? 'customEditSubtitle'
    : variant === 'workspace'
      ? 'customCreateWorkspaceSubtitle'
      : 'customCreateSubtitle'
  const submitKey = isEdit ? 'customEditSubmit' : 'customSubmit'
  const connectivityLocked = isEdit && initial?.lockConnectivity === true

  return (
    <div className="flex w-full flex-col gap-4 p-6" data-testid="mcp-admin-custom-form">
      <header className="flex flex-col gap-1">
        <h3 className="text-xl font-semibold tracking-tight">{t(titleKey)}</h3>
        <p className="text-sm text-muted-foreground">{t(subtitleKey)}</p>
      </header>

      <form
        className="flex flex-col gap-4"
        autoComplete="off"
        onSubmit={(e) => void handleSubmit(e)}
      >
        <Card>
          <CardHeader>
            <CardTitle>{t('customSectionServer')}</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="custom-name">{t('customFieldName')}</Label>
              <Input
                id="custom-name"
                name="mcp-connector-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t('customFieldNamePlaceholder')}
                autoComplete="off"
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                required
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="custom-url">{t('customFieldUrl')}</Label>
              <Input
                id="custom-url"
                type="url"
                name="mcp-connector-server-url"
                value={serverUrl}
                onChange={(e) => setServerUrl(e.target.value)}
                placeholder="https://example.com/mcp"
                autoComplete="off"
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                required
                disabled={connectivityLocked}
              />
              {connectivityLocked ? (
                <p className="text-xs text-muted-foreground">{t('customEditLockedHint')}</p>
              ) : null}
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="custom-transport">{t('customFieldTransport')}</Label>
                <Select
                  value={transport}
                  onValueChange={(v) => {
                    if (v) setTransport(v as MCPTransport)
                  }}
                  disabled={connectivityLocked}
                >
                  <SelectTrigger id="custom-transport" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="streamable_http">streamable_http</SelectItem>
                    <SelectItem value="sse">sse</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="flex flex-col gap-1.5">
                <Label htmlFor="custom-auth-method">{t('customFieldAuthMethod')}</Label>
                <Select
                  value={authMethod}
                  onValueChange={(v) => {
                    if (v) handleAuthMethodChange(v as MCPAuthMethod)
                  }}
                  disabled={isEdit}
                >
                  <SelectTrigger id="custom-auth-method" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="static">{t('authMethodStatic')}</SelectItem>
                    <SelectItem value="oauth">{t('authMethodOAuth')}</SelectItem>
                    <SelectItem value="none">{t('authMethodNone')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          </CardContent>
        </Card>

        {credentialFieldsNeeded && !isEdit ? (
          <Card>
            <CardHeader>
              <CardTitle>{t('customSectionCredential')}</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="custom-secret">{t('customFieldCredPlaintext')}</Label>
                <div className="relative">
                  <Input
                    id="custom-secret"
                    type={revealSecret ? 'text' : 'password'}
                    value={credentialPlaintext}
                    onChange={(e) => setCredentialPlaintext(e.target.value)}
                  />
                  <button
                    type="button"
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                    onClick={() => setRevealSecret((v) => !v)}
                    aria-label={revealSecret ? t('hideSecret') : t('revealSecret')}
                  >
                    {revealSecret ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
                  </button>
                </div>
              </div>
            </CardContent>
          </Card>
        ) : null}

        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={testing || !serverUrl.trim()}
            onClick={() => void handleTest()}
          >
            {testing ? <Loader2 data-icon="inline-start" className="animate-spin" /> : null}
            {t('customTestConnection')}
          </Button>
          {testResult ? (
            <div
              className={`flex items-center gap-1.5 text-sm ${
                testResult.ok ? 'text-success-fg' : 'text-destructive'
              }`}
            >
              {testResult.ok ? (
                <CheckCircle2 className="size-3.5" />
              ) : (
                <XCircle className="size-3.5" />
              )}
              {testResult.ok
                ? authMethod === 'oauth'
                  ? t('customTestOkOAuth')
                  : t('customTestOk', { count: testResult.tool_count })
                : (testResult.error_message ?? testResult.error_code ?? t('customTestFailed'))}
            </div>
          ) : null}
        </div>

        {error ? <p className="text-sm text-destructive">{error}</p> : null}

        <div className="flex justify-end gap-2">
          {isEdit && onCancel ? (
            <Button type="button" variant="outline" onClick={onCancel} disabled={submitting}>
              {t('customEditCancel')}
            </Button>
          ) : null}
          <Button type="submit" disabled={!canSubmit}>
            {submitting ? <Loader2 data-icon="inline-start" className="animate-spin" /> : null}
            {t(submitKey)}
          </Button>
        </div>
      </form>
    </div>
  )
}
