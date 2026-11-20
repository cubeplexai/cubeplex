'use client'

import useSWR from 'swr'

interface SandboxTerminal {
  url: string
}

async function fetcher(url: string): Promise<SandboxTerminal> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) {
    throw new Error(`terminal fetch failed: ${res.status}`)
  }
  return res.json() as Promise<SandboxTerminal>
}

/** Retry transient cold-start / proxy failures; skip auth, not-found, and unsupported. */
export function shouldRetryTerminalError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err ?? '')
  if (/terminal fetch failed: (40[134]|501)\b/.test(msg)) return false
  return true
}

export function useSandboxTerminal(
  workspaceId: string | null,
  enabled = true,
  conversationId?: string | null,
) {
  const convQs = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : ''
  const key = workspaceId && enabled ? `/api/v1/ws/${workspaceId}/sandbox/terminal${convQs}` : null
  const { data, error, isLoading, isValidating, mutate } = useSWR<SandboxTerminal>(key, fetcher, {
    revalidateOnFocus: false,
    revalidateOnMount: true,
    revalidateOnReconnect: false,
    shouldRetryOnError: shouldRetryTerminalError,
    errorRetryCount: 4,
    errorRetryInterval: 3000,
  })
  return {
    url: data?.url ?? null,
    loading: isLoading || isValidating,
    error: error as Error | undefined,
    refresh: mutate,
  }
}
