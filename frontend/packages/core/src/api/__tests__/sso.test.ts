import { describe, expect, it, vi } from 'vitest'
import {
  activateSsoConnection,
  createSsoConnection,
  deactivateSsoConnection,
  deleteSsoConnection,
  discoverOidcEndpoints,
  getGoogleAuthorizeUrl,
  getOrgInfo,
  getOrgSso,
  initiateSsoLogin,
  listSsoIdentities,
  unlinkSsoIdentity,
  updateSsoConnection,
} from '../sso'
import type { ApiClient } from '../client'

function mockClient(response: {
  ok: boolean
  status?: number
  body?: unknown
  text?: string
}): ApiClient {
  const res = {
    ok: response.ok,
    status: response.status ?? (response.ok ? 200 : 500),
    json: vi.fn().mockResolvedValue(response.body ?? {}),
    text: vi.fn().mockResolvedValue(response.text ?? JSON.stringify(response.body ?? {})),
    headers: new Headers({ 'content-type': 'application/json' }),
  }
  return {
    baseUrl: '',
    workspaceId: null,
    setWorkspaceId: vi.fn(),
    locale: null,
    setLocale: vi.fn(),
    resolvePath: vi.fn().mockImplementation((p: string) => p),
    get: vi.fn().mockResolvedValue(res),
    post: vi.fn().mockResolvedValue(res),
    postRaw: vi.fn().mockResolvedValue(res),
    postForm: vi.fn().mockResolvedValue(res),
    put: vi.fn().mockResolvedValue(res),
    patch: vi.fn().mockResolvedValue(res),
    del: vi.fn().mockResolvedValue(res),
    onUnauthorized: vi.fn().mockReturnValue(() => {}),
    notifyUnauthorized: vi.fn(),
  } as unknown as ApiClient
}

describe('SSO public API', () => {
  it('getOrgInfo encodes slug into the path', async () => {
    const client = mockClient({
      ok: true,
      body: { org_name: 'Acme', sso_enabled: true, sso_protocol: 'oidc' },
    })
    const out = await getOrgInfo(client, 'acme co')
    expect(client.get).toHaveBeenCalledWith('/api/v1/auth/org-info/acme%20co')
    expect(out.org_name).toBe('Acme')
    expect(out.sso_enabled).toBe(true)
  })

  it('initiateSsoLogin posts with null slug when omitted (single-tenant)', async () => {
    const client = mockClient({ ok: true, body: { redirect_url: 'https://idp/x' } })
    const out = await initiateSsoLogin(client)
    expect(client.post).toHaveBeenCalledWith('/api/v1/auth/sso/initiate', { org_slug: null })
    expect(out.redirect_url).toBe('https://idp/x')
  })

  it('initiateSsoLogin sends slug when provided', async () => {
    const client = mockClient({ ok: true, body: { redirect_url: 'https://idp/y' } })
    await initiateSsoLogin(client, 'acme')
    expect(client.post).toHaveBeenCalledWith('/api/v1/auth/sso/initiate', { org_slug: 'acme' })
  })

  it('getGoogleAuthorizeUrl hits the social route', async () => {
    const client = mockClient({ ok: true, body: { redirect_url: 'https://google/auth' } })
    const out = await getGoogleAuthorizeUrl(client)
    expect(client.get).toHaveBeenCalledWith('/api/v1/auth/social/google/authorize')
    expect(out.redirect_url).toBe('https://google/auth')
  })

  it('throws ApiError on non-ok response', async () => {
    const client = mockClient({ ok: false, status: 404, body: { detail: 'org_not_found' } })
    await expect(getOrgInfo(client, 'missing')).rejects.toThrow()
  })
})

describe('SSO admin API', () => {
  it('getOrgSso returns null when body is empty', async () => {
    const client = mockClient({ ok: true, text: '' })
    const out = await getOrgSso(client)
    expect(client.get).toHaveBeenCalledWith('/api/v1/admin/sso')
    expect(out).toBeNull()
  })

  it('getOrgSso parses the connection when present', async () => {
    const conn = {
      id: 'sso_1',
      org_id: 'org_1',
      protocol: 'oidc',
      display_name: 'My IdP',
      status: 'testing',
      provisioning: 'auto',
      config: {},
      created_at: '2026-06-17T00:00:00+00:00',
      updated_at: '2026-06-17T00:00:00+00:00',
    }
    const client = mockClient({ ok: true, text: JSON.stringify(conn) })
    const out = await getOrgSso(client)
    expect(out?.id).toBe('sso_1')
    expect(out?.protocol).toBe('oidc')
  })

  it('createSsoConnection posts the body', async () => {
    const client = mockClient({ ok: true, body: { id: 'sso_1' } })
    await createSsoConnection(client, {
      protocol: 'oidc',
      display_name: 'IdP',
      provisioning: 'auto',
      config: { issuer_url: 'https://idp' },
      client_secret: 'shh',
    })
    expect(client.post).toHaveBeenCalledWith(
      '/api/v1/admin/sso',
      expect.objectContaining({ protocol: 'oidc', display_name: 'IdP', client_secret: 'shh' }),
    )
  })

  it('updateSsoConnection PUTs to the right path', async () => {
    const client = mockClient({ ok: true, body: { id: 'sso_1' } })
    await updateSsoConnection(client, 'sso_1', { display_name: 'Renamed' })
    expect(client.put).toHaveBeenCalledWith(
      '/api/v1/admin/sso/sso_1',
      expect.objectContaining({ display_name: 'Renamed' }),
    )
  })

  it('deleteSsoConnection DELETEs the connection', async () => {
    const client = mockClient({ ok: true, status: 204 })
    await deleteSsoConnection(client, 'sso_1')
    expect(client.del).toHaveBeenCalledWith('/api/v1/admin/sso/sso_1')
  })

  it('activateSsoConnection posts to /activate with empty body', async () => {
    const client = mockClient({ ok: true, body: { id: 'sso_1', status: 'active' } })
    await activateSsoConnection(client, 'sso_1')
    expect(client.post).toHaveBeenCalledWith('/api/v1/admin/sso/sso_1/activate', {})
  })

  it('deactivateSsoConnection posts to /deactivate', async () => {
    const client = mockClient({ ok: true, body: { id: 'sso_1', status: 'inactive' } })
    await deactivateSsoConnection(client, 'sso_1')
    expect(client.post).toHaveBeenCalledWith('/api/v1/admin/sso/sso_1/deactivate', {})
  })

  it('listSsoIdentities GETs without query when no params', async () => {
    const client = mockClient({ ok: true, body: [] })
    await listSsoIdentities(client, 'sso_1')
    expect(client.get).toHaveBeenCalledWith('/api/v1/admin/sso/sso_1/identities')
  })

  it('listSsoIdentities appends pagination params', async () => {
    const client = mockClient({ ok: true, body: [] })
    await listSsoIdentities(client, 'sso_1', { limit: 25, offset: 50 })
    expect(client.get).toHaveBeenCalledWith('/api/v1/admin/sso/sso_1/identities?limit=25&offset=50')
  })

  it('unlinkSsoIdentity DELETEs the identity', async () => {
    const client = mockClient({ ok: true, status: 204 })
    await unlinkSsoIdentity(client, 'sso_1', 'eid_42')
    expect(client.del).toHaveBeenCalledWith('/api/v1/admin/sso/sso_1/identities/eid_42')
  })

  it('discoverOidcEndpoints posts the issuer_url', async () => {
    const client = mockClient({
      ok: true,
      body: {
        issuer: 'https://idp',
        authorization_endpoint: 'https://idp/auth',
        token_endpoint: 'https://idp/token',
        userinfo_endpoint: null,
      },
    })
    const out = await discoverOidcEndpoints(client, 'https://idp')
    expect(client.post).toHaveBeenCalledWith('/api/v1/admin/sso/discover-oidc', {
      issuer_url: 'https://idp',
    })
    expect(out.token_endpoint).toBe('https://idp/token')
  })
})
