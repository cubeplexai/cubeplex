'use client'

import { useEffect } from 'react'
import type { ApiClient } from '../api/client'
import { streamUserEvents } from '../api/userEventStream'
import { useAuthStore } from '../stores/authStore'
import { useMemoryEventStore } from '../stores/memoryEventStore'

// Note: this hook deliberately does NOT persist a per-connection `since`
// cursor. The previous design advanced a localStorage cursor on every received
// event, but the in-memory Zustand store can be lost (tab reload / close)
// before the user clicks the chip to mark the event read — in which case the
// stored cursor is already past the event id, and the next connection sends
// `since=<that id>`, causing the unread event to be filtered out and
// permanently invisible on that device. The server's `read_at IS NULL` is the
// durable source of truth instead: each connection re-receives still-unread
// events, the store dedupes by id, and the chip POST /read advances the
// server-side state when the user actually dismisses the notification.

export function useUserEvents(client: ApiClient): void {
  const add = useMemoryEventStore((s) => s.add)
  const userId = useAuthStore((s) => s.user?.id ?? null)

  useEffect(() => {
    // Clear any events from a previous session before subscribing. Without
    // this, logging out and logging back in as a different user in the same
    // tab would let the previous user's unread events surface as chips/toasts
    // for the new user. Runs on every userId change (including transitions
    // to null on logout).
    useMemoryEventStore.getState().reset()
    if (!userId) return // wait for auth to load before subscribing
    const ac = new AbortController()
    let backoff = 1000
    const MAX_BACKOFF = 30000

    const run = async () => {
      while (!ac.signal.aborted) {
        let cleanEnd = false
        try {
          for await (const ev of streamUserEvents(client, { signal: ac.signal })) {
            if (ev.type === 'memory_updated') add(ev)
            backoff = 1000 // reset on successful event
          }
          cleanEnd = true
        } catch {
          if (ac.signal.aborted) return
          // network error / non-2xx; fall through to backoff
        }
        if (ac.signal.aborted) return
        if (cleanEnd) backoff = 1000 // server closed cleanly — reconnect promptly
        await new Promise((r) => setTimeout(r, backoff))
        backoff = Math.min(backoff * 2, MAX_BACKOFF)
      }
    }

    run().catch(() => {
      /* swallow */
    })
    return () => ac.abort()
  }, [client, add, userId])
}
