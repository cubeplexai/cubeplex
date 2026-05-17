'use client'

/**
 * Admin auth action band — admin-page-only variant.
 *
 * Spec: docs/superpowers/specs/2026-05-16-mcp-install-auth-handoff-spec.md §3,§4.
 *
 * Consumes the admin-only `AdminOrgConnector` DTO, which already carries the
 * org-row effective state (`org_effective`). We adapt that into the shape
 * expected by {@link computeAuthBandState} (which is shared with the
 * workspace band and still types against `MCPEffectiveConnector`) by
 * synthesizing a connector with `required_grant_scope='org'`.
 *
 * Binds admin-only write APIs (org-grant create/delete/oauth-start). No
 * workspace/user grant calls reach this component.
 */

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  adminCreateOrgGrant,
  adminDeleteOrgGrant,
  adminOrgGrantOAuthStart,
  runOAuthFlow,
  type AdminOrgConnector,
  type ApiClient,
  type MCPEffectiveConnector,
} from '@cubebox/core'

import { computeAuthBandState } from './effectiveAuthState'
import { AuthBandFrame, type DisconnectOption } from './AuthBandFrame'

export interface AdminAuthBandProps {
  connector: AdminOrgConnector
  client: ApiClient
  onChanged: () => Promise<void>
}

export function AdminAuthBand(props: AdminAuthBandProps) {
  const { connector, client, onChanged } = props
  const synthesized = toEffectiveForAdmin(connector)
  return <AdminBandInner connector={synthesized} client={client} onChanged={onChanged} />
}

// Bridge between the new admin DTO and the shared band-state computer.
// `computeAuthBandState` is still typed against `MCPEffectiveConnector`
// (kept untouched because `WsAuthBand` also consumes it). The admin row
// always evaluates the org grant, so we pin `required_grant_scope='org'`
// and lift `usable` / `reason` / availability straight out of
// `connector.org_effective`.
function toEffectiveForAdmin(connector: AdminOrgConnector): MCPEffectiveConnector {
  const eff = connector.org_effective
  const isNoAuth = connector.install.auth_method === 'none'
  // Mirror the backend's effective-state mapping: for auth_method='none'
  // there's no credential involved, so the ready band's "no credential"
  // sub-state should fire (not "credential from <source>"). When a
  // credential IS required, surface 'available'/'missing' so the band
  // routes to the correct branch.
  const credentialAvailability: MCPEffectiveConnector['credential_availability'] = isNoAuth
    ? 'not_required'
    : eff.credential_availability === 'available'
      ? 'available'
      : 'missing'
  // The admin band always targets the org grant when a credential is
  // present, so `credential_source='org'` (or null when the install
  // doesn't take credentials, or isn't usable yet).
  const credentialSource: MCPEffectiveConnector['credential_source'] = isNoAuth
    ? null
    : eff.usable
      ? 'org'
      : null

  return {
    template: connector.template,
    install: connector.install,
    workspace_state: null,
    credential_policy: connector.install.default_credential_policy,
    required_grant_scope: 'org',
    credential_availability: credentialAvailability,
    credential_source: credentialSource,
    usable: eff.usable,
    reason: eff.reason,
  }
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

  const installId = connector.install.install_id

  const onConnect = async (): Promise<void> => {
    setInFlight(true)
    setErrorMessage(undefined)
    const result = await runOAuthFlow({
      startPost: () => adminOrgGrantOAuthStart(client, installId),
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
    try {
      await adminCreateOrgGrant(client, installId, { credential_plaintext: token })
      await onChanged()
    } catch (err) {
      setErrorMessage(`save_failed:${(err as Error).message}`)
    }
  }

  const onDelete = async (): Promise<void> => {
    await adminDeleteOrgGrant(client, installId)
    await onChanged()
  }

  // Admin band only ever targets the org grant — never workspace/user.
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
