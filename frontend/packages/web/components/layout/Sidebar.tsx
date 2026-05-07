'use client'

import Link from 'next/link'
import { Suspense } from 'react'
import { usePathname } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { createApiClient, useConversationStore } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { AvatarPopover } from '@/components/sidebar/AvatarPopover'
import { WorkspacesSection } from '@/components/sidebar/WorkspacesSection'
import { SettingsNav } from '@/components/workspace-settings/SettingsNav'
import { Box, Plus, Settings, Trash2 } from 'lucide-react'

function formatRelativeTime(
  dateStr: string,
  t: ReturnType<typeof useTranslations<'time'>>,
): string {
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)

  if (diffMins < 1) return t('justNow')
  if (diffMins < 60) return t('minutesAgo', { n: diffMins })
  if (diffHours < 24) return t('hoursAgo', { n: diffHours })
  return t('daysAgo', { n: diffDays })
}

export function Sidebar() {
  const tSidebar = useTranslations('sidebar')
  const tTime = useTranslations('time')
  const { conversations, activeId, remove, setActive } = useConversationStore()
  const pathname = usePathname()

  // Current workspace inferred from URL (no WorkspaceContext dependency).
  const wsMatch = pathname?.match(/^\/w\/([^/]+)/)
  const currentWsId = wsMatch ? wsMatch[1] : null
  const newChatHref = currentWsId ? `/w/${currentWsId}` : '/'
  const isSettingsRoute = currentWsId
    ? (pathname?.startsWith('/w/' + currentWsId + '/settings') ?? false)
    : false

  const handleDeleteClick = async (e: React.MouseEvent, id: string) => {
    e.preventDefault()
    const client = createApiClient('')
    if (currentWsId) client.setWorkspaceId(currentWsId)
    try {
      await remove(client, id)
    } catch (err) {
      console.error('Failed to delete conversation:', err)
    }
  }

  return (
    <aside
      aria-label="Sidebar"
      className="w-56 bg-card border-r border-border flex flex-col h-screen shrink-0"
    >
      {/* Brand + new chat */}
      <div className="px-4 pt-4 pb-3 border-b border-border/60">
        <div className="flex items-center gap-2 mb-3">
          <div className="w-6 h-6 rounded-md bg-primary flex items-center justify-center shrink-0 shadow-sm">
            <Box className="size-3.5 text-primary-foreground" strokeWidth={2.5} />
          </div>
          <span className="text-sm font-semibold tracking-tight">cubebox</span>
        </div>
        <Link href={newChatHref}>
          <Button variant="outline" size="sm" className="w-full h-7 text-xs gap-1.5">
            <Plus className="size-3" />
            {tSidebar('newChat')}
          </Button>
        </Link>
      </div>

      {/* Workspaces */}
      <WorkspacesSection />

      {/* Recent conversations — flex-1 so the SettingsNav (when shown) sits
          right above the footer instead of floating in the middle. */}
      <div className="flex-1 flex flex-col min-h-0">
        <div className="px-2 pt-2 pb-1">
          <p className="px-2 text-[10px] font-medium uppercase tracking-widest text-muted-foreground/60">
            {tSidebar('recentChats')}
          </p>
        </div>
        <ScrollArea className="flex-1 px-2">
          <ul className="space-y-0.5">
            {conversations.map((convo) => (
              <li key={convo.id}>
                <Link
                  href={currentWsId ? `/w/${currentWsId}/conversations/${convo.id}` : '/'}
                  onClick={() => setActive(convo.id)}
                  className={`group relative flex items-center gap-2 px-2 py-2 rounded-md transition-colors ${
                    activeId === convo.id
                      ? 'text-foreground bg-primary/8'
                      : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
                  }`}
                >
                  {activeId === convo.id && (
                    <div className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 bg-primary rounded-r-full" />
                  )}
                  <div className="flex-1 min-w-0 pl-1">
                    <div className="truncate text-[12.5px] font-medium leading-none mb-1">
                      {convo.title || tSidebar('untitledChat')}
                    </div>
                    <div className="text-[10px] text-muted-foreground/50">
                      {formatRelativeTime(convo.created_at, tTime)}
                    </div>
                  </div>
                  <button
                    onClick={(e) => handleDeleteClick(e, convo.id)}
                    className="opacity-0 group-hover:opacity-40 hover:!opacity-80 transition-opacity shrink-0 p-0.5"
                    aria-label="Delete conversation"
                  >
                    <Trash2 className="size-3" />
                  </button>
                </Link>
              </li>
            ))}
          </ul>
        </ScrollArea>
      </div>

      {/* Settings nav — only on settings route, anchored just above the footer. */}
      {isSettingsRoute && currentWsId && (
        <Suspense>
          <SettingsNav wsId={currentWsId} />
        </Suspense>
      )}

      {/* Footer: avatar popover + settings */}
      <div className="border-t border-border/60 p-2 flex items-center gap-1">
        <div className="flex-1">
          <AvatarPopover />
        </div>
        {currentWsId && (
          <Link
            href={`/w/${currentWsId}/settings`}
            className={`p-1.5 rounded-md transition-colors ${
              isSettingsRoute
                ? 'text-primary bg-primary/10'
                : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
            }`}
            aria-label="Workspace settings"
          >
            <Settings className="size-4" />
          </Link>
        )}
      </div>
    </aside>
  )
}
