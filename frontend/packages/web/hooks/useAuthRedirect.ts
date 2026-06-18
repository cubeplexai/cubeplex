'use client'

import { useEffect } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import type { ApiClient } from '@cubebox/core'

export function useAuthRedirect(client: ApiClient) {
  const router = useRouter()
  const pathname = usePathname()

  useEffect(() => {
    let firing = false
    const unsubscribe = client.onUnauthorized(() => {
      if (firing) return
      firing = true
      const next = encodeURIComponent(pathname)
      // Clear the auth + CSRF cookies via the backend's logout endpoint
      // BEFORE bouncing to /login. proxy.ts redirects /login → / whenever
      // an auth cookie is present (any value, even a stale one), so leaving
      // the cookie in place would trap the user in /w/... → 401 → /login
      // → / → 401 → /login … forever. The logout endpoint accepts 401
      // silently and always replies with Set-Cookie that expires the
      // cookies; we ignore its return value.
      void fetch(`${client.baseUrl}/api/v1/auth/logout`, {
        method: 'POST',
        credentials: 'include',
      })
        .catch(() => undefined)
        .finally(() => {
          router.push(`/login?next=${next}`)
        })
    })
    return () => {
      unsubscribe()
    }
  }, [client, router, pathname])
}
