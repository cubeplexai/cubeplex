'use client'

/**
 * Admin auth action band — admin-page-only variant.
 *
 * Spec: docs/superpowers/specs/2026-05-16-mcp-install-auth-handoff-spec.md §3,§4.
 *
 * Renders the auth action band on the admin detail panel. For org-scope
 * installs the workspace lens can misreport `required_grant_scope` (e.g. an
 * org install whose workspace lens overrides to `user` would surface the
 * wrong band on the admin page). On admin we always want the org-row
 * semantics — see spec §4 admin row. We therefore pre-fetch the dedicated
 * admin /effective endpoint and synthesize an `MCPEffectiveConnector` with
 * `required_grant_scope='org'` before handing it to {@link AuthBandFrame}.
 *
 * For workspace-scope installs the lens is already the right answer; no
 * extra call is needed.
 *
 * Binds admin-only write APIs (org-grant create/delete/oauth-start). No
 * workspace/user grant calls reach this component.
 */

import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  adminCreateOrgGrant,
  adminDeleteOrgGrant,
  adminGetInstallEffective,
  adminOrgGrantOAuthStart,
  runOAuthFlow,
  type ApiClient,
  type MCPAdminInstallEffective,
  type MCPEffectiveConnector,
} from '@cubebox/core'

import { computeAuthBandState } from './effectiveAuthState'
import { AuthBandFrame, type DisconnectOption } from './AuthBandFrame'

export interface AdminAuthBandProps {
  connector: MCPEffectiveConnector
  client: ApiClient
  /** Lens workspace id — used only for callback URL context, not for grant scope. */
  wsId: string
  onChanged: () => Promise<void>
}

export function AdminAuthBand(props: AdminAuthBandProps) {
  const { connector, client, onChanged } = props
  const isOrgScope = connector.install.install_scope === 'org'
  const [orgEffective, setOrgEffective] = useState<MCPAdminInstallEffective | null>(null)
  const [loaded, setLoaded] = useState(!isOrgScope)

  // Re-fetch on connector reference change too — the parent passes a fresh
  // MCPEffectiveConnector object on every list reload (after the band calls
  // onChanged() following a save/delete grant), so the reference flip is a
  // reliable signal that org-grant state may have changed. Without this,
  // /admin/.../effective stays cached at the pre-save reason and the
  // ready/needs-action band shows stale state until the user navigates away.
  useEffect(() => {
    if (!isOrgScope) {
      setLoaded(true)
      return
    }
    let cancelled = false
    setLoaded(false)
    void adminGetInstallEffective(client, connector.install.install_id)
      .then((res) => {
        if (cancelled) return
        setOrgEffective(res)
      })
      .catch(() => {
        // Fall back to lens connector — band will hide if reason is unknown.
      })
      .finally(() => {
        if (!cancelled) setLoaded(true)
      })
    return () => {
      cancelled = true
    }
  }, [client, connector, isOrgScope])

  if (!loaded) return null

  const synthesized: MCPEffectiveConnector =
    isOrgScope && orgEffective
      ? {
          ...connector,
          required_grant_scope: 'org',
          usable: orgEffective.usable,
          reason: orgEffective.reason,
          // For auth_method='none' installs the backend reports
          // reason='usable' AND no credential is involved at all. The ready
          // band has a dedicated "No credential required" sub-state that
          // keys off `credential_availability === 'not_required'` — forcing
          // 'available' here would route the band into the "credential from
          // <source>" variant even though credential_source is null, which
          // would render incorrectly. Mirror the backend's effective-state
          // mapping: auth_method='none' → not_required.
          credential_availability:
            connector.install.auth_method === 'none'
              ? 'not_required'
              : orgEffective.usable
                ? 'available'
                : 'missing',
          // The workspace lens (when the admin's current workspace overrides
          // the org-policy install down to user/workspace) surfaces a
          // different credential_source. For the org-row band we always want
          // 'org' (when usable + needs a credential) so the ready band reads
          // "credential from Org grant" and the Disconnect menu targets the
          // org grant — not whichever workspace-lens grant happened to be
          // there.
          credential_source:
            connector.install.auth_method === 'none' ? null : orgEffective.usable ? 'org' : null,
        }
      : connector

  return <AdminBandInner connector={synthesized} client={client} onChanged={onChanged} />
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
