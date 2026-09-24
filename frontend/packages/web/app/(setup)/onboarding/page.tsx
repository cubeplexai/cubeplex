'use client'

import { useEffect, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { createApiClient, useAuthStore } from '@cubeplex/core'
import { OnboardingForm } from '@/components/onboarding/OnboardingForm'

export default function OnboardingPage() {
  const router = useRouter()
  const user = useAuthStore((s) => s.user)
  const completingRef = useRef(false)

  useEffect(() => {
    const client = createApiClient('')
    useAuthStore.getState().loadMe(client)
  }, [])

  useEffect(() => {
    if (user && !user.needs_onboarding && !completingRef.current) router.replace('/')
  }, [user, router])

  if (!user) {
    return <div className="text-sm text-muted-foreground">Loading...</div>
  }
  if (!user.needs_onboarding) return null

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <OnboardingForm
        onCompletionChange={(completing) => {
          completingRef.current = completing
        }}
      />
    </div>
  )
}
