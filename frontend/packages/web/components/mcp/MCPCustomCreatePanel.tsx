'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { CheckCircle2, Eye, EyeOff, Loader2, XCircle } from 'lucide-react'
import {
  adminCreateInstall,
  adminTestConnection,
  wsCreateInstall,
  type ApiClient,
  type MCPAuthMethod,
  type MCPConnector,
  type MCPCredentialScope,
  type MCPTransport,
  type TestConnectionResult,
} from '@cubebox/core'
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

interface MCPCustomCreatePanelProps {
  client: ApiClient
  scope?: 'org' | 'workspace'
  wsId?: string
  onCreated: (install: MCPConnector) => void
}

export function MCPCustomCreatePanel({
  client,
  scope = 'org',
  wsId,
  onCreated,
}: MCPCustomCreatePanelProps) {
  const t = useTranslations('mcpAdmin')
  const isWorkspaceScope = scope === 'workspace'

  const [name, setName] = useState('')
  const [serverUrl, setServerUrl] = useState('')
  const [transport, setTransport] = useState<MCPTransport>('streamable_http')
  const [authMethod, setAuthMethod] = useState<MCPAuthMethod>('static')
  const [credentialPolicy, setCredentialPolicy] = useState<MCPCredentialScope>(
    isWorkspaceScope ? 'workspace' : 'org',
  )
  const [credentialPlaintext, setCredentialPlaintext] = useState('')
  const [revealSecret, setRevealSecret] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<TestConnectionResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  function handleAuthMethodChange(next: MCPAuthMethod): void {
    setAuthMethod(next)
    if (next === 'none') {
      setCredentialPolicy('none')
    } else if (credentialPolicy === 'none') {
      setCredentialPolicy(isWorkspaceScope ? 'workspace' : 'org')
    }
  }

  const credentialFieldsNeeded =
    !isWorkspaceScope && authMethod === 'static' && credentialPolicy === 'org'
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
      const body: Record<string, unknown> = {
        template_id: null,
        install_scope: scope,
        auth_method: authMethod,
        default_credential_policy: credentialPolicy,
        name: name.trim(),
        server_url: serverUrl.trim(),
        transport,
        auto_enable: { mode: 'none' },
      }
      if (credentialFieldsNeeded) {
        body.credential_plaintext = credentialPlaintext
      }
      const created =
        isWorkspaceScope && wsId
          ? await wsCreateInstall(client, wsId, body)
          : await adminCreateInstall(client, body)
      onCreated(created)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex w-full flex-col gap-4 p-6" data-testid="mcp-admin-custom-form">
      <header className="flex flex-col gap-1">
        <h3 className="text-xl font-semibold tracking-tight">
          {isWorkspaceScope ? t('customCreateWorkspaceTitle') : t('customCreateTitle')}
        </h3>
        <p className="text-sm text-muted-foreground">
          {isWorkspaceScope ? t('customCreateWorkspaceSubtitle') : t('customCreateSubtitle')}
        </p>
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
              />
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="custom-transport">{t('customFieldTransport')}</Label>
                <Select
                  value={transport}
                  onValueChange={(v) => {
                    if (v) setTransport(v as MCPTransport)
                  }}
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

        {authMethod !== 'none' ? (
          <Card>
            <CardHeader>
              <CardTitle>{t('customSectionCredential')}</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="custom-policy">{t('customFieldScope')}</Label>
                <Select
                  value={credentialPolicy}
                  onValueChange={(v) => {
                    if (v) setCredentialPolicy(v as MCPCredentialScope)
                  }}
                >
                  <SelectTrigger id="custom-policy" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {!isWorkspaceScope ? (
                      <SelectItem value="org">{t('customScopeOrg')}</SelectItem>
                    ) : null}
                    <SelectItem value="workspace">{t('scopeWorkspace')}</SelectItem>
                    <SelectItem value="user">{t('customScopeUser')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {credentialFieldsNeeded ? (
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="custom-secret">{t('customFieldCredPlaintext')}</Label>
                  <div className="relative">
                    <Input
                      id="custom-secret"
                      type={revealSecret ? 'text' : 'password'}
                      value={credentialPlaintext}
                      onChange={(e) => setCredentialPlaintext(e.target.value)}
                      name="mcp-credential-plaintext"
                      autoComplete="new-password"
                      autoCapitalize="off"
                      autoCorrect="off"
                      spellCheck={false}
                      required
                    />
                    <button
                      type="button"
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                      onClick={() => setRevealSecret((v) => !v)}
                      aria-label={revealSecret ? t('hideSecret') : t('revealSecret')}
                    >
                      {revealSecret ? (
                        <EyeOff className="size-3.5" />
                      ) : (
                        <Eye className="size-3.5" />
                      )}
                    </button>
                  </div>
                </div>
              ) : null}
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
                ? t('customTestOk', { count: testResult.tool_count })
                : (testResult.error_message ?? testResult.error_code ?? t('customTestFailed'))}
            </div>
          ) : null}
        </div>

        {error ? <p className="text-sm text-destructive">{error}</p> : null}

        <div className="flex justify-end">
          <Button type="submit" disabled={!canSubmit}>
            {submitting ? <Loader2 data-icon="inline-start" className="animate-spin" /> : null}
            {t('customSubmit')}
          </Button>
        </div>
      </form>
    </div>
  )
}
