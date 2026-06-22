'use client'

import { useCallback, useSyncExternalStore } from 'react'

// Module-level shared 1 Hz ticker: a single `setInterval` drives every consumer
// instead of each pending tool / reasoning bubble / subagent card running its
// own. With ~10 tools in flight during a stream this collapses 10× per-second
// re-render commits down to a single batched commit — measurable wall-clock win
// because each commit walks the whole MessageList memo-bailout chain.
const listeners = new Set<() => void>()
let timer: ReturnType<typeof setInterval> | null = null
let snapshotMs = Math.floor(Date.now() / 1000) * 1000

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  if (listeners.size === 1) {
    timer = setInterval(() => {
      snapshotMs = Math.floor(Date.now() / 1000) * 1000
      for (const l of listeners) l()
    }, 1000)
  }
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0 && timer) {
      clearInterval(timer)
      timer = null
    }
  }
}

function getSnapshot(): number {
  return snapshotMs
}

const NOOP_UNSUB = (): void => {}

/**
 * Subscribe to a shared 1 Hz tick. Returns "now" rounded to the most recent
 * whole second (in ms). When `active` is false the hook reads the current
 * snapshot once and does not subscribe, so idle call sites don't re-render.
 */
export function useNowSeconds(active: boolean): number {
  const subscribeFn = useCallback((l: () => void) => (active ? subscribe(l) : NOOP_UNSUB), [active])
  return useSyncExternalStore(subscribeFn, getSnapshot, getSnapshot)
}
