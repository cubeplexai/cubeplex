import { GET, UPSTREAM_TIMEOUT_MS } from '../../app/api/v1/ws/[wsId]/browser/live-view/route'

describe('browser live-view route proxy', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('forwards identity headers, wsId, and conversation_id query', async () => {
    const backendFetch = vi.fn(async () =>
      Response.json({ url: 'https://neko.example/view?token=abc' }),
    )
    vi.stubGlobal('fetch', backendFetch)

    const request = {
      url: 'http://localhost/api/v1/ws/ws-42/browser/live-view?conversation_id=conv-9',
      headers: new Headers([
        ['cookie', 'cubeplex_auth=cookie-val'],
        ['x-user-id', 'header-user'],
        ['x-csrf-token', 'csrf-token'],
      ]),
    } as any

    const response = await GET(request, {
      params: Promise.resolve({ wsId: 'ws-42' }),
    })

    expect(backendFetch).toHaveBeenCalledTimes(1)
    const [url, init] = backendFetch.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/api/v1/ws/ws-42/browser/live-view?conversation_id=conv-9')
    expect(init?.headers).toMatchObject({
      Accept: 'application/json',
      cookie: 'cubeplex_auth=cookie-val',
      'x-user-id': 'header-user',
      'X-CSRF-Token': 'csrf-token',
    })
    expect(init).toMatchObject({ cache: 'no-store' })
    expect(init?.signal).toBeInstanceOf(AbortSignal)
    // Bound must sit under maxDuration (180s) and above start_browser (120s).
    expect(UPSTREAM_TIMEOUT_MS).toBeGreaterThan(120_000)
    expect(UPSTREAM_TIMEOUT_MS).toBeLessThan(180_000)

    expect(response.status).toBe(200)
    await expect(response.json()).resolves.toEqual({
      url: 'https://neko.example/view?token=abc',
    })
  })

  it('passes through backend status and set-cookie', async () => {
    const backendFetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: 'sandbox is starting up' }), {
          status: 503,
          headers: {
            'content-type': 'application/json',
            'set-cookie': 'cubeplex_auth=refreshed; Path=/; HttpOnly',
          },
        }),
    )
    vi.stubGlobal('fetch', backendFetch)

    const request = {
      url: 'http://localhost/api/v1/ws/ws-42/browser/live-view',
      headers: new Headers(),
    } as any

    const response = await GET(request, {
      params: Promise.resolve({ wsId: 'ws-42' }),
    })

    expect(response.status).toBe(503)
    expect(response.headers.get('set-cookie')).toContain('cubeplex_auth=refreshed')
    await expect(response.json()).resolves.toEqual({ detail: 'sandbox is starting up' })
  })

  it('returns 504 JSON when the upstream wait times out', async () => {
    const timeoutErr = new DOMException('The operation was aborted due to timeout', 'TimeoutError')
    const backendFetch = vi.fn(async () => {
      throw timeoutErr
    })
    vi.stubGlobal('fetch', backendFetch)

    const request = {
      url: 'http://localhost/api/v1/ws/ws-42/browser/live-view',
      headers: new Headers(),
    } as any

    const response = await GET(request, {
      params: Promise.resolve({ wsId: 'ws-42' }),
    })

    expect(response.status).toBe(504)
    await expect(response.json()).resolves.toMatchObject({
      detail: expect.stringContaining('timed out'),
    })
  })

  it('returns 502 JSON when the upstream fetch fails (network)', async () => {
    const backendFetch = vi.fn(async () => {
      throw new TypeError('fetch failed')
    })
    vi.stubGlobal('fetch', backendFetch)

    const request = {
      url: 'http://localhost/api/v1/ws/ws-42/browser/live-view',
      headers: new Headers(),
    } as any

    const response = await GET(request, {
      params: Promise.resolve({ wsId: 'ws-42' }),
    })

    expect(response.status).toBe(502)
    await expect(response.json()).resolves.toMatchObject({
      detail: expect.stringContaining('proxy failed'),
    })
  })
})
