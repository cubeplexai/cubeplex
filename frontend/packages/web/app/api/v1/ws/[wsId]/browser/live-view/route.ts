/**
 * Browser live-view endpoint — Route Handler proxy.
 *
 * Cold-start of the sandbox Neko stack can take 30–120s (start-browser.sh).
 * Next.js config rewrites use a hardcoded ~30s http-proxy timeout, which
 * aborts the upstream connection (`socket hang up` / ECONNRESET) while the
 * backend is still working and eventually returns 200. The browser then
 * sees a proxy 500 even though the backend succeeded.
 *
 * A dedicated route handler takes precedence over the rewrite fallback
 * (same pattern as the SSE messages proxy) and has no 30s proxy ceiling.
 * See: https://github.com/cubeplexai/cubeplex/issues/422
 */
import { type NextRequest, NextResponse } from 'next/server'

const BACKEND_URL = process.env.CUBEPLEX_API_URL ?? 'http://localhost:8000'

// Backend start_browser allows up to 120s for the shell script; leave headroom
// for get_or_create + endpoint mint. Hosted platforms that honor maxDuration
// will keep the function alive; local Next dev has no such cap on route handlers.
export const maxDuration = 180
export const dynamic = 'force-dynamic'

// Bound the upstream wait below maxDuration so a hung backend still returns a
// client-visible JSON error (and SWR can retry) instead of spinning forever.
// 165s = start_browser 120s + get_or_create/endpoint slack, under the 180s cap.
export const UPSTREAM_TIMEOUT_MS = 165_000

function buildProxyHeaders(request: NextRequest): HeadersInit {
  const headers: Record<string, string> = { Accept: 'application/json' }
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

function isTimeoutError(err: unknown): boolean {
  // AbortSignal.timeout → TimeoutError (DOMException); AbortController → AbortError.
  // Do not require `instanceof Error` — DOMException identity can differ across
  // realms / test environments while still exposing `.name`.
  if (err == null || typeof err !== 'object') return false
  const name = (err as { name?: string }).name
  return name === 'TimeoutError' || name === 'AbortError'
}

export async function GET(request: NextRequest, { params }: { params: Promise<{ wsId: string }> }) {
  const { wsId } = await params
  const qs = new URL(request.url).search
  const url = `${BACKEND_URL}/api/v1/ws/${wsId}/browser/live-view${qs}`

  let backendRes: Response
  try {
    backendRes = await fetch(url, {
      headers: buildProxyHeaders(request),
      // Never cache a signed live-view URL.
      cache: 'no-store',
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    })
  } catch (err) {
    if (isTimeoutError(err)) {
      return NextResponse.json(
        {
          detail: 'sandbox browser live-view timed out waiting for backend; please retry',
        },
        { status: 504 },
      )
    }
    return NextResponse.json(
      { detail: 'sandbox browser live-view proxy failed; please retry' },
      { status: 502 },
    )
  }

  const contentType = backendRes.headers.get('content-type') ?? 'application/json'
  const body = await backendRes.text()
  const headers = new Headers({ 'Content-Type': contentType })
  appendSetCookie(headers, backendRes.headers)
  return new NextResponse(body, {
    status: backendRes.status,
    headers,
  })
}
