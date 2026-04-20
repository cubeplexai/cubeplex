import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { createApiClient } from '../../src/api/client'

describe('ApiClient', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn(async () => new Response('{}', { status: 200 }))
    globalThis.fetch = fetchMock as unknown as typeof fetch
    Object.defineProperty(document, 'cookie', {
      writable: true,
      value: 'cubebox_csrf=csrf-abc; other=x',
    })
  })

  afterEach(() => vi.restoreAllMocks())

  it('always sends credentials: include', async () => {
    const client = createApiClient('')
    await client.get('/api/v1/anything')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/anything',
      expect.objectContaining({ credentials: 'include' })
    )
  })

  it('injects /ws/<id>/ into scoped paths when workspaceId is set', async () => {
    const client = createApiClient('')
    client.setWorkspaceId('ws-123')
    await client.get('/api/v1/conversations')
    const [url] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/ws/ws-123/conversations')
  })

  it('does NOT rewrite /api/v1/auth/* paths', async () => {
    const client = createApiClient('')
    client.setWorkspaceId('ws-123')
    await client.post('/api/v1/auth/login', { a: 1 })
    const [url] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/auth/login')
  })

  it('does NOT rewrite /api/v1/workspaces paths', async () => {
    const client = createApiClient('')
    client.setWorkspaceId('ws-123')
    await client.get('/api/v1/workspaces')
    const [url] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/workspaces')
  })

  it('does NOT double-inject /ws/ if path already scoped', async () => {
    const client = createApiClient('')
    client.setWorkspaceId('ws-123')
    await client.get('/api/v1/ws/ws-123/conversations')
    const [url] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/ws/ws-123/conversations')
  })

  it('resolvePath mirrors the rewrite (for direct fetch callers)', () => {
    const client = createApiClient('')
    client.setWorkspaceId('ws-123')
    expect(client.resolvePath('/api/v1/conversations/abc/messages'))
      .toBe('/api/v1/ws/ws-123/conversations/abc/messages')
  })

  it('never sends X-Workspace-Id header (legacy behavior removed)', async () => {
    const client = createApiClient('')
    client.setWorkspaceId('ws-123')
    await client.get('/api/v1/conversations')
    const [, init] = fetchMock.mock.calls[0]
    expect((init as RequestInit).headers).not.toHaveProperty('X-Workspace-Id')
  })

  it('injects X-CSRF-Token on POST/PATCH/DELETE from cubebox_csrf cookie', async () => {
    const client = createApiClient('')
    await client.post('/api/v1/conversations', {})
    const [, init] = fetchMock.mock.calls[0]
    expect((init as RequestInit).headers).toMatchObject({ 'X-CSRF-Token': 'csrf-abc' })
  })

  it('does NOT inject X-CSRF-Token on GET', async () => {
    const client = createApiClient('')
    await client.get('/api/v1/conversations')
    const [, init] = fetchMock.mock.calls[0]
    expect((init as RequestInit).headers).not.toHaveProperty('X-CSRF-Token')
  })

  it('postForm sends form-urlencoded body', async () => {
    const client = createApiClient('')
    await client.postForm('/api/v1/auth/login', { username: 'a@b.c', password: 'pw' })
    const [, init] = fetchMock.mock.calls[0]
    expect((init as RequestInit).headers).toMatchObject({
      'Content-Type': 'application/x-www-form-urlencoded',
    })
    expect(String((init as RequestInit).body)).toContain('username=a%40b.c')
    expect(String((init as RequestInit).body)).toContain('password=pw')
  })

  it('fires onUnauthorized callback on 401', async () => {
    fetchMock.mockResolvedValueOnce(new Response('', { status: 401 }))
    const handler = vi.fn()
    const client = createApiClient('')
    client.onUnauthorized(handler)
    await client.get('/api/v1/anything')
    expect(handler).toHaveBeenCalledOnce()
  })

  it('does not fire onUnauthorized for /auth/login 400s', async () => {
    fetchMock.mockResolvedValueOnce(new Response('', { status: 400 }))
    const handler = vi.fn()
    const client = createApiClient('')
    client.onUnauthorized(handler)
    await client.postForm('/api/v1/auth/login', { username: 'x', password: 'y' })
    expect(handler).not.toHaveBeenCalled()
  })
})
