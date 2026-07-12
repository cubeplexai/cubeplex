import type { MCPEffectiveConnector } from '@cubeplex/core'

export type AuthBandState =
  | { kind: 'hidden' }
  | {
      kind: 'ready'
      subkind: 'with_credential' | 'no_credential'
      source?: 'org' | 'workspace' | 'user'
    }
  | { kind: 'needs-action'; reason: AuthReason }
  | { kind: 'awaiting-others'; reason: AuthReason; who: 'org_admin' | 'workspace_admin' }
  | { kind: 'oauth-in-flight' }
  | { kind: 'error'; reason?: string }

export type AuthReason =
  | 'pending_oauth'
  | 'missing_org_grant'
  | 'missing_workspace_grant'
  | 'user_needs_connection'
  | 'grant_expired'

export interface AuthBandInputs {
  connector: MCPEffectiveConnector
  callerRole: 'admin' | 'member'
  isOrgAdmin: boolean
}

const AUTH_REASONS = new Set<AuthReason>([
  'pending_oauth',
  'missing_org_grant',
  'missing_workspace_grant',
  'user_needs_connection',
  'grant_expired',
])

export function computeAuthBandState({
  connector,
  callerRole,
  isOrgAdmin,
}: AuthBandInputs): AuthBandState {
  // Spec §3.1.
  if (connector.usable) {
    if (connector.credential_availability === 'not_required') {
      return { kind: 'ready', subkind: 'no_credential' }
    }
    return {
      kind: 'ready',
      subkind: 'with_credential',
      source: connector.credential_source ?? undefined,
    }
  }

  const reason = connector.reason as AuthReason | string
  if (!AUTH_REASONS.has(reason as AuthReason)) {
    // Non-auth blockers (not_installed / install_uninstalled /
    // template_deprecated / not_enabled_in_workspace / discovery_failed)
    // belong to other surfaces — see spec §3.2 / §3.3.
    return { kind: 'hidden' }
  }

  const required = connector.required_grant_scope
  const r = reason as AuthReason

  // Spec §4 + §3.3.
  if (required === 'org') {
    if (isOrgAdmin) return { kind: 'needs-action', reason: r }
    return { kind: 'awaiting-others', reason: r, who: 'org_admin' }
  }
  if (required === 'workspace') {
    if (callerRole === 'admin') return { kind: 'needs-action', reason: r }
    return { kind: 'awaiting-others', reason: r, who: 'workspace_admin' }
  }
  // required === 'user' — caller always has authority over their own grant.
  return { kind: 'needs-action', reason: r }
}
