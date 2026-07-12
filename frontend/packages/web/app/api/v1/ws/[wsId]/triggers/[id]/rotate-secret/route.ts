import { type NextRequest } from 'next/server'

const BACKEND_URL = process.env.CUBEPLEX_API_URL ?? 'http://localhost:8000'

function buildProxyHeaders(request: NextRequest, accept: string): HeadersInit {
  const headers: Record<string, string> = { Accept: accept }
  const cookie = request.headers.get('cookie')
  const userId = request.headers.get('x-user-id')
  const csrf = request.headers.get('x-csrf-token')

  if (cookie) {
    headers.cookie = cookie
  }
  if (userId) {
    headers['x-user-id'] = userId
  }
  if (csrf) {
    headers['X-CSRF-Token'] = csrf
  }

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

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ wsId: string; id: string }> },
) {
  const { wsId, id } = await params
  const body = await request.text()

  const backendRes = await fetch(`${BACKEND_URL}/api/v1/ws/${wsId}/triggers/${id}/rotate-secret`, {
    method: 'POST',
    headers: {
      ...buildProxyHeaders(request, 'application/json'),
      'Content-Type': 'application/json',
    },
    body,
  })

  const headers = new Headers({
    'Content-Type': backendRes.headers.get('content-type') ?? 'application/json',
  })
  appendSetCookie(headers, backendRes.headers)
  return new Response(await backendRes.text(), {
    status: backendRes.status,
    headers,
  })
}
