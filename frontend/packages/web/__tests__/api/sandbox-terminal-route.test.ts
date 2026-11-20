import {
  GET,
  maxDuration,
  UPSTREAM_TIMEOUT_MS,
} from '../../app/api/v1/ws/[wsId]/sandbox/terminal/route'

describe('sandbox terminal route proxy', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('forwards identity headers, wsId, and conversation_id query', async () => {
    const backendFetch = vi.fn(async () =>
      Response.json({ url: 'https://panel.example/sandbox-panel/tok/' }),
    )
    vi.stubGlobal('fetch', backendFetch)

    const request = {
      url: 'http://localhost/api/v1/ws/ws-42/sandbox/terminal?conversation_id=conv-9',
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
    expect(url).toContain('/api/v1/ws/ws-42/sandbox/terminal?conversation_id=conv-9')
    expect(init?.headers).toMatchObject({
      Accept: 'application/json',
      cookie: 'cubeplex_auth=cookie-val',
      'x-user-id': 'header-user',
      'X-CSRF-Token': 'csrf-token',
    })
    expect(init).toMatchObject({ cache: 'no-store' })
    expect(init?.signal).toBeInstanceOf(AbortSignal)
    expect(UPSTREAM_TIMEOUT_MS).toBeGreaterThanOrEqual(240_000)
    expect(UPSTREAM_TIMEOUT_MS).toBeLessThanOrEqual(maxDuration * 1000)

    expect(response.status).toBe(200)
    await expect(response.json()).resolves.toEqual({
      url: 'https://panel.example/sandbox-panel/tok/',
    })
  })

  it('returns 503 instead of 500/504 when upstream times out', async () => {
    const timeoutErr = new DOMException('The operation was aborted due to timeout', 'TimeoutError')
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw timeoutErr
      }),
    )

    const request = {
      url: 'http://localhost/api/v1/ws/ws-42/sandbox/terminal',
      headers: new Headers(),
    } as any

    const response = await GET(request, {
      params: Promise.resolve({ wsId: 'ws-42' }),
    })

    expect(response.status).toBe(503)
    await expect(response.json()).resolves.toMatchObject({
      detail: expect.stringContaining('starting'),
    })
  })

  it('returns 502 when upstream is unreachable', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('fetch failed')
      }),
    )

    const request = {
      url: 'http://localhost/api/v1/ws/ws-42/sandbox/terminal',
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

  it('encodes wsId in backend path', async () => {
    const backendFetch = vi.fn(async () => Response.json({ url: 'https://panel.example/tok/' }))
    vi.stubGlobal('fetch', backendFetch)

    const request = {
      url: 'http://localhost/api/v1/ws/ws%2F..%2Fadmin/sandbox/terminal',
      headers: new Headers(),
    } as any

    await GET(request, {
      params: Promise.resolve({ wsId: 'ws/../admin' }),
    })

    const [url] = backendFetch.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/api/v1/ws/ws%2F..%2Fadmin/sandbox/terminal')
  })
})
