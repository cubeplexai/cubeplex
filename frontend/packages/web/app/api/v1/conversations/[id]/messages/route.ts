/**
 * Conversations messages endpoint — Route Handler proxy.
 *
 * POST: SSE streaming proxy that bypasses Next.js rewrite proxy (which uses
 *   http-proxy with 30s default timeout and response buffering). Pipes the
 *   backend SSE stream through the Web Streams API — no timeout, no buffering.
 *
 * GET: Plain JSON proxy for message history (list messages).
 */
import { type NextRequest, NextResponse } from 'next/server'

const BACKEND_URL = process.env.CUBEBOX_API_URL ?? 'http://localhost:8000'

function buildProxyHeaders(request: NextRequest, accept: string): HeadersInit {
  const headers: Record<string, string> = { Accept: accept }
  const cookie = request.headers.get('cookie')
  const userId = request.headers.get('x-user-id')

  if (cookie) {
    headers.cookie = cookie
  }
  if (userId) {
    headers['x-user-id'] = userId
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
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params
  const body = await request.text()

  const backendRes = await fetch(
    `${BACKEND_URL}/api/v1/conversations/${id}/messages`,
    {
      method: 'POST',
      headers: {
        ...buildProxyHeaders(request, 'text/event-stream'),
        'Content-Type': 'application/json',
      },
      body,
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

  // Pipe the backend SSE stream straight through — no buffering
  const headers = new Headers({
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache, no-transform',
    'Connection': 'keep-alive',
    'X-Accel-Buffering': 'no',
  })
  appendSetCookie(headers, backendRes.headers)
  return new Response(backendRes.body, {
    status: backendRes.status,
    headers,
  })
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params
  const url = new URL(request.url)
  const qs = url.search

  const backendRes = await fetch(
    `${BACKEND_URL}/api/v1/conversations/${id}/messages${qs}`,
    {
      headers: buildProxyHeaders(request, 'application/json'),
    },
  )

  const data = await backendRes.json()
  const response = NextResponse.json(data, { status: backendRes.status })
  appendSetCookie(response.headers, backendRes.headers)
  return response
}
