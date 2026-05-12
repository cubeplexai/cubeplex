'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Eye, EyeOff, Loader2 } from 'lucide-react'
import {
  useMcpStore,
  type ApiClient,
  type MCPAuthMethod,
  type MCPServerCreateAdminBody,
  type MCPTransport,
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

type CredentialScope = 'org' | 'user' | 'none'

interface MCPCustomCreatePanelProps {
  client: ApiClient
  wsId: string
  onCreated: (serverId: string) => void
}

export function MCPCustomCreatePanel({ client, wsId, onCreated }: MCPCustomCreatePanelProps) {
  const t = useTranslations('mcpAdmin')
  const createCustom = useMcpStore((s) => s.createCustom)
  const fetchAll = useMcpStore((s) => s.fetchAll)

  const [name, setName] = useState('')
  const [serverUrl, setServerUrl] = useState('')
  const [transport, setTransport] = useState<MCPTransport>('streamable_http')
  const [authMethod, setAuthMethod] = useState<MCPAuthMethod>('static')
  const [credentialScope, setCredentialScope] = useState<CredentialScope>('org')
  const [credentialName, setCredentialName] = useState('')
  const [credentialPlaintext, setCredentialPlaintext] = useState('')
  const [revealSecret, setRevealSecret] = useState(false)
  const [timeout, setTimeoutSec] = useState('30')
  const [sseReadTimeout, setSseReadTimeout] = useState('60')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function handleAuthMethodChange(next: MCPAuthMethod): void {
    setAuthMethod(next)
    if (next === 'none') {
      setCredentialScope('none')
    } else if (credentialScope === 'none') {
      setCredentialScope('org')
    }
  }

  const credentialFieldsNeeded = authMethod === 'static' && credentialScope !== 'none'
  const canSubmit =
    !submitting &&
    name.trim().length > 0 &&
    serverUrl.trim().length > 0 &&
    (!credentialFieldsNeeded || credentialPlaintext.length > 0)

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)

    const body: MCPServerCreateAdminBody = {
      name: name.trim(),
      server_url: serverUrl.trim(),
      transport,
      auth_method: authMethod,
      credential_scope: credentialScope,
      timeout: Math.max(1, Number(timeout) || 30),
      sse_read_timeout: Math.max(1, Number(sseReadTimeout) || 60),
    }
    if (credentialFieldsNeeded) {
      body.credential_plaintext = credentialPlaintext
      if (credentialName.trim().length > 0) {
        body.credential_name = credentialName.trim()
      }
    }

    try {
      const created = await createCustom(client, body)
      // Re-fetch so the new server is merged with overrideCounts and any
      // catalog matching is reapplied.
      await fetchAll(client, wsId)
      onCreated(created.id)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex w-full flex-col gap-4 p-6" data-testid="mcp-admin-custom-form">
      <header className="flex flex-col gap-1">
        <h3 className="text-xl font-semibold tracking-tight">{t('customCreateTitle')}</h3>
        <p className="text-sm text-muted-foreground">{t('customCreateSubtitle')}</p>
      </header>

      <form className="flex flex-col gap-4" onSubmit={(e) => void handleSubmit(e)}>
        <Card>
          <CardHeader>
            <CardTitle>{t('customSectionServer')}</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="custom-name">{t('customFieldName')}</Label>
              <Input
                id="custom-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t('customFieldNamePlaceholder')}
                required
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="custom-url">{t('customFieldUrl')}</Label>
              <Input
                id="custom-url"
                type="url"
                value={serverUrl}
                onChange={(e) => setServerUrl(e.target.value)}
                placeholder="https://example.com/mcp"
                required
              />
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="custom-transport">{t('customFieldTransport')}</Label>
                <Select
                  value={transport}
                  onValueChange={(v) => setTransport((v ?? 'streamable_http') as MCPTransport)}
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
                <Label htmlFor="custom-auth">{t('customFieldAuthMethod')}</Label>
                <Select
                  value={authMethod}
                  onValueChange={(v) => handleAuthMethodChange((v ?? 'static') as MCPAuthMethod)}
                >
                  <SelectTrigger id="custom-auth" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="static">static</SelectItem>
                    <SelectItem value="oauth">oauth</SelectItem>
                    <SelectItem value="none">none</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="custom-timeout">{t('customFieldTimeout')}</Label>
                <Input
                  id="custom-timeout"
                  type="number"
                  min="1"
                  value={timeout}
                  onChange={(e) => setTimeoutSec(e.target.value)}
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <Label htmlFor="custom-sse">{t('customFieldSseTimeout')}</Label>
                <Input
                  id="custom-sse"
                  type="number"
                  min="1"
                  value={sseReadTimeout}
                  onChange={(e) => setSseReadTimeout(e.target.value)}
                />
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t('customSectionCredential')}</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="custom-scope">{t('customFieldScope')}</Label>
              <Select
                value={credentialScope}
                onValueChange={(v) => setCredentialScope((v ?? 'org') as CredentialScope)}
                disabled={authMethod === 'none'}
              >
                <SelectTrigger id="custom-scope" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="org">{t('customScopeOrg')}</SelectItem>
                  <SelectItem value="user">{t('customScopeUser')}</SelectItem>
                  <SelectItem value="none">{t('customScopeNone')}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {credentialFieldsNeeded && (
              <>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="custom-cred-name">{t('customFieldCredName')}</Label>
                  <Input
                    id="custom-cred-name"
                    value={credentialName}
                    onChange={(e) => setCredentialName(e.target.value)}
                    placeholder={t('customFieldCredNamePlaceholder')}
                  />
                </div>

                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="custom-cred">{t('customFieldCredPlaintext')}</Label>
                  <div className="flex gap-2">
                    <Input
                      id="custom-cred"
                      type={revealSecret ? 'text' : 'password'}
                      value={credentialPlaintext}
                      onChange={(e) => setCredentialPlaintext(e.target.value)}
                      autoComplete="new-password"
                      required
                    />
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      onClick={() => setRevealSecret((r) => !r)}
                      aria-label={revealSecret ? t('hideSecret') : t('revealSecret')}
                    >
                      {revealSecret ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                    </Button>
                  </div>
                </div>
              </>
            )}
          </CardContent>
        </Card>

        {error && <p className="text-xs text-destructive">{error}</p>}

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
