'use client'

import { useEffect, useMemo } from 'react'
import { createApiClient, useAuthStore, useWorkspaceStore } from '@cubebox/core'
import { useAuthRedirect } from '@/hooks/useAuthRedirect'
import { Sidebar } from '@/components/layout/Sidebar'

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const client = useMemo(() => createApiClient(''), [])
  useAuthRedirect(client)

  useEffect(() => {
    useAuthStore.getState().loadMe(client)
    useWorkspaceStore.getState().fetchList(client)
  }, [client])

  return (
    <div className="flex h-screen bg-background text-foreground">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">{children}</div>
    </div>
  )
}
