'use client'

import { useEffect, useMemo } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import { createApiClient, useAuthStore, useWorkspaceStore } from '@cubebox/core'
// Direct import: useUserEvents is a client-only hook and must NOT be loaded
// via the @cubebox/core barrel (would force react into server bundles for
// any file importing AUTH_COOKIE_NAME).
import { useUserEvents } from '@cubebox/core/hooks/useUserEvents'
import { Menu } from 'lucide-react'
import { useAuthRedirect } from '@/hooks/useAuthRedirect'
import { useMobileMenu } from '@/hooks/useMobileMenu'
import { Sidebar } from '@/components/layout/Sidebar'
import { VerificationBanner } from '@/components/layout/VerificationBanner'
import { Sheet, SheetContent, SheetTitle, SheetDescription } from '@/components/ui/sheet'

/** Detect routes that mount AppShell — those render their own h-11 header
 *  with a mobile hamburger built in, so we don't add a second strip. */
function routeOwnsHeader(pathname: string | null): boolean {
  if (!pathname) return false
  // Chat conversation pages render <AppShell> (see app/(app)/w/[wsId]/conversations/[id]/page.tsx)
  // and the workspace home page also uses InputBar inside its own centered layout — no AppShell.
  return /^\/w\/[^/]+\/conversations\//.test(pathname)
}

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
  const drawerOpen = useMobileMenu((s) => s.isOpen)
  const setDrawerOpen = useMobileMenu((s) => s.set)
  const openDrawer = useMobileMenu((s) => s.open)
  const hasAppShellHeader = routeOwnsHeader(pathname)

  // Auto-close the mobile drawer on route change
  useEffect(() => {
    setDrawerOpen(false)
  }, [pathname, setDrawerOpen])

  return (
    <div className="flex h-screen bg-background text-foreground">
      <div className="hidden md:flex">
        <Sidebar />
      </div>
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Mobile fallback strip ONLY for routes that don't render their own
            AppShell header (memory, skills, settings, …). Chat pages embed
            the hamburger in their AppShell header to avoid two stacked
            44px bars eating mobile viewport. */}
        {!hasAppShellHeader && (
          <div className="md:hidden h-11 border-b border-border flex items-center px-3 shrink-0">
            <button
              type="button"
              onClick={openDrawer}
              className="grid size-7 place-items-center rounded text-muted-foreground hover:bg-accent transition-colors duration-fast"
              aria-label="Open menu"
            >
              <Menu className="size-4" />
            </button>
            <span className="ml-2 text-sm font-semibold tracking-tight">cubebox</span>
          </div>
        )}
        {/* Reserve remaining vertical space for the child page so the 44px
            mobile strip doesn't push h-full pages off the bottom of the
            viewport. flex-1 min-h-0 ensures overflow-hidden parents clip
            correctly and inner scroll regions still work. */}
        <div className="flex-1 min-h-0 flex flex-col">
          <VerificationBanner />
          {children}
        </div>
      </div>
      <Sheet open={drawerOpen} onOpenChange={(open) => setDrawerOpen(open)}>
        <SheetContent side="left" className="w-56 max-w-[80vw] p-0">
          <SheetTitle className="sr-only">Navigation menu</SheetTitle>
          <SheetDescription className="sr-only">
            Workspace, recent conversations, and account
          </SheetDescription>
          <Sidebar />
        </SheetContent>
      </Sheet>
    </div>
  )
}
