/**
 * Skill refresh endpoint — POST proxy.
 * Triggers a re-fetch of a remote-imported skill's source to check for updates.
 */
import { type NextRequest, NextResponse } from 'next/server'

const BACKEND_URL = process.env.CUBEPLEX_API_URL ?? 'http://localhost:8000'

function buildProxyHeaders(request: NextRequest): HeadersInit {
  const headers: Record<string, string> = {
    Accept: 'application/json',
    'Content-Type': 'application/json',
  }
  const cookie = request.headers.get('cookie')
  const userId = request.headers.get('x-user-id')
  const csrf = request.headers.get('x-csrf-token')
  if (cookie) headers.cookie = cookie
  if (userId) headers['x-user-id'] = userId
  if (csrf) headers['X-CSRF-Token'] = csrf
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
  if (setCookie) target.append('set-cookie', setCookie)
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ wsId: string; skillId: string }> },
) {
  const { wsId, skillId } = await params

  const backendRes = await fetch(`${BACKEND_URL}/api/v1/ws/${wsId}/skills/${skillId}/refresh`, {
    method: 'POST',
    headers: buildProxyHeaders(request),
    body: '{}',
  })

  const data = await backendRes.json()
  const response = NextResponse.json(data, { status: backendRes.status })
  appendSetCookie(response.headers, backendRes.headers)
  return response
}
