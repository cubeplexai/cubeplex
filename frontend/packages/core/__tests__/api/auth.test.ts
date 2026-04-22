import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { createApiClient } from '../../src/api/client'
import { registerUser, loginUser, logoutUser, getMe } from '../../src/api/auth'

describe('auth API', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    globalThis.fetch = fetchMock as unknown as typeof fetch
  })
  afterEach(() => vi.restoreAllMocks())

  it('registerUser POSTs JSON and returns id+email+default_workspace_id', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ id: 'u1', email: 'a@b.c', default_workspace_id: 'ws-1' }), {
        status: 201,
      }),
    )
    const client = createApiClient('')
    const result = await registerUser(client, 'a@b.c', 'pw')
    expect(result).toEqual({ id: 'u1', email: 'a@b.c', default_workspace_id: 'ws-1' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/auth/register')
    expect((init as RequestInit).method).toBe('POST')
  })

  it('loginUser POSTs form-urlencoded', async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
    const client = createApiClient('')
    await loginUser(client, 'a@b.c', 'pw')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/auth/login')
    expect((init as RequestInit).headers).toMatchObject({
      'Content-Type': 'application/x-www-form-urlencoded',
    })
  })

  it('loginUser throws on 400', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'LOGIN_BAD_CREDENTIALS' }), {
        status: 400,
        headers: { 'content-type': 'application/json' },
      }),
    )
    const client = createApiClient('')
    await expect(loginUser(client, 'a@b.c', 'pw')).rejects.toThrow('LOGIN_BAD_CREDENTIALS')
  })

  it('logoutUser POSTs with no body', async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
    const client = createApiClient('')
    await logoutUser(client)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/auth/logout')
    expect((init as RequestInit).method).toBe('POST')
  })

  it('getMe returns { id, email } on 200', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ id: 'u1', email: 'a@b.c' }), { status: 200 }),
    )
    const client = createApiClient('')
    const me = await getMe(client)
    expect(me).toEqual({ id: 'u1', email: 'a@b.c' })
  })

  it('getMe returns null on 401', async () => {
    fetchMock.mockResolvedValueOnce(new Response('', { status: 401 }))
    const client = createApiClient('')
    const me = await getMe(client)
    expect(me).toBeNull()
  })
})
