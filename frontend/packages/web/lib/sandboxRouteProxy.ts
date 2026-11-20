import { type NextRequest, NextResponse } from 'next/server'

const BACKEND_URL = process.env.CUBEPLEX_API_URL ?? 'http://localhost:8000'

export interface SandboxProxyOptions {
  backendPath: string
  timeoutMs: number
  timeoutStatus: number
  timeoutDetail: string
  failureDetail: string
}

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
  // AbortSignal.timeout -> TimeoutError (DOMException); AbortController -> AbortError.
  // Do not require `instanceof Error` — DOMException identity can differ across
  // realms / test environments while still exposing `.name`.
  if (err == null || typeof err !== 'object') return false
  const name = (err as { name?: string }).name
  return name === 'TimeoutError' || name === 'AbortError'
}

export async function proxySandboxEndpoint(
  request: NextRequest,
  options: SandboxProxyOptions,
): Promise<NextResponse> {
  const qs = new URL(request.url).search
  const url = `${BACKEND_URL}${options.backendPath}${qs}`

  const timeoutSignal = AbortSignal.timeout(options.timeoutMs)
  const signal =
    request.signal && typeof AbortSignal.any === 'function'
      ? AbortSignal.any([request.signal, timeoutSignal])
      : timeoutSignal

  try {
    // Guard both the upstream wait *and* body consumption: headers can arrive
    // while the body stalls; AbortSignal.timeout aborts the body stream too.
    const backendRes = await fetch(url, {
      headers: buildProxyHeaders(request),
      cache: 'no-store',
      signal,
    })
    const contentType = backendRes.headers.get('content-type') ?? 'application/json'
    const body = await backendRes.text()
    const headers = new Headers({ 'Content-Type': contentType })
    appendSetCookie(headers, backendRes.headers)
    return new NextResponse(body, {
      status: backendRes.status,
      headers,
    })
  } catch (err) {
    if (isTimeoutError(err)) {
      return NextResponse.json({ detail: options.timeoutDetail }, { status: options.timeoutStatus })
    }
    return NextResponse.json({ detail: options.failureDetail }, { status: 502 })
  }
}
