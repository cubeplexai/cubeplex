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

/**
 * Fetches the embeddable live-view URL for the workspace's sandbox browser.
 * The backend ensures the Neko stack is running before returning the URL.
 *
 * Pass ``conversationId`` so dedicated-mode topic / standalone group-chat
 * conversations resolve to the shared sandbox's browser instead of the
 * viewer's personal one.
 */
export function useBrowserLiveView(
  workspaceId: string | null,
  enabled = true,
  conversationId?: string | null,
) {
  const convQs = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : ''
  const key = workspaceId && enabled ? `/api/v1/ws/${workspaceId}/browser/live-view${convQs}` : null
  const { data, error, isLoading, mutate } = useSWR<BrowserLiveView>(key, fetcher, {
    revalidateOnFocus: false,
    revalidateOnMount: true,
    shouldRetryOnError: false,
  })
  return {
    url: data?.url ?? null,
    loading: isLoading,
    error: error as Error | undefined,
    refresh: mutate,
  }
}
