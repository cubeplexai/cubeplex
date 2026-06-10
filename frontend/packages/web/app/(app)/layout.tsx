'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import { createApiClient, useAuthStore, useWorkspaceStore } from '@cubebox/core'
// Direct import: useUserEvents is a client-only hook and must NOT be loaded
// via the @cubebox/core barrel (would force react into server bundles for
// any file importing AUTH_COOKIE_NAME).
import { useUserEvents } from '@cubebox/core/hooks/useUserEvents'
import { Menu } from 'lucide-react'
import { useAuthRedirect } from '@/hooks/useAuthRedirect'
import { Sidebar } from '@/components/layout/Sidebar'
import { Sheet, SheetContent } from '@/components/ui/sheet'

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

  const pathname = usePathname()
  const [drawerOpen, setDrawerOpen] = useState(false)

  // Auto-close the mobile drawer on route change
  useEffect(() => {
    setDrawerOpen(false)
  }, [pathname])

  return (
    <div className="flex h-screen bg-background text-foreground">
      <div className="hidden md:flex">
        <Sidebar />
      </div>
      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="md:hidden h-11 border-b border-border flex items-center px-3 shrink-0">
          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            className="grid size-7 place-items-center rounded text-muted-foreground hover:bg-accent transition-colors duration-fast"
            aria-label="Open menu"
          >
            <Menu className="size-4" />
          </button>
          <span className="ml-2 text-sm font-semibold tracking-tight">cubebox</span>
        </div>
        {children}
      </div>
      <Sheet open={drawerOpen} onOpenChange={(open) => setDrawerOpen(open)}>
        <SheetContent side="left" className="w-56 max-w-[80vw] p-0">
          <Sidebar />
        </SheetContent>
      </Sheet>
    </div>
  )
}
