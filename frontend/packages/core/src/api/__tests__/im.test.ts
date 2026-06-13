import { describe, expect, it, vi } from 'vitest'
import { adminListImAccounts, wsConnectImAccount, wsListImAccounts } from '../im'
import type { ApiClient } from '../client'

function mockClient(response: { ok: boolean; status?: number; body?: unknown }): ApiClient {
  const res = {
    ok: response.ok,
    status: response.status ?? (response.ok ? 200 : 500),
    json: vi.fn().mockResolvedValue(response.body ?? {}),
    text: vi.fn().mockResolvedValue(''),
    headers: new Headers(),
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

describe('IM SDK', () => {
  it('wsListImAccounts hits the correct path', async () => {
    const client = mockClient({ ok: true, body: { accounts: [] } })
    const out = await wsListImAccounts(client, 'ws-1')
    expect(client.get).toHaveBeenCalledWith('/api/v1/ws/ws-1/im/accounts')
    expect(out.accounts).toEqual([])
  })

  it('adminListImAccounts hits the admin path', async () => {
    const client = mockClient({ ok: true, body: { accounts: [] } })
    await adminListImAccounts(client)
    expect(client.get).toHaveBeenCalledWith('/api/v1/admin/im/accounts')
  })

  it('wsConnectImAccount posts the payload', async () => {
    const client = mockClient({ ok: true, body: { id: 'imac-1' } })
    await wsConnectImAccount(client, 'ws-1', {
      platform: 'feishu',
      app_id: 'cli_x',
      app_secret: 's',
    })
    expect(client.post).toHaveBeenCalledWith(
      '/api/v1/ws/ws-1/im/accounts',
      expect.objectContaining({ platform: 'feishu', app_id: 'cli_x' }),
    )
  })

  it('throws when the response is not ok', async () => {
    const client = mockClient({ ok: false, status: 409, body: { detail: 'dup' } })
    await expect(
      wsConnectImAccount(client, 'ws-1', {
        platform: 'feishu',
        app_id: 'x',
        app_secret: 'y',
      }),
    ).rejects.toThrow()
  })
})
