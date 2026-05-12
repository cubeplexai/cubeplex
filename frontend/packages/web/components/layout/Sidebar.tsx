'use client'

import Link from 'next/link'
import { Suspense, useEffect, useRef, useState } from 'react'
import { usePathname } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { type Conversation, createApiClient, useConversationStore } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ScrollArea } from '@/components/ui/scroll-area'
import { AvatarPopover } from '@/components/sidebar/AvatarPopover'
import { WorkspacesSection } from '@/components/sidebar/WorkspacesSection'
import { SettingsNav } from '@/components/workspace-settings/SettingsNav'
import {
  Box,
  Brain,
  MoreHorizontal,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Settings,
  Trash2,
} from 'lucide-react'

type ApiClient = ReturnType<typeof createApiClient>

function buildClient(currentWsId: string | null): ApiClient {
  const client = createApiClient('')
  if (currentWsId) client.setWorkspaceId(currentWsId)
  return client
}

function ConversationRow({
  convo,
  isActive,
  currentWsId,
}: {
  convo: Conversation
  isActive: boolean
  currentWsId: string | null
}): React.ReactElement {
  const tSidebar = useTranslations('sidebar')
  const tShell = useTranslations('shellLayout')
  const { remove, rename, setPin, setActive, pinPending } = useConversationStore()
  const isPinPending = !!pinPending[convo.id]

  const [isEditing, setIsEditing] = useState(false)
  const [draft, setDraft] = useState(convo.title)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!isEditing) setDraft(convo.title)
  }, [convo.title, isEditing])

  useEffect(() => {
    if (isEditing) {
      inputRef.current?.focus()
      inputRef.current?.select()
    }
  }, [isEditing])

  const commitEdit = async (): Promise<void> => {
    const next = draft.trim()
    setIsEditing(false)
    if (!next || next === convo.title) return
    try {
      await rename(buildClient(currentWsId), convo.id, next)
    } catch (err) {
      console.error('Failed to rename conversation:', err)
    }
  }

  const cancelEdit = (): void => {
    setIsEditing(false)
    setDraft(convo.title)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>): void => {
    if (e.key === 'Enter') {
      e.preventDefault()
      void commitEdit()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      cancelEdit()
    }
  }

  const stateClass = isActive
    ? 'text-foreground bg-primary/8'
    : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
  const baseRowClasses =
    'group relative flex items-center gap-1 pl-2 pr-1 py-1.5 ' +
    `rounded-md transition-colors ${stateClass}`

  if (isEditing) {
    return (
      <li>
        <div className={baseRowClasses}>
          {convo.is_pinned && <Pin className="size-3 shrink-0 text-primary/70 fill-primary/30" />}
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={() => void commitEdit()}
            className={
              'flex-1 min-w-0 bg-background/80 border border-border rounded ' +
              'px-1.5 py-0.5 text-[12.5px] font-medium leading-none outline-none ' +
              'focus:border-primary'
            }
          />
        </div>
      </li>
    )
  }

  return (
    <li>
      <Link
        href={currentWsId ? `/w/${currentWsId}/conversations/${convo.id}` : '/'}
        onClick={() => setActive(convo.id)}
        className={baseRowClasses}
      >
        {isActive && (
          <div className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 bg-primary rounded-r-full" />
        )}
        {convo.is_pinned && <Pin className="size-3 shrink-0 text-primary/70 fill-primary/30" />}
        <div className="flex-1 min-w-0 truncate text-[12.5px] font-medium leading-tight">
          {convo.title || tSidebar('untitledChat')}
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger
            onClick={(e) => {
              e.preventDefault()
              e.stopPropagation()
            }}
            className={
              'p-1 rounded hover:bg-accent text-muted-foreground ' +
              'hover:text-foreground shrink-0 opacity-0 ' +
              'group-hover:opacity-100 data-[popup-open]:opacity-100 ' +
              'transition-opacity'
            }
            aria-label={tSidebar('moreActions')}
            title={tSidebar('moreActions')}
          >
            <MoreHorizontal className="size-3.5" />
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="end"
            side="right"
            sideOffset={4}
            className="w-36"
            onClick={(e) => e.stopPropagation()}
          >
            <DropdownMenuItem
              onSelect={() => {
                setDraft(convo.title)
                setIsEditing(true)
              }}
            >
              <Pencil className="size-3.5" />
              {tSidebar('renameConversation')}
            </DropdownMenuItem>
            <DropdownMenuItem
              disabled={isPinPending}
              onSelect={() => {
                void setPin(buildClient(currentWsId), convo.id, !convo.is_pinned).catch((err) =>
                  console.error('Failed to toggle pin:', err),
                )
              }}
            >
              {convo.is_pinned ? <PinOff className="size-3.5" /> : <Pin className="size-3.5" />}
              {convo.is_pinned ? tSidebar('unpinConversation') : tSidebar('pinConversation')}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              variant="destructive"
              onSelect={() => {
                void remove(buildClient(currentWsId), convo.id).catch((err) =>
                  console.error('Failed to delete conversation:', err),
                )
              }}
            >
              <Trash2 className="size-3.5" />
              {tShell('deleteConversation')}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </Link>
    </li>
  )
}

export function Sidebar(): React.ReactElement {
  const tSidebar = useTranslations('sidebar')
  const tShell = useTranslations('shellLayout')
  const { conversations, activeId } = useConversationStore()
  const pathname = usePathname()

  // Current workspace inferred from URL (no WorkspaceContext dependency).
  const wsMatch = pathname?.match(/^\/w\/([^/]+)/)
  const currentWsId = wsMatch ? wsMatch[1] : null
  const newChatHref = currentWsId ? `/w/${currentWsId}` : '/'
  const isSettingsRoute = currentWsId
    ? (pathname?.startsWith('/w/' + currentWsId + '/settings') ?? false)
    : false

  return (
    <aside
      aria-label={tShell('sidebar')}
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

      {/* Memory link */}
      {currentWsId && (
        <div className="px-2 pt-1 pb-1">
          <Link
            href={`/w/${currentWsId}/memory`}
            className={`flex items-center gap-2 px-2 py-1.5 rounded-md text-xs transition-colors ${
              pathname?.startsWith('/w/' + currentWsId + '/memory')
                ? 'text-foreground bg-primary/8 font-medium'
                : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
            }`}
          >
            <Brain className="size-3.5 shrink-0" />
            <span>Memory</span>
          </Link>
        </div>
      )}

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
              <ConversationRow
                key={convo.id}
                convo={convo}
                isActive={activeId === convo.id}
                currentWsId={currentWsId}
              />
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
        <div className="min-w-0 flex-1">
          <AvatarPopover />
        </div>
        {currentWsId && (
          <Link
            href={`/w/${currentWsId}/settings`}
            className={`shrink-0 p-1.5 rounded-md transition-colors ${
              isSettingsRoute
                ? 'text-primary bg-primary/10'
                : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
            }`}
            aria-label={tShell('workspaceSettings')}
          >
            <Settings className="size-4" />
          </Link>
        )}
      </div>
    </aside>
  )
}
