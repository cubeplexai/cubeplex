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
        try {
          const since =
            typeof window !== 'undefined'
              ? (localStorage.getItem(STORAGE_KEY) ?? undefined)
              : undefined
          for await (const ev of streamUserEvents(client, { signal: ac.signal, since })) {
            if (ev.type === 'memory_updated') {
              add(ev)
              if (typeof window !== 'undefined') {
                localStorage.setItem(STORAGE_KEY, ev.id)
              }
            }
            backoff = 1000 // reset on successful event
          }
          // Stream ended normally — backoff before reconnect
        } catch {
          if (ac.signal.aborted) return
          // network error / non-2xx; fall through to backoff
        }
        if (ac.signal.aborted) return
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
