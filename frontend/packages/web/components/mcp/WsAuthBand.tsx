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
import {
  runOAuthFlow,
  wsCreateMyGrant,
  wsCreateWorkspaceGrant,
  wsDeleteMyGrant,
  wsDeleteWorkspaceGrant,
  wsMyGrantOAuthStart,
  wsWorkspaceGrantOAuthStart,
  type ApiClient,
  type MCPEffectiveConnector,
  type MCPOAuthStartResult,
} from '@cubebox/core'

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
  return <WsBandInner key={props.connector.install.install_id} {...props} />
}

function WsBandInner(props: WsAuthBandProps) {
  const { connector, client, wsId, callerRole, onChanged } = props
  const t = useTranslations('mcp.auth')
  const state = computeAuthBandState({ connector, callerRole, isOrgAdmin: false })
  const [inFlight, setInFlight] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | undefined>(undefined)

  const scope = wsScopeForBand(connector)
  const installId = connector.install.install_id

  const onConnect = async (): Promise<void> => {
    const flowInstallId = installId
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
    const flowInstallId = installId
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
    const flowInstallId = installId
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
  installId: string,
): () => Promise<MCPOAuthStartResult> {
  if (scope === 'workspace') {
    return () => wsWorkspaceGrantOAuthStart(client, wsId, installId)
  }
  return () => wsMyGrantOAuthStart(client, wsId, installId)
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
