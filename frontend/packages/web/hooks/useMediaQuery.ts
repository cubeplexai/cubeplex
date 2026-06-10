'use client'

import { useSyncExternalStore } from 'react'

/** Subscribe to a CSS media query. SSR-safe (returns `false` on the server). */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (callback) => {
      if (typeof window === 'undefined') return () => {}
      const mql = window.matchMedia(query)
      mql.addEventListener('change', callback)
      return () => mql.removeEventListener('change', callback)
    },
    () => {
      if (typeof window === 'undefined') return false
      return window.matchMedia(query).matches
    },
    () => false,
  )
}
