import { type NextRequest } from 'next/server'

const BACKEND_URL = process.env.CUBEPLEX_API_URL ?? 'http://localhost:8000'

function buildProxyHeaders(request: NextRequest): HeadersInit {
  const headers: Record<string, string> = { Accept: 'text/event-stream' }
  const cookie = request.headers.get('cookie')
  const userId = request.headers.get('x-user-id')
  const csrf = request.headers.get('x-csrf-token')
  const lastEventId = request.headers.get('last-event-id')

  if (cookie) headers.cookie = cookie
  if (userId) headers['x-user-id'] = userId
  if (csrf) headers['X-CSRF-Token'] = csrf
  if (lastEventId) headers['Last-Event-ID'] = lastEventId

  return headers
}

function appendSetCookie(target: Headers, source: Headers): void {
  const getSetCookie = (source as Headers & { getSetCookie?: () => string[] }).getSetCookie
  if (typeof getSetCookie === 'function') {
    for (const value of getSetCookie.call(source)) {
      target.append('set-cookie', value)
    }
    return
  }

  const setCookie = source.get('set-cookie')
  if (setCookie) {
    target.append('set-cookie', setCookie)
  }
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ wsId: string; id: string; runId: string }> },
) {
  const { wsId, id, runId } = await params

  const backendRes = await fetch(
    `${BACKEND_URL}/api/v1/ws/${wsId}/conversations/${id}/runs/${runId}/stream`,
    {
      headers: buildProxyHeaders(request),
    },
  )

  if (!backendRes.ok || !backendRes.body) {
    const headers = new Headers({ 'Content-Type': 'application/json' })
    appendSetCookie(headers, backendRes.headers)
    return new Response(await backendRes.text(), {
      status: backendRes.status,
      headers,
    })
  }

  const headers = new Headers({
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache, no-transform',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
  })
  appendSetCookie(headers, backendRes.headers)
  return new Response(backendRes.body, {
    status: backendRes.status,
    headers,
  })
}
