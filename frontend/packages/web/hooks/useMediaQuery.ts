'use client'

import { useSyncExternalStore } from 'react'

/** Subscribe to a CSS media query.
 *  SSR-safe with caller-provided `serverFallback` so layouts that prefer
 *  desktop on first paint don't flash the mobile branch during hydration. */
export function useMediaQuery(query: string, serverFallback: boolean = false): boolean {
  return useSyncExternalStore(
    (callback) => {
      if (typeof window === 'undefined') return () => {}
      const mql = window.matchMedia(query)
      mql.addEventListener('change', callback)
      return () => mql.removeEventListener('change', callback)
    },
    () => {
      if (typeof window === 'undefined') return serverFallback
      return window.matchMedia(query).matches
    },
    () => serverFallback,
  )
}
