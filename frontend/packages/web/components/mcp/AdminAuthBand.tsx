'use client'

/**
 * Admin auth action band — admin-page-only variant.
 *
 * Spec: docs/superpowers/specs/2026-05-16-mcp-install-auth-handoff-spec.md §3,§4.
 *
 * Consumes an `AdminCatalogRow`. The row's connector facts carry connector_id
 * and discovery_status; we synthesize an MCPEffectiveConnector shape so the
 * shared band-state computer ({@link computeAuthBandState}) can evaluate it.
 * The org_grant_status field on the row drives credential_availability.
 *
 * Binds admin-only write APIs (org-grant create/delete/oauth-start).
 *
 * When no org grant exists and the template supports multiple auth methods,
 * we render one Connect button per method instead of guessing which to use.
 */

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { AlertTriangle, Loader2 } from 'lucide-react'
import {
  adminCreateOrgGrant,
  adminDeleteOrgGrant,
  adminOrgGrantOAuthStart,
  runOAuthFlow,
  type AdminCatalogRow,
  type ApiClient,
  type MCPAuthMethod,
  type MCPConnector,
  type MCPConnectorTemplate,
  type MCPEffectiveConnector,
} from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

import { computeAuthBandState } from './effectiveAuthState'
import { AuthBandFrame, type DisconnectOption } from './AuthBandFrame'

export interface AdminAuthBandProps {
  row: AdminCatalogRow
  client: ApiClient
  onChanged: () => Promise<void>
}

export function AdminAuthBand(props: AdminAuthBandProps) {
  const { row, client, onChanged } = props
  const connectorId = row.connector?.connector_id
  if (!connectorId) return null

  const hasGrant = row.org_grant_status !== null
  const supported = (row.template.supported_auth_methods as MCPAuthMethod[]).filter(
    (m) => m !== 'none',
  )

  // When no grant and template supports multiple auth methods, show one
  // Connect button per method so the admin explicitly chooses — no guessing.
  if (!hasGrant && supported.length >= 2) {
    return (
      <AdminNoGrantMultiMethod
        key={connectorId}
        connectorId={connectorId}
        supported={supported}
        providerLabel={row.template.provider || row.template.name}
        client={client}
        onChanged={onChanged}
      />
    )
  }

  const synthesized = toEffectiveForAdmin(row)
  return (
    <AdminBandInner
      key={connectorId}
      connector={synthesized}
      client={client}
      onChanged={onChanged}
    />
  )
}

// Bridge between the new AdminCatalogRow DTO and the shared band-state computer.
// The admin row always evaluates the org grant, so we pin required_grant_scope='org'
// and derive usable/reason/availability from org_grant_status + connector.discovery_status.
function toEffectiveForAdmin(row: AdminCatalogRow): MCPEffectiveConnector {
  const facts = row.connector!
  const grantStatus = row.org_grant_status

  const usable = grantStatus === 'valid' && facts.discovery_status === 'ok'
  const credentialAvailability: MCPEffectiveConnector['credential_availability'] =
    grantStatus === 'valid' ? 'available' : grantStatus === 'expired' ? 'missing' : 'not_required'
  const credentialSource: MCPEffectiveConnector['credential_source'] =
    grantStatus === 'valid' ? 'org' : null

  // Derive a reason for the band-state computer.
  let reason: string
  if (usable) {
    reason = 'usable'
  } else if (grantStatus === 'expired') {
    reason = 'grant_expired'
  } else if (grantStatus === null) {
    reason = 'missing_org_grant'
  } else {
    // grant is 'valid' but discovery hasn't succeeded yet.
    reason = 'discovery_failed'
  }

  // When a grant exists, use the method recorded on the grant (the method it was
  // minted with). When no grant exists, use the single supported method if there
  // is exactly one (multi-method no-grant is handled by AdminNoGrantMultiMethod
  // before reaching this function, so supported.length >= 2 won't appear here).
  const singleSupportedMethod = (row.template.supported_auth_methods as MCPAuthMethod[]).find(
    (m) => m !== 'none',
  )
  const grantAuthMethod: MCPConnector['auth_method'] =
    grantStatus !== null && facts.org_grant_auth_method != null
      ? (facts.org_grant_auth_method as MCPConnector['auth_method'])
      : (singleSupportedMethod ?? 'none')

  // Synthesize MCPConnector shape expected by effectiveAuthState.
  const syntheticInstall: MCPConnector = {
    connector_id: facts.connector_id,
    template_id: row.template.template_id,
    install_scope: 'org',
    workspace_id: null,
    name: row.template.name,
    server_url: row.template.server_url,
    transport: row.template.transport as 'streamable_http' | 'sse',
    auth_method: grantAuthMethod,
    default_credential_policy:
      facts.default_credential_policy as MCPConnector['default_credential_policy'],
    auth_status: grantStatus === 'valid' ? 'authorized' : 'pending',
    discovery_status: facts.discovery_status,
    install_state: 'active',
    tool_count: facts.tool_count,
    tools: facts.tools,
    tool_citations: facts.tool_citations as unknown as MCPConnector['tool_citations'],
    last_error: facts.last_error,
    auto_enroll_new_workspaces: facts.auto_enroll_new_workspaces,
  }

  const syntheticTemplate: MCPConnectorTemplate = {
    template_id: row.template.template_id,
    slug: row.template.slug,
    name: row.template.name,
    provider: row.template.provider,
    description: row.template.description,
    server_url: row.template.server_url,
    transport: row.template.transport as 'streamable_http' | 'sse',
    supported_auth_methods: row.template.supported_auth_methods as MCPConnector['auth_method'][],
    default_credential_policy:
      facts.default_credential_policy as MCPConnector['default_credential_policy'],
    static_form_schema: null,
    status: row.template.status as 'active' | 'deprecated' | 'disabled',
  }

  return {
    template: syntheticTemplate,
    install: syntheticInstall,
    workspace_state: null,
    credential_policy: facts.default_credential_policy as MCPConnector['default_credential_policy'],
    required_grant_scope: 'org',
    credential_availability: credentialAvailability,
    credential_source: credentialSource,
    credential_availability_by_scope: {
      org: credentialSource === 'org',
      workspace: false,
      user: false,
    },
    usable,
    reason,
  }
}

/**
 * Shown when no org grant exists and the template supports multiple auth methods.
 * Renders one Connect action per method so the admin explicitly picks — avoids
 * guessing which method to present.
 */
function AdminNoGrantMultiMethod({
  connectorId,
  supported,
  providerLabel,
  client,
  onChanged,
}: {
  connectorId: string
  supported: MCPAuthMethod[]
  providerLabel: string
  client: ApiClient
  onChanged: () => Promise<void>
}) {
  const t = useTranslations('mcp.auth')
  const [inFlight, setInFlight] = useState<MCPAuthMethod | null>(null)
  const [staticToken, setStaticToken] = useState('')
  const [showStaticForm, setShowStaticForm] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | undefined>(undefined)

  const handleOAuth = async (): Promise<void> => {
    setInFlight('oauth')
    setErrorMessage(undefined)
    const result = await runOAuthFlow({
      startPost: () => adminOrgGrantOAuthStart(client, connectorId),
    })
    setInFlight(null)
    if (result.status === 'ok') {
      await onChanged()
      return
    }
    if (result.status === 'cancelled') return
    setErrorMessage(result.reason)
  }

  const handleSaveStatic = async (): Promise<void> => {
    if (!staticToken) return
    setInFlight('static')
    setErrorMessage(undefined)
    try {
      await adminCreateOrgGrant(client, connectorId, { credential_plaintext: staticToken })
      await onChanged()
    } catch (err) {
      setErrorMessage(`save_failed:${(err as Error).message}`)
    } finally {
      setInFlight(null)
      setStaticToken('')
    }
  }

  const busy = inFlight !== null

  return (
    <div
      role="status"
      data-testid="mcp-auth-band"
      className="flex flex-col gap-2 rounded-lg border border-warning-border bg-warning-surface px-3 py-2.5 text-sm text-warning-fg"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 size-4 shrink-0" />
        <div className="flex flex-1 flex-col gap-1">
          <p className="font-medium">{t('bandTitleNeedsAction')}</p>
          <p className="text-xs text-muted-foreground">{t('reasonMissingOrgGrantSelf')}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {supported.map((method) => {
            if (method === 'oauth') {
              return (
                <Button
                  key="oauth"
                  size="sm"
                  disabled={busy}
                  onClick={() => void handleOAuth()}
                  data-testid="connect-oauth"
                >
                  {inFlight === 'oauth' ? (
                    <Loader2 data-icon="inline-start" className="animate-spin" />
                  ) : null}
                  {t('connectButton', { provider: providerLabel })}
                </Button>
              )
            }
            if (method === 'static') {
              return (
                <Button
                  key="static"
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() => setShowStaticForm((v) => !v)}
                  data-testid="connect-static"
                >
                  {t('staticTokenSave')}
                </Button>
              )
            }
            return null
          })}
        </div>
      </div>
      {showStaticForm && (
        <div className="flex flex-wrap items-center gap-2 pl-7">
          <Input
            type="password"
            value={staticToken}
            onChange={(e) => setStaticToken(e.target.value)}
            placeholder={t('staticTokenLabel')}
            className="max-w-xs"
            aria-label={t('staticTokenLabel')}
          />
          <Button size="sm" disabled={!staticToken || busy} onClick={() => void handleSaveStatic()}>
            {inFlight === 'static' ? (
              <Loader2 data-icon="inline-start" className="animate-spin" />
            ) : null}
            {t('staticTokenSave')}
          </Button>
        </div>
      )}
      {errorMessage && (
        <p className="pl-7 text-xs text-destructive">
          {errorMessage.startsWith('start_failed:')
            ? errorMessage.slice('start_failed:'.length)
            : errorMessage}
        </p>
      )}
    </div>
  )
}

function AdminBandInner({
  connector,
  client,
  onChanged,
}: {
  connector: MCPEffectiveConnector
  client: ApiClient
  onChanged: () => Promise<void>
}) {
  const t = useTranslations('mcp.auth')
  const state = computeAuthBandState({ connector, callerRole: 'admin', isOrgAdmin: true })
  const [inFlight, setInFlight] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | undefined>(undefined)

  const connectorId = connector.install.connector_id

  const onConnect = async (): Promise<void> => {
    const flowInstallId = connectorId
    setInFlight(true)
    setErrorMessage(undefined)
    const result = await runOAuthFlow({
      startPost: () => adminOrgGrantOAuthStart(client, flowInstallId),
    })
    setInFlight(false)
    if (result.status === 'ok') {
      await onChanged()
      return
    }
    if (result.status === 'cancelled') return
    setErrorMessage(result.reason)
  }

  const onSaveStaticToken = async (token: string): Promise<void> => {
    const flowInstallId = connectorId
    try {
      await adminCreateOrgGrant(client, flowInstallId, { credential_plaintext: token })
      await onChanged()
    } catch (err) {
      setErrorMessage(`save_failed:${(err as Error).message}`)
    }
  }

  const onDelete = async (): Promise<void> => {
    const flowInstallId = connectorId
    await adminDeleteOrgGrant(client, flowInstallId)
    await onChanged()
  }

  const source =
    state.kind === 'ready' && state.subkind === 'with_credential' ? state.source : undefined
  const disconnectOptions: DisconnectOption[] =
    source === 'org'
      ? [
          {
            scope: 'org',
            label: t('removeOrgGrant'),
            onClick: () => void onDelete(),
          },
        ]
      : []

  return (
    <AuthBandFrame
      state={state}
      authMethod={connector.install.auth_method === 'oauth' ? 'oauth' : 'static'}
      providerLabel={providerLabel(connector)}
      onConnect={() => void onConnect()}
      onSaveStaticToken={(token) => void onSaveStaticToken(token)}
      disconnectOptions={disconnectOptions}
      onRetryError={() => setErrorMessage(undefined)}
      errorMessage={errorMessage}
      inFlight={inFlight}
    />
  )
}

function providerLabel(connector: MCPEffectiveConnector): string {
  return connector.template?.provider || connector.template?.name || connector.install.name
}
