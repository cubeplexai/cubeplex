import { describe, expect, it } from 'vitest'
import { computeAuthBandState } from './effectiveAuthState'

describe('computeAuthBandState', () => {
  it('returns ready when usable and credential_source set', () => {
    const s = computeAuthBandState({
      connector: {
        usable: true,
        credential_availability: 'available',
        credential_source: 'org',
        reason: 'usable',
        required_grant_scope: 'org',
        install: { auth_method: 'oauth', auth_status: 'authorized' },
      } as any,
      callerRole: 'admin',
      isOrgAdmin: true,
    })
    expect(s.kind).toBe('ready')
  })

  it('returns ready (no-credential) when auth_method=none', () => {
    const s = computeAuthBandState({
      connector: {
        usable: true,
        credential_availability: 'not_required',
        credential_source: null,
        reason: 'usable',
        install: { auth_method: 'none', auth_status: 'not_required' },
      } as any,
      callerRole: 'member',
      isOrgAdmin: false,
    })
    expect(s.kind).toBe('ready')
    if (s.kind === 'ready') expect(s.subkind).toBe('no_credential')
  })

  it('returns needs-action for user_needs_connection on user-policy install', () => {
    const s = computeAuthBandState({
      connector: {
        usable: false,
        credential_availability: 'missing',
        reason: 'user_needs_connection',
        required_grant_scope: 'user',
        install: { auth_method: 'oauth', auth_status: 'pending' },
      } as any,
      callerRole: 'member',
      isOrgAdmin: false,
    })
    expect(s.kind).toBe('needs-action')
  })

  it('returns awaiting-others for missing_org_grant when caller is not org admin', () => {
    const s = computeAuthBandState({
      connector: {
        usable: false,
        reason: 'missing_org_grant',
        required_grant_scope: 'org',
        install: { auth_method: 'oauth', auth_status: 'pending' },
      } as any,
      callerRole: 'member',
      isOrgAdmin: false,
    })
    expect(s.kind).toBe('awaiting-others')
    if (s.kind === 'awaiting-others') expect(s.who).toBe('org_admin')
  })

  it('returns needs-action for pending_oauth on org install when caller is org admin', () => {
    const s = computeAuthBandState({
      connector: {
        usable: false,
        reason: 'pending_oauth',
        required_grant_scope: 'org',
        install: { auth_method: 'oauth', auth_status: 'pending' },
      } as any,
      callerRole: 'admin',
      isOrgAdmin: true,
    })
    expect(s.kind).toBe('needs-action')
  })

  it('returns hidden for non-auth reasons (discovery_failed)', () => {
    const s = computeAuthBandState({
      connector: {
        usable: false,
        reason: 'discovery_failed',
        install: { auth_method: 'oauth', auth_status: 'authorized' },
      } as any,
      callerRole: 'admin',
      isOrgAdmin: false,
    })
    expect(s.kind).toBe('hidden')
  })
})
