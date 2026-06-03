'use client'

import { useEffect, useMemo } from 'react'
import { useRouter } from 'next/navigation'
import { createApiClient, useAuthStore, useWorkspaceStore } from '@cubebox/core'
// Direct import: useUserEvents is a client-only hook and must NOT be loaded
// via the @cubebox/core barrel (would force react into server bundles for
// any file importing AUTH_COOKIE_NAME).
import { useUserEvents } from '@cubebox/core/hooks/useUserEvents'
import { useAuthRedirect } from '@/hooks/useAuthRedirect'
import { Sidebar } from '@/components/layout/Sidebar'

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const client = useMemo(() => createApiClient(''), [])
  useAuthRedirect(client)
  useUserEvents(client)

  useEffect(() => {
    useAuthStore.getState().loadMe(client)
    useWorkspaceStore.getState().fetchList(client)
  }, [client])

  // M9: pending owners (single_tenant first user before /setup completes) bounce to /setup.
  const needsOrgSetup = useAuthStore((s) => s.user?.needs_org_setup)
  useEffect(() => {
    if (needsOrgSetup) router.replace('/setup')
  }, [needsOrgSetup, router])

  return (
    <div className="flex h-screen bg-background text-foreground">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">{children}</div>
    </div>
  )
}
