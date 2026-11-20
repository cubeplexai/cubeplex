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
import { type NextRequest } from 'next/server'

import { proxySandboxEndpoint } from '@/lib/sandboxRouteProxy'

// Backend start_browser allows up to 120s for the shell script; leave headroom
// for get_or_create + endpoint mint. Hosted platforms that honor maxDuration
// will keep the function alive; local Next dev has no such cap on route handlers.
export const maxDuration = 180
export const dynamic = 'force-dynamic'

// Bound the upstream wait below maxDuration so a hung backend still returns a
// client-visible JSON error (and SWR can retry) instead of spinning forever.
// 165s = start_browser 120s + get_or_create/endpoint slack, under the 180s cap.
export const UPSTREAM_TIMEOUT_MS = 165_000

export async function GET(request: NextRequest, { params }: { params: Promise<{ wsId: string }> }) {
  const { wsId } = await params
  return proxySandboxEndpoint(request, {
    backendPath: `/api/v1/ws/${encodeURIComponent(wsId)}/browser/live-view`,
    timeoutMs: UPSTREAM_TIMEOUT_MS,
    timeoutStatus: 504,
    timeoutDetail: 'sandbox browser live-view timed out waiting for backend; please retry',
    failureDetail: 'sandbox browser live-view proxy failed; please retry',
  })
}
