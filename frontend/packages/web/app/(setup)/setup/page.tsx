'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { createApiClient, useAuthStore } from '@cubebox/core'
import { SetupForm } from '@/components/setup/SetupForm'

export default function SetupPage() {
  const router = useRouter()
  const user = useAuthStore((s) => s.user)

  useEffect(() => {
    const client = createApiClient('')
    useAuthStore.getState().loadMe(client)
  }, [])

  useEffect(() => {
    if (user && !user.needs_org_setup) {
      router.replace('/')
    }
  }, [user, router])

  if (!user) {
    return <div className="text-sm text-muted-foreground">Loading…</div>
  }
  if (!user.needs_org_setup) return null

  return <SetupForm />
}
