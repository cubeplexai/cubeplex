'use client'

/**
 * Authentication action band — five mutually exclusive states.
 * Spec: docs/superpowers/specs/2026-05-16-mcp-install-auth-handoff-spec.md §3.
 *
 * Intermediate form: delegates all visual rendering to AuthBandFrame and only
 * binds API callbacks per caller scope. Task 13 will split this into
 * AdminAuthBand + WsAuthBand and delete this file.
 */

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  runOAuthFlow,
  wsCreateMyGrant,
  wsCreateWorkspaceGrant,
  adminCreateOrgGrant,
  wsMyGrantOAuthStart,
  wsWorkspaceGrantOAuthStart,
  adminOrgGrantOAuthStart,
  wsDeleteMyGrant,
  wsDeleteWorkspaceGrant,
  adminDeleteOrgGrant,
  type ApiClient,
  type MCPEffectiveConnector,
  type MCPOAuthStartResult,
} from '@cubebox/core'

import { computeAuthBandState } from './effectiveAuthState'
import { AuthBandFrame, type DisconnectOption } from './AuthBandFrame'

type Scope = 'org' | 'workspace' | 'user'

export interface AuthActionBandProps {
  connector: MCPEffectiveConnector
  client: ApiClient
  /** For workspace-scope OAuth/grant calls, lens workspace id. */
  wsId: string
  callerRole: 'admin' | 'member'
  isOrgAdmin: boolean
  onChanged: () => Promise<void>
}

export function AuthActionBand(props: AuthActionBandProps) {
  const t = useTranslations('mcp.auth')
  const state = computeAuthBandState({
    connector: props.connector,
    callerRole: props.callerRole,
    isOrgAdmin: props.isOrgAdmin,
  })
  const [inFlight, setInFlight] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | undefined>(undefined)

  const scope = scopeForBand(props.connector)
  const installId = props.connector.install.install_id

  const onConnect = async (): Promise<void> => {
    setInFlight(true)
    setErrorMessage(undefined)
    const startPost = oauthStartFn(scope, props)
    const result = await runOAuthFlow({ startPost })
    setInFlight(false)
    if (result.status === 'ok') {
      await props.onChanged()
      return
    }
    if (result.status === 'cancelled') return
    setErrorMessage(result.reason)
  }

  const onSaveStaticToken = async (token: string): Promise<void> => {
    try {
      const body = { credential_plaintext: token }
      if (scope === 'org') {
        await adminCreateOrgGrant(props.client, installId, body)
      } else if (scope === 'workspace') {
        await wsCreateWorkspaceGrant(props.client, props.wsId, installId, body)
      } else {
        await wsCreateMyGrant(props.client, props.wsId, installId, body)
      }
      await props.onChanged()
    } catch (err) {
      setErrorMessage(`save_failed:${(err as Error).message}`)
    }
  }

  const onDelete = async (target: Scope): Promise<void> => {
    if (target === 'org') {
      await adminDeleteOrgGrant(props.client, installId)
    } else if (target === 'workspace') {
      await wsDeleteWorkspaceGrant(props.client, props.wsId, installId)
    } else {
      await wsDeleteMyGrant(props.client, props.wsId, installId)
    }
    await props.onChanged()
  }

  const source =
    state.kind === 'ready' && state.subkind === 'with_credential' ? state.source : undefined
  const disconnectOptions: DisconnectOption[] = disconnectsForCaller(props, source).map((s) => ({
    scope: s,
    label:
      s === 'org'
        ? t('removeOrgGrant')
        : s === 'workspace'
          ? t('removeWsGrant')
          : t('removeMyGrant'),
    onClick: () => void onDelete(s),
  }))

  return (
    <AuthBandFrame
      state={state}
      authMethod={props.connector.install.auth_method === 'oauth' ? 'oauth' : 'static'}
      providerLabel={providerLabel(props.connector)}
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

function scopeForBand(connector: MCPEffectiveConnector): Scope {
  const r = connector.required_grant_scope
  if (r === 'org' || r === 'workspace' || r === 'user') return r
  // Fallback: derive from credential_policy. Spec §4 admin-row override is
  // handled by the caller pre-synthesizing `required_grant_scope='org'`.
  const policy = connector.credential_policy
  if (policy === 'org' || policy === 'workspace' || policy === 'user') return policy
  return 'user'
}

function oauthStartFn(
  scope: Scope,
  props: AuthActionBandProps,
): () => Promise<MCPOAuthStartResult> {
  const installId = props.connector.install.install_id
  if (scope === 'org') return () => adminOrgGrantOAuthStart(props.client, installId)
  if (scope === 'workspace')
    return () => wsWorkspaceGrantOAuthStart(props.client, props.wsId, installId)
  return () => wsMyGrantOAuthStart(props.client, props.wsId, installId)
}

function providerLabel(connector: MCPEffectiveConnector): string {
  return connector.template?.provider || connector.template?.name || connector.install.name
}

/**
 * Which "remove X grant" entries to show in the disconnect menu, based on the
 * caller's authority. Spec §4.
 */
function disconnectsForCaller(
  props: AuthActionBandProps,
  source: 'org' | 'workspace' | 'user' | undefined,
): Scope[] {
  const items: Scope[] = []
  // Only offer to remove the layer that actually supplied the credential —
  // we don't want a member to be able to revoke an org grant even if they
  // see "credential from org" in their detail panel.
  if (source === 'org' && props.isOrgAdmin) items.push('org')
  if (source === 'workspace' && (props.isOrgAdmin || props.callerRole === 'admin'))
    items.push('workspace')
  if (source === 'user') items.push('user')
  return items
}
