'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Loader2 } from 'lucide-react'
import {
  adminCreateInstall,
  type ApiClient,
  type MCPAuthMethod,
  type MCPConnectorTemplate,
  type MCPCredentialScope,
} from '@cubebox/core'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

type DistributionMode = 'all' | 'none'

interface MCPTemplateInstallPanelProps {
  template: MCPConnectorTemplate
  client: ApiClient
  onInstalled: (connectorId: string) => void
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
  onInstalled,
}: MCPTemplateInstallPanelProps) {
  const t = useTranslations('mcpAdmin')

  // Auth method is auto-picked at install time using a template-preferred
  // order (oauth > static > none). The admin can switch later from the
  // detail panel via PATCH install — that's the right place to choose,
  // since it sits next to the credential provisioning UI. Install itself
  // is just "wire this template to the org"; the credential method
  // decision belongs with credentials.
  const supported = useMemo(() => template.supported_auth_methods, [template])
  const authMethod = useMemo<MCPAuthMethod>(() => defaultAuthMethod(supported), [supported])
  // Distribution defaults to 'none' (each workspace opts in). 'all' fans
  // the install out into every existing workspace AND auto-enrolls new
  // ones. The default is the safer / less invasive choice for an admin
  // who just picked a template — they can flip it before clicking
  // Install if they really want everyone on by default.
  const [distribution, setDistribution] = useState<DistributionMode>('none')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDistribution('none')
    setError(null)
  }, [template.template_id])

  async function handleInstall(): Promise<void> {
    setSubmitting(true)
    setError(null)
    try {
      const policy = defaultPolicy(template.default_credential_policy, authMethod)
      const install = await adminCreateInstall(client, {
        template_id: template.template_id,
        install_scope: 'org',
        auth_method: authMethod,
        default_credential_policy: policy,
        auto_enable: { mode: distribution },
      })
      onInstalled(install.connector_id)
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
          <div className="flex flex-col gap-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t('distributionLabel')}
            </span>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                variant={distribution === 'none' ? 'default' : 'outline'}
                onClick={() => setDistribution('none')}
              >
                {t('distributionNone')}
              </Button>
              <Button
                type="button"
                size="sm"
                variant={distribution === 'all' ? 'default' : 'outline'}
                onClick={() => setDistribution('all')}
              >
                {t('distributionAll')}
              </Button>
            </div>
            <p className="text-xs leading-relaxed text-muted-foreground">
              {distribution === 'none' ? t('distributionNoneHint') : t('distributionAllHint')}
            </p>
          </div>

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
