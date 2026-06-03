'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { createApiClient, getMemoryCount, useMemoryEventStore } from '@cubebox/core'

/**
 * Per-conversation memory count, refresh-safe.
 *
 * Sourced from `GET /memory/count` so it survives page reload and reflects
 * any save (main agent CONVERSATION + reflection REFLECTION).
 *
 * SSE events for the same conversation trigger a refetch — we don't increment
 * locally to avoid maintaining dedup state.
 *
 * Returns `null` while the initial fetch is in flight; once a number is
 * known it stays at the last-known value across refetches so the UI doesn't
 * flicker between number → null → number.
 */
export function useMemoryCount(workspaceId: string | null | undefined, conversationId: string) {
  const client = useMemo(() => {
    if (!workspaceId) return null
    const c = createApiClient('')
    c.setWorkspaceId(workspaceId)
    return c
  }, [workspaceId])

  const [count, setCount] = useState<number | null>(null)

  const refresh = useCallback(async () => {
    if (!client) return
    try {
      const n = await getMemoryCount(client, { source_conversation_id: conversationId })
      setCount(n)
    } catch {
      // best-effort: keep previous count visible
    }
  }, [client, conversationId])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh()
  }, [refresh])

  const eventCount = useMemoryEventStore((s) => (s.byConversation[conversationId] ?? []).length)
  useEffect(() => {
    if (eventCount === 0) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh()
  }, [eventCount, refresh])

  return count
}
