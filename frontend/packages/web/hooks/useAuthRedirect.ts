'use client'

import { useEffect } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import type { ApiClient } from '@cubebox/core'

export function useAuthRedirect(client: ApiClient) {
  const router = useRouter()
  const pathname = usePathname()

  useEffect(() => {
    const unsubscribe = client.onUnauthorized(() => {
      const next = encodeURIComponent(pathname)
      router.push(`/login?next=${next}`)
    })
    return () => {
      unsubscribe()
    }
  }, [client, router, pathname])
}
