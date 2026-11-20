/**
 * Sandbox terminal endpoint — Route Handler proxy.
 *
 * First-time sandbox cold-start (image pull / Pod or container boot + ttyd)
 * can exceed Next.js config rewrite's ~30s http-proxy timeout, causing
 * `socket hang up` and a proxy 500 while the backend is still provisioning.
 *
 * This dedicated route handler bypasses the 30s rewrite ceiling and waits up
 * to ~5 minutes (matching backend sandbox.create_timeout=300s and staying
 * under Node undici's default 300s headersTimeout). If it still times out,
 * it returns 503 so SWR can retry seamlessly instead of surfacing a 500/504.
 */
import { type NextRequest } from 'next/server'

import { proxySandboxEndpoint } from '@/lib/sandboxRouteProxy'

export const maxDuration = 300
export const dynamic = 'force-dynamic'

export const UPSTREAM_TIMEOUT_MS = 295_000

export async function GET(request: NextRequest, { params }: { params: Promise<{ wsId: string }> }) {
  const { wsId } = await params
  return proxySandboxEndpoint(request, {
    backendPath: `/api/v1/ws/${wsId}/sandbox/terminal`,
    timeoutMs: UPSTREAM_TIMEOUT_MS,
    timeoutStatus: 503,
    timeoutDetail: 'sandbox terminal is still starting; please retry',
    failureDetail: 'sandbox terminal proxy failed; please retry',
  })
}
