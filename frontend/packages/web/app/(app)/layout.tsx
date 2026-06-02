'use client'

import { useEffect, useMemo } from 'react'
import { useRouter } from 'next/navigation'
import { createApiClient, useAuthStore, useWorkspaceStore, useUserEvents } from '@cubebox/core'
import { useAuthRedirect } from '@/hooks/useAuthRedirect'
import { Sidebar } from '@/components/layout/Sidebar'
import { Toaster } from 'sonner'
import { MemoryUpdateToastBridge } from '@/components/chat/MemoryUpdateToastBridge'

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
      <Toaster position="bottom-right" />
      <MemoryUpdateToastBridge />
    </div>
  )
}
