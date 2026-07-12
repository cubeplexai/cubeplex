'use client'

/**
 * Workspace auth action band — workspace-page-only variant.
 *
 * Spec: docs/superpowers/specs/2026-05-16-mcp-install-auth-handoff-spec.md §3,§4.
 *
 * Binds workspace-only write APIs (workspace + per-user grant
 * create/delete/oauth-start). No admin org-grant calls reach this
 * component — workspace admins must use the admin page to manage org
 * credentials, even if they're also org-admins. Spec §4: workspace lens
 * never offers "remove org grant" to avoid cross-workspace blast radius.
 */

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { AlertTriangle, Loader2 } from 'lucide-react'
import {
  runOAuthFlow,
  wsCreateMyGrant,
  wsCreateWorkspaceGrant,
  wsDeleteMyGrant,
  wsDeleteWorkspaceGrant,
  wsMyGrantOAuthStart,
  wsWorkspaceGrantOAuthStart,
  type ApiClient,
  type MCPAuthMethod,
  type MCPEffectiveConnector,
  type MCPOAuthStartResult,
} from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

import { computeAuthBandState } from './effectiveAuthState'
import { AuthBandFrame, type DisconnectOption } from './AuthBandFrame'

type WsScope = 'workspace' | 'user'

export interface WsAuthBandProps {
  connector: MCPEffectiveConnector
  client: ApiClient
  /** Lens workspace id for all workspace/user grant calls. */
  wsId: string
  callerRole: 'admin' | 'member'
  onChanged: () => Promise<void>
}

export function WsAuthBand(props: WsAuthBandProps) {
  const { connector, client, wsId, callerRole, onChanged } = props
  const connectorId = connector.install.connector_id

  // Only show WsNoGrantMultiMethod when the caller can actually complete a grant
  // (computeAuthBandState returns 'needs-action'). If the policy is 'org', the
  // band state will be 'awaiting-others' for non-org-admins — we must not render
  // Connect buttons the caller cannot submit. If credential_policy='workspace' and
  // the caller is a plain member, state is also 'awaiting-others'.
  const bandState = computeAuthBandState({ connector, callerRole, isOrgAdmin: false })
  const scope = wsScopeForBand(connector)
  const supported = (connector.template?.supported_auth_methods ?? []).filter(
    (m): m is MCPAuthMethod => m !== 'none',
  )
  if (bandState.kind === 'needs-action' && supported.length >= 2) {
    return (
      <WsNoGrantMultiMethod
        key={connectorId}
        connectorId={connectorId}
        supported={supported}
        scope={scope}
        providerLabel={
          connector.template?.provider || connector.template?.name || connector.install.name
        }
        client={client}
        wsId={wsId}
        onChanged={onChanged}
      />
    )
  }

  return <WsBandInner key={connectorId} {...props} />
}

function WsBandInner(props: WsAuthBandProps) {
  const { connector, client, wsId, callerRole, onChanged } = props
  const t = useTranslations('mcp.auth')
  const state = computeAuthBandState({ connector, callerRole, isOrgAdmin: false })
  const [inFlight, setInFlight] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | undefined>(undefined)

  const scope = wsScopeForBand(connector)
  const connectorId = connector.install.connector_id

  const onConnect = async (): Promise<void> => {
    const flowInstallId = connectorId
    setInFlight(true)
    setErrorMessage(undefined)
    const startPost = oauthStartFn(scope, client, wsId, flowInstallId)
    const result = await runOAuthFlow({ startPost })
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
      const body = { credential_plaintext: token }
      if (scope === 'workspace') {
        await wsCreateWorkspaceGrant(client, wsId, flowInstallId, body)
      } else {
        await wsCreateMyGrant(client, wsId, flowInstallId, body)
      }
      await onChanged()
    } catch (err) {
      setErrorMessage(`save_failed:${(err as Error).message}`)
    }
  }

  const onDelete = async (target: WsScope): Promise<void> => {
    const flowInstallId = connectorId
    if (target === 'workspace') {
      await wsDeleteWorkspaceGrant(client, wsId, flowInstallId)
    } else {
      await wsDeleteMyGrant(client, wsId, flowInstallId)
    }
    await onChanged()
  }

  const source =
    state.kind === 'ready' && state.subkind === 'with_credential' ? state.source : undefined
  const disconnectOptions: DisconnectOption[] = disconnectsForWsCaller(callerRole, source).map(
    (s) => ({
      scope: s,
      label: s === 'workspace' ? t('removeWsGrant') : t('removeMyGrant'),
      onClick: () => void onDelete(s),
    }),
  )

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

/**
 * Shown when no ws-scope grant exists and the template supports multiple auth methods.
 * Renders one Connect action per method so the user explicitly picks — mirrors AdminNoGrantMultiMethod.
 */
function WsNoGrantMultiMethod({
  connectorId,
  supported,
  scope,
  providerLabel,
  client,
  wsId,
  onChanged,
}: {
  connectorId: string
  supported: MCPAuthMethod[]
  scope: WsScope
  providerLabel: string
  client: ApiClient
  wsId: string
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
    const startPost = oauthStartFn(scope, client, wsId, connectorId)
    const result = await runOAuthFlow({ startPost })
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
      const body = { credential_plaintext: staticToken }
      if (scope === 'workspace') {
        await wsCreateWorkspaceGrant(client, wsId, connectorId, body)
      } else {
        await wsCreateMyGrant(client, wsId, connectorId, body)
      }
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

// ---------- helpers ---------- //

function wsScopeForBand(connector: MCPEffectiveConnector): WsScope {
  const r = connector.required_grant_scope
  // The ws-page band never originates org grants — even if the connector's
  // required scope says 'org', a workspace caller can only act on
  // workspace/user grants. The auth band state derivation already routes
  // missing-org-grant into awaiting-others rather than needs-action, so
  // this fallback is only reached for static-token entry / disconnect
  // menu wiring, where defaulting to 'user' is the safe choice.
  if (r === 'workspace' || r === 'user') return r
  const policy = connector.credential_policy
  if (policy === 'workspace' || policy === 'user') return policy
  return 'user'
}

function oauthStartFn(
  scope: WsScope,
  client: ApiClient,
  wsId: string,
  connectorId: string,
): () => Promise<MCPOAuthStartResult> {
  if (scope === 'workspace') {
    return () => wsWorkspaceGrantOAuthStart(client, wsId, connectorId)
  }
  return () => wsMyGrantOAuthStart(client, wsId, connectorId)
}

function providerLabel(connector: MCPEffectiveConnector): string {
  return connector.template?.provider || connector.template?.name || connector.install.name
}

/**
 * Which "remove X grant" entries to show in the disconnect menu, based on
 * the caller's workspace authority. Spec §4: the workspace lens never
 * offers "remove org grant" — that lives on the admin page.
 */
function disconnectsForWsCaller(
  callerRole: 'admin' | 'member',
  source: 'org' | 'workspace' | 'user' | undefined,
): WsScope[] {
  const items: WsScope[] = []
  if (source === 'workspace' && callerRole === 'admin') items.push('workspace')
  if (source === 'user') items.push('user')
  return items
}
