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

export function useSandboxTerminal(
  workspaceId: string | null,
  enabled = true,
  conversationId?: string | null,
) {
  const convQs = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : ''
  const key = workspaceId && enabled ? `/api/v1/ws/${workspaceId}/sandbox/terminal${convQs}` : null
  const { data, error, isLoading, mutate } = useSWR<SandboxTerminal>(key, fetcher, {
    revalidateOnFocus: false,
    revalidateIfStale: false,
    revalidateOnReconnect: false,
    shouldRetryOnError: false,
  })
  return {
    url: data?.url ?? null,
    loading: isLoading,
    error: error as Error | undefined,
    refresh: mutate,
  }
}
