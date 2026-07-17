import { GET, POST } from '../../app/api/v1/ws/[wsId]/conversations/[id]/messages/route'

describe('conversation messages route proxy', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('forwards identity headers and wsId in the URL for POST run-start requests', async () => {
    const backendFetch = vi.fn(async () => new Response('ok', { status: 200 }))
    vi.stubGlobal('fetch', backendFetch)

    const request = {
      headers: new Headers([
        ['cookie', 'cubeplex_user_id=user-cookie'],
        ['x-user-id', 'header-user'],
      ]),
      text: async () => JSON.stringify({ content: 'hello' }),
    } as any

    await POST(request, {
      params: Promise.resolve({ wsId: 'ws-42', id: 'conv-1' }),
    })

    expect(backendFetch).toHaveBeenCalledTimes(1)
    const [url, init] = backendFetch.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/api/v1/ws/ws-42/conversations/conv-1/messages')
    expect(init.headers).toMatchObject({
      'Content-Type': 'application/json',
      Accept: 'application/json',
      cookie: 'cubeplex_user_id=user-cookie',
      'x-user-id': 'header-user',
    })
    expect(init.headers).not.toHaveProperty('X-Workspace-Id')
  })

  it('passes backend set-cookie through the JSON response', async () => {
    const backendFetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ run_id: 'run-1' }), {
          status: 200,
          headers: {
            'content-type': 'application/json',
            'set-cookie': 'cubeplex_user_id=user-cookie; Path=/; HttpOnly',
          },
        }),
    )
    vi.stubGlobal('fetch', backendFetch)

    const request = {
      headers: new Headers(),
      text: async () => JSON.stringify({ content: 'hello' }),
    } as any

    const response = await POST(request, {
      params: Promise.resolve({ wsId: 'ws-42', id: 'conv-1' }),
    })

    expect(response.headers.get('set-cookie')).toContain('cubeplex_user_id=user-cookie')
  })

  it('forwards identity headers and wsId in the URL on GET requests too', async () => {
    const backendFetch = vi.fn(async () => Response.json({ messages: [] }))
    vi.stubGlobal('fetch', backendFetch)

    const request = {
      url: 'http://localhost/api/v1/ws/ws-42/conversations/conv-1/messages?limit=10',
      headers: new Headers([
        ['cookie', 'cubeplex_user_id=user-cookie'],
        ['x-user-id', 'header-user'],
      ]),
    } as any

    await GET(request, {
      params: Promise.resolve({ wsId: 'ws-42', id: 'conv-1' }),
    })

    expect(backendFetch).toHaveBeenCalledTimes(1)
    const [url, init] = backendFetch.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/api/v1/ws/ws-42/conversations/conv-1/messages?limit=10')
    expect(init?.headers).toMatchObject({
      cookie: 'cubeplex_user_id=user-cookie',
      'x-user-id': 'header-user',
    })
  })
})
