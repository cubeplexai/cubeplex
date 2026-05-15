'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Loader2 } from 'lucide-react'
import {
  wsCreateInstall,
  type ApiClient,
  type MCPAuthMethod,
  type MCPConnectorTemplate,
  type MCPCredentialScope,
} from '@cubebox/core'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

interface MCPTemplateInstallPanelProps {
  template: MCPConnectorTemplate
  client: ApiClient
  wsId: string
  onInstalled: (installId: string) => void
}

function defaultAuthMethod(supported: MCPAuthMethod[]): MCPAuthMethod {
  if (supported.includes('oauth')) return 'oauth'
  if (supported.includes('static')) return 'static'
  return 'none'
}

function defaultPolicy(scope: MCPCredentialScope, method: MCPAuthMethod): MCPCredentialScope {
  if (method === 'none') return 'none'
  if (scope === 'none') return 'user'
  return scope
}

export function MCPTemplateInstallPanel({
  template,
  client,
  wsId,
  onInstalled,
}: MCPTemplateInstallPanelProps) {
  const t = useTranslations('mcpAdmin')

  const supported = useMemo(() => template.supported_auth_methods, [template])
  const [authMethod, setAuthMethod] = useState<MCPAuthMethod>(() => defaultAuthMethod(supported))
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setAuthMethod(defaultAuthMethod(supported))
    setError(null)
  }, [template.template_id, supported])

  async function handleInstall(): Promise<void> {
    setSubmitting(true)
    setError(null)
    try {
      const policy = defaultPolicy(template.default_credential_policy, authMethod)
      const install = await wsCreateInstall(client, wsId, {
        template_id: template.template_id,
        install_scope: 'workspace',
        auth_method: authMethod,
        default_credential_policy: policy,
      })
      onInstalled(install.install_id)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex w-full flex-col gap-4 p-6" data-testid="mcp-template-install-panel">
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-xl font-semibold tracking-tight">{template.name}</h3>
          {template.provider && (
            <Badge variant="outline" className="text-[11px]">
              {template.provider}
            </Badge>
          )}
          <Badge variant="secondary" className="text-[11px]">
            {t('templates')}
          </Badge>
        </div>
        {template.description && (
          <p className="text-sm leading-relaxed text-muted-foreground">{template.description}</p>
        )}
      </header>

      {error && (
        <Alert variant="destructive">
          <AlertTitle>{t('templateInstallError')}</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <Card>
        <CardHeader>
          <CardTitle>{t('templateInstallTitle')}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {supported.length > 1 && (
            <div className="flex gap-2">
              {supported.map((m) => (
                <Button
                  key={m}
                  type="button"
                  size="sm"
                  variant={authMethod === m ? 'default' : 'outline'}
                  onClick={() => setAuthMethod(m)}
                >
                  {t(
                    m === 'oauth'
                      ? 'authMethodOAuth'
                      : m === 'static'
                        ? 'authMethodStatic'
                        : 'authMethodNone',
                  )}
                </Button>
              ))}
            </div>
          )}

          <p className="text-xs text-muted-foreground">{t('templateInstallNotice')}</p>

          <div className="flex justify-end">
            <Button type="button" disabled={submitting} onClick={() => void handleInstall()}>
              {submitting ? <Loader2 data-icon="inline-start" className="animate-spin" /> : null}
              {t('installButton')}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
