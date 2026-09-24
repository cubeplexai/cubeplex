'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { createApiClient, useAuthStore } from '@cubeplex/core'
import { OnboardingForm } from '@/components/onboarding/OnboardingForm'
import { useAuthRedirect } from '@/hooks/useAuthRedirect'

export default function OnboardingPage() {
  const router = useRouter()
  const user = useAuthStore((s) => s.user)
  const client = useMemo(() => createApiClient(''), [])
  const completingRef = useRef(false)
  const [completing, setCompleting] = useState(false)
  useAuthRedirect(client)

  useEffect(() => {
    useAuthStore.getState().loadMe(client)
  }, [client])

  useEffect(() => {
    if (user && !user.needs_onboarding && !completing && !completingRef.current) {
      router.replace('/')
    }
  }, [user, completing, router])

  if (!user) {
    return <div className="text-sm text-muted-foreground">Loading...</div>
  }
  if (!user.needs_onboarding) return null

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <OnboardingForm
        client={client}
        onCompletionChange={(completing) => {
          completingRef.current = completing
          setCompleting(completing)
        }}
      />
    </div>
  )
}
