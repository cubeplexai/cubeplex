'use client'

import useSWR from 'swr'

interface BrowserLiveView {
  url: string
}

async function fetcher(url: string): Promise<BrowserLiveView> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`browser live-view fetch failed: ${res.status}`)
  return res.json() as Promise<BrowserLiveView>
}

/** Retry transient cold-start / proxy failures; skip auth and not-found. */
function shouldRetryOnError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err ?? '')
  // 401/403/404 are terminal for this endpoint.
  if (/browser live-view fetch failed: 40[134]\b/.test(msg)) return false
  return true
}

/**
 * Fetches the embeddable live-view URL for the workspace's sandbox browser.
 * The backend ensures the Neko stack is running before returning the URL.
 *
 * Pass ``conversationId`` so dedicated-mode topic / standalone group-chat
 * conversations resolve to the shared sandbox's browser instead of the
 * viewer's personal one.
 *
 * Cold start can take tens of seconds; the request is served by a dedicated
 * Next route handler (not the 30s rewrite proxy) so it can wait for the
 * backend. We still retry transient 5xx/network errors a few times.
 */
export function useBrowserLiveView(
  workspaceId: string | null,
  enabled = true,
  conversationId?: string | null,
) {
  const convQs = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : ''
  const key = workspaceId && enabled ? `/api/v1/ws/${workspaceId}/browser/live-view${convQs}` : null
  const { data, error, isLoading, isValidating, mutate } = useSWR<BrowserLiveView>(key, fetcher, {
    revalidateOnFocus: false,
    revalidateOnMount: true,
    shouldRetryOnError,
    errorRetryCount: 4,
    // Give cold-start 503s a few seconds before the next attempt.
    errorRetryInterval: 3000,
  })
  return {
    url: data?.url ?? null,
    loading: isLoading,
    /** True while SWR is in-flight (first load or error retry). */
    validating: isValidating,
    error: error as Error | undefined,
    refresh: mutate,
  }
}
