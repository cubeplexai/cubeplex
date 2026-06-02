'use client'

import { useEffect } from 'react'
import type { ApiClient } from '../api/client'
import { streamUserEvents } from '../api/userEventStream'
import { useMemoryEventStore } from '../stores/memoryEventStore'

const STORAGE_KEY = 'cubebox.userEvents.lastSeenId'

export function useUserEvents(client: ApiClient): void {
  const add = useMemoryEventStore((s) => s.add)
  useEffect(() => {
    const ac = new AbortController()
    let backoff = 1000
    const MAX_BACKOFF = 30000

    const run = async () => {
      while (!ac.signal.aborted) {
        let cleanEnd = false
        try {
          const since =
            typeof window !== 'undefined'
              ? (localStorage.getItem(STORAGE_KEY) ?? undefined)
              : undefined
          for await (const ev of streamUserEvents(client, { signal: ac.signal, since })) {
            if (ev.type === 'memory_updated') {
              add(ev)
              // localStorage.setItem can throw in Safari private mode (quota=0).
              // Don't let storage failure abort the event-processing loop.
              try {
                localStorage.setItem(STORAGE_KEY, ev.id)
              } catch {
                /* ignore */
              }
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
  }, [client, add])
}
