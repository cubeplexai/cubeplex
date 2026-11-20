'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import useSWR from 'swr'

interface SandboxTerminal {
  url: string
}

const MAX_RETRY_COUNT = 4
const RETRY_INTERVAL_MS = 3000

async function fetcher(url: string): Promise<SandboxTerminal> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) {
    throw new Error(`terminal fetch failed: ${res.status}`)
  }
  return res.json() as Promise<SandboxTerminal>
}

/** Retry transient cold-start / proxy failures; skip deterministic 4xx and 501. */
export function shouldRetryTerminalError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err ?? '')
  const match = /terminal fetch failed: (\d{3})\b/.exec(msg)
  if (match) {
    const status = Number(match[1])
    if (status === 501) return false
    if (status >= 400 && status < 500 && status !== 408 && status !== 429) return false
  }
  return true
}

export function useSandboxTerminal(
  workspaceId: string | null,
  enabled = true,
  conversationId?: string | null,
) {
  const convQs = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : ''
  const key = workspaceId && enabled ? `/api/v1/ws/${workspaceId}/sandbox/terminal${convQs}` : null
  const [retrying, setRetrying] = useState(false)
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const clearRetryTimer = useCallback(() => {
    if (retryTimerRef.current) {
      clearTimeout(retryTimerRef.current)
      retryTimerRef.current = null
    }
  }, [])

  useEffect(() => {
    setRetrying(false)
    return clearRetryTimer
  }, [key, clearRetryTimer])

  const { data, error, isLoading, isValidating, mutate } = useSWR<SandboxTerminal>(key, fetcher, {
    revalidateOnFocus: false,
    revalidateOnMount: true,
    revalidateOnReconnect: false,
    shouldRetryOnError: shouldRetryTerminalError,
    onSuccess: () => {
      clearRetryTimer()
      setRetrying(false)
    },
    onErrorRetry: (err, _key, _config, revalidate, { retryCount }) => {
      clearRetryTimer()
      if (!shouldRetryTerminalError(err) || retryCount > MAX_RETRY_COUNT) {
        setRetrying(false)
        return
      }
      setRetrying(true)
      retryTimerRef.current = setTimeout(() => {
        retryTimerRef.current = null
        void revalidate({ retryCount })
      }, RETRY_INTERVAL_MS)
    },
  })

  const activeRetrying = retrying && Boolean(error) && shouldRetryTerminalError(error)
  return {
    url: data?.url ?? null,
    loading: isLoading || isValidating || activeRetrying,
    error: activeRetrying ? undefined : (error as Error | undefined),
    refresh: mutate,
  }
}
