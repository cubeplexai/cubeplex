import { GET, POST } from '../../app/api/v1/conversations/[id]/messages/route'

describe('conversation messages route proxy', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('forwards identity headers to the backend for streaming POST requests', async () => {
    const backendFetch = vi.fn(async () => new Response('ok', { status: 200 }))
    vi.stubGlobal('fetch', backendFetch)

    const request = {
      headers: new Headers([
        ['cookie', 'cubebox_user_id=user-cookie'],
        ['x-user-id', 'header-user'],
      ]),
      text: async () => JSON.stringify({ content: 'hello' }),
    } as any

    await POST(request, { params: Promise.resolve({ id: 'conv-1' }) })

    expect(backendFetch).toHaveBeenCalledTimes(1)
    const [url, init] = backendFetch.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/api/v1/conversations/conv-1/messages')
    expect(init.headers).toMatchObject({
      'Content-Type': 'application/json',
      'Accept': 'text/event-stream',
      'cookie': 'cubebox_user_id=user-cookie',
      'x-user-id': 'header-user',
    })
  })

  it('passes backend set-cookie through the streaming response', async () => {
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('data: {"type":"done"}\n\n'))
        controller.close()
      },
    })
    const backendFetch = vi.fn(async () => new Response(stream, {
      status: 200,
      headers: {
        'content-type': 'text/event-stream',
        'set-cookie': 'cubebox_user_id=user-cookie; Path=/; HttpOnly',
      },
    }))
    vi.stubGlobal('fetch', backendFetch)

    const request = {
      headers: new Headers(),
      text: async () => JSON.stringify({ content: 'hello' }),
    } as any

    const response = await POST(request, { params: Promise.resolve({ id: 'conv-1' }) })

    expect(response.headers.get('set-cookie')).toContain('cubebox_user_id=user-cookie')
  })

  it('forwards identity headers on GET requests too', async () => {
    const backendFetch = vi.fn(async () => Response.json({ messages: [] }))
    vi.stubGlobal('fetch', backendFetch)

    const request = {
      url: 'http://localhost/api/v1/conversations/conv-1/messages?limit=10',
      headers: new Headers([
        ['cookie', 'cubebox_user_id=user-cookie'],
        ['x-user-id', 'header-user'],
      ]),
    } as any

    await GET(request, { params: Promise.resolve({ id: 'conv-1' }) })

    expect(backendFetch).toHaveBeenCalledTimes(1)
    const [url, init] = backendFetch.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/api/v1/conversations/conv-1/messages?limit=10')
    expect(init?.headers).toMatchObject({
      'cookie': 'cubebox_user_id=user-cookie',
      'x-user-id': 'header-user',
    })
  })
})
