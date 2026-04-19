'use client'

import { useEffect, useMemo } from 'react'
import { createApiClient, useAuthStore, useWorkspaceStore } from '@cubebox/core'
import { useAuthRedirect } from '@/hooks/useAuthRedirect'
import { AppTopBar } from '@/components/layout/AppTopBar'

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const client = useMemo(() => createApiClient(''), [])
  useAuthRedirect(client)

  useEffect(() => {
    useAuthStore.getState().loadMe(client)
    useWorkspaceStore.getState().fetchList(client)
  }, [client])

  return (
    <div className="min-h-screen flex flex-col bg-background text-foreground">
      <AppTopBar />
      <div className="flex-1 flex flex-col">{children}</div>
    </div>
  )
}
