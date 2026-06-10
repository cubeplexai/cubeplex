'use client'

import Link from 'next/link'
import { Suspense, useEffect, useRef, useState } from 'react'
import { usePathname, useSearchParams } from 'next/navigation'
import { useTranslations } from 'next-intl'
import {
  type Conversation,
  createApiClient,
  useConversationStore,
  useWorkspaceStore,
} from '@cubebox/core'
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
import {
  Box,
  Brain,
  CalendarClock,
  type LucideIcon,
  KeyRound,
  MoreHorizontal,
  Pencil,
  Pin,
  PinOff,
  Plug,
  Plus,
  Settings,
  Sparkles,
  Trash2,
  Users,
  Webhook,
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
    // eslint-disable-next-line react-hooks/set-state-in-effect
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
              onClick={() => {
                setDraft(convo.title)
                setIsEditing(true)
              }}
            >
              <Pencil className="size-3.5" />
              {tSidebar('renameConversation')}
            </DropdownMenuItem>
            <DropdownMenuItem
              disabled={isPinPending}
              onClick={() => {
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
              onClick={() => {
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

interface WorkspaceNavEntry {
  key: string
  labelKey:
    | 'skills'
    | 'mcp'
    | 'memory'
    | 'scheduledTasks'
    | 'members'
    | 'settings'
    | 'triggers'
    | 'sandboxEnv'
  icon: LucideIcon
  href: string
  isActive: boolean
}

function WorkspaceNav({ wsId }: { wsId: string }): React.ReactElement {
  const tSidebar = useTranslations('sidebar')
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const wsRole = useWorkspaceStore((s) => s.workspaces.find((w) => w.id === wsId)?.role)
  const isAdmin = wsRole === 'admin'

  const settingsPrefix = `/w/${wsId}/settings`
  const memoryPrefix = `/w/${wsId}/memory`
  const scheduledTasksPrefix = `/w/${wsId}/scheduled-tasks`
  const triggersPrefix = `/w/${wsId}/triggers`
  const skillsPrefix = `/w/${wsId}/skills`
  const sandboxEnvPrefix = `/w/${wsId}/sandbox-env`
  const onSettings = pathname?.startsWith(settingsPrefix) ?? false
  const onMemory = pathname?.startsWith(memoryPrefix) ?? false
  const onScheduledTasks = pathname?.startsWith(scheduledTasksPrefix) ?? false
  const onTriggers = pathname?.startsWith(triggersPrefix) ?? false
  const onSkills = pathname?.startsWith(skillsPrefix) ?? false
  const onSandboxEnv = pathname?.startsWith(sandboxEnvPrefix) ?? false
  const currentTab = searchParams.get('tab') ?? 'workspace'

  const entries: WorkspaceNavEntry[] = [
    {
      key: 'skills',
      labelKey: 'skills',
      icon: Sparkles,
      href: skillsPrefix,
      isActive: onSkills,
    },
    {
      key: 'mcp',
      labelKey: 'mcp',
      icon: Plug,
      href: `${settingsPrefix}?tab=mcp`,
      isActive: onSettings && currentTab === 'mcp',
    },
    {
      key: 'memory',
      labelKey: 'memory',
      icon: Brain,
      href: memoryPrefix,
      isActive: onMemory,
    },
    {
      key: 'scheduledTasks',
      labelKey: 'scheduledTasks',
      icon: CalendarClock,
      href: scheduledTasksPrefix,
      isActive: onScheduledTasks,
    },
    {
      key: 'triggers',
      labelKey: 'triggers',
      icon: Webhook,
      href: triggersPrefix,
      isActive: onTriggers,
    },
    {
      key: 'sandboxEnv',
      labelKey: 'sandboxEnv',
      icon: KeyRound,
      href: sandboxEnvPrefix,
      isActive: onSandboxEnv,
    },
  ]
  if (isAdmin) {
    entries.push({
      key: 'members',
      labelKey: 'members',
      icon: Users,
      href: `${settingsPrefix}?tab=members`,
      isActive: onSettings && currentTab === 'members',
    })
  }
  entries.push({
    key: 'settings',
    labelKey: 'settings',
    icon: Settings,
    href: `${settingsPrefix}?tab=workspace`,
    isActive: onSettings && currentTab === 'workspace',
  })

  return (
    <nav className="px-2 pt-1 pb-1 space-y-0.5">
      {entries.map((entry) => {
        const Icon = entry.icon
        return (
          <Link
            key={entry.key}
            href={entry.href}
            className={`flex items-center gap-2 px-2 py-1.5 rounded-md text-xs transition-colors ${
              entry.isActive
                ? 'text-foreground bg-primary/8 font-medium'
                : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
            }`}
            aria-label={tSidebar(entry.labelKey)}
          >
            <Icon className="size-3.5 shrink-0" />
            <span>{tSidebar(entry.labelKey)}</span>
          </Link>
        )
      })}
    </nav>
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

      {/* Workspace nav: skills, mcp, memory, members, settings */}
      {currentWsId && (
        <Suspense>
          <WorkspaceNav wsId={currentWsId} />
        </Suspense>
      )}

      {/* Recent conversations — flex-1 so it stretches to the footer. */}
      <div className="flex-1 flex flex-col min-h-0">
        <div className="px-2 pt-2 pb-1">
          <p className="px-2 text-[10px] font-medium uppercase tracking-widest text-muted-foreground/60">
            {tSidebar('recentChats')}
          </p>
        </div>
        <ScrollArea className="flex-1 px-2">
          {conversations.length === 0 ? (
            <p className="px-2 py-1.5 text-xs text-muted-foreground/60">
              {tSidebar('noRecentChats')}
            </p>
          ) : (
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
          )}
        </ScrollArea>
      </div>

      {/* Footer: avatar popover */}
      <div className="border-t border-border/60 p-2 flex items-center gap-1">
        <div className="min-w-0 flex-1">
          <AvatarPopover />
        </div>
      </div>
    </aside>
  )
}
