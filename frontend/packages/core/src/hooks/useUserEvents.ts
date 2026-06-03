'use client'

import { useEffect } from 'react'
import type { ApiClient } from '../api/client'
import { markUserEventRead, streamUserEvents } from '../api/userEventStream'
import { useAuthStore } from '../stores/authStore'
import { useMemoryEventStore } from '../stores/memoryEventStore'

// Note: this hook deliberately does NOT persist a per-connection `since`
// cursor — the in-memory Zustand store can be lost (tab reload/close) before
// the cursor would be safe to advance, and a stored cursor past unprocessed
// events would silently filter them out forever. The server's `read_at IS
// NULL` is the durable source of truth.
//
// Events are CONSUMED as "refresh triggers" by the chip (count refetch via
// `useMemoryCount`). After delivering an event to the store, this hook
// fire-and-forget POSTs `/events/{id}/read` so the server marks it consumed
// and doesn't re-stream it on every future reconnect. Without that ack the
// `read_at IS NULL` backlog grows forever and every new tab replays the
// entire history before catching up to live events.

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
            if (ev.type === 'memory_updated') {
              add(ev)
              // Ack so the server doesn't keep replaying this event forever.
              // Best-effort — failure means a replay on next reconnect, which
              // the store's id-based dedup absorbs without UX impact.
              void markUserEventRead(client, ev.id).catch(() => {
                /* swallow */
              })
            }
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
