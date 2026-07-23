'use client'

import Link from 'next/link'
import { Suspense, useEffect, useRef, useState } from 'react'
import { usePathname, useSearchParams } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import { Tooltip as BaseTooltip } from '@base-ui/react'
import {
  type Conversation,
  type Topic,
  createApiClient,
  useConversationStore,
  useTopicStore,
} from '@cubeplex/core'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ScrollArea } from '@/components/ui/scroll-area'
import { AvatarPopover } from '@/components/sidebar/AvatarPopover'
import { ConversationSearch } from '@/components/sidebar/ConversationSearch'
import { TopicNode } from '@/components/sidebar/TopicNode'
import { WorkspaceSelector } from '@/components/sidebar/WorkspaceSelector'
import { CreateGroupChatDialog } from '@/components/dialogs/CreateGroupChatDialog'
import { DeleteConversationDialog } from '@/components/layout/DeleteConversationDialog'
import { AvatarStack } from '@/components/ui/avatar-stack'
import { CubePlexLogo } from '@/components/brand/CubePlexLogo'
import { VscMcp } from 'react-icons/vsc'
import {
  CalendarClock,
  Layers,
  type LucideIcon,
  MoreHorizontal,
  Package,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Pin,
  PinOff,
  SquarePen,
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

const SIDEBAR_AVATAR_MAX = 3

function GroupChatAvatars({ convoId }: { convoId: string }): React.ReactElement | null {
  const participants = useConversationStore((s) => s.conversationParticipants[convoId])
  if (!participants || participants.length === 0) return null
  return (
    <AvatarStack
      items={participants.map((p) => ({
        src: p.avatar_url,
        seed: p.avatar_seed ?? p.user_id,
        name: p.display_name,
        userId: p.user_id,
      }))}
      size={16}
      max={SIDEBAR_AVATAR_MAX}
    />
  )
}

function ConversationRow({
  convo,
  isActive,
  currentWsId,
  showGroupIcon = false,
}: {
  convo: Conversation
  isActive: boolean
  currentWsId: string | null
  showGroupIcon?: boolean
}): React.ReactElement {
  const tSidebar = useTranslations('sidebar')
  const tShell = useTranslations('shellLayout')
  const { rename, setPin, setActive, pinPending } = useConversationStore()
  const isPinPending = !!pinPending[convo.id]
  const hasGroupParticipants = useConversationStore(
    (s) => (s.conversationParticipants[convo.id]?.length ?? 0) > 0,
  )

  const [isEditing, setIsEditing] = useState(false)
  const [draft, setDraft] = useState(convo.title)
  const [deleteOpen, setDeleteOpen] = useState(false)
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
    ? 'text-foreground bg-accent'
    : 'text-muted-foreground hover:text-foreground hover:bg-accent'
  const baseRowClasses =
    'group relative flex items-center gap-1 pl-2 pr-1 py-1.5 ' +
    `rounded transition-colors duration-fast ${stateClass}`

  if (isEditing) {
    return (
      <li>
        <div className={baseRowClasses}>
          {convo.is_pinned && <Pin className="size-3 shrink-0 text-primary/70 fill-primary/30" />}
          {showGroupIcon && !hasGroupParticipants && (
            <Users className="size-3 shrink-0 text-muted-foreground" />
          )}
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
          <div className="absolute left-0 top-[22%] bottom-[22%] w-0.5 bg-primary rounded-r" />
        )}
        {convo.is_pinned && <Pin className="size-3 shrink-0 text-primary/70 fill-primary/30" />}
        {showGroupIcon && !hasGroupParticipants && (
          <Users className="size-3 shrink-0 text-muted-foreground" />
        )}
        <div className="flex-1 min-w-0 truncate text-[12.5px] font-medium leading-tight">
          {convo.title || tSidebar('untitledChat')}
        </div>
        {showGroupIcon && <GroupChatAvatars convoId={convo.id} />}
        <DropdownMenu>
          <DropdownMenuTrigger
            onClick={(e) => {
              e.preventDefault()
              e.stopPropagation()
            }}
            className={
              'p-1 rounded cursor-pointer hover:bg-accent text-muted-foreground ' +
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
              onClick={(e) => {
                e.preventDefault()
                e.stopPropagation()
                setDeleteOpen(true)
              }}
            >
              <Trash2 className="size-3.5" />
              {tShell('deleteConversation')}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </Link>
      <DeleteConversationDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        conversationId={convo.id}
        conversationTitle={convo.title}
        currentWsId={currentWsId}
      />
    </li>
  )
}

// Portal-based tooltip — unaffected by the sidebar's overflow-hidden.
function RailTooltip({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}): React.ReactElement {
  return (
    <BaseTooltip.Root>
      <BaseTooltip.Trigger render={<div />}>{children}</BaseTooltip.Trigger>
      <BaseTooltip.Portal>
        <BaseTooltip.Positioner side="right" sideOffset={8}>
          <BaseTooltip.Popup className="z-50 w-fit rounded-md bg-foreground px-2.5 py-1 text-xs text-background shadow-md whitespace-nowrap">
            {label}
          </BaseTooltip.Popup>
        </BaseTooltip.Positioner>
      </BaseTooltip.Portal>
    </BaseTooltip.Root>
  )
}

interface WorkspaceNavEntry {
  key: string
  labelKey: 'skills' | 'mcp' | 'artifacts' | 'scheduledTasks' | 'settings' | 'triggers'
  icon: LucideIcon | React.ComponentType<{ className?: string }>
  href: string
  isActive: boolean
}

function WorkspaceNav({
  wsId,
  collapsed,
}: {
  wsId: string
  collapsed?: boolean
}): React.ReactElement {
  const tSidebar = useTranslations('sidebar')
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const settingsPrefix = `/w/${wsId}/settings`
  const scheduledTasksPrefix = `/w/${wsId}/scheduled-tasks`
  const triggersPrefix = `/w/${wsId}/triggers`
  const skillsPrefix = `/w/${wsId}/skills`
  const mcpPrefix = `/w/${wsId}/mcp`
  const artifactsPrefix = `/w/${wsId}/artifacts`
  const onSettings = pathname?.startsWith(settingsPrefix) ?? false
  const onScheduledTasks = pathname?.startsWith(scheduledTasksPrefix) ?? false
  const onTriggers = pathname?.startsWith(triggersPrefix) ?? false
  const onSkills = pathname?.startsWith(skillsPrefix) ?? false
  const onMcp = pathname?.startsWith(mcpPrefix) ?? false
  const onArtifacts = pathname?.startsWith(artifactsPrefix) ?? false
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
      icon: VscMcp,
      href: mcpPrefix,
      isActive: onMcp,
    },
    {
      key: 'artifacts',
      labelKey: 'artifacts',
      icon: Package,
      href: artifactsPrefix,
      isActive: onArtifacts,
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
  ]
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
        const label = tSidebar(entry.labelKey)
        const link = (
          <Link
            href={entry.href}
            className={cn(
              'relative flex items-center px-2 py-1.5 rounded text-xs transition-colors duration-fast',
              collapsed ? 'justify-center' : 'gap-2',
              entry.isActive
                ? 'text-foreground bg-accent font-medium'
                : 'text-muted-foreground hover:text-foreground hover:bg-accent',
            )}
            aria-label={label}
          >
            {entry.isActive && (
              <div className="absolute left-0 top-[22%] bottom-[22%] w-0.5 bg-primary rounded-r" />
            )}
            <Icon className="size-3.5 shrink-0" />
            {!collapsed && <span className="whitespace-nowrap">{label}</span>}
          </Link>
        )
        return (
          <div key={entry.key}>
            {collapsed ? <RailTooltip label={label}>{link}</RailTooltip> : link}
          </div>
        )
      })}
    </nav>
  )
}

type MixedEntry =
  | { kind: 'conversation'; conversation: Conversation; sortKey: number }
  | { kind: 'group-chat'; conversation: Conversation; sortKey: number }
  | { kind: 'topic'; topic: Topic; conversations: Conversation[]; sortKey: number }

function buildMixedList(
  topics: Topic[],
  conversations: Conversation[],
  topicConversations: Record<string, Conversation[]>,
): MixedEntry[] {
  const ts = (iso: string): number => {
    const t = new Date(iso).getTime()
    return Number.isNaN(t) ? 0 : t
  }

  // A conversation with a topic_id belongs under that topic (pinned or not —
  // pinning sorts it first *within* the topic). Only topicless conversations
  // go in the flat list, where pinned ones float to the top.
  const byTopic = new Map<string, Conversation[]>()
  const flat: Conversation[] = []
  for (const c of conversations) {
    if (c.topic_id) {
      const list = byTopic.get(c.topic_id) ?? []
      list.push(c)
      byTopic.set(c.topic_id, list)
    } else {
      flat.push(c)
    }
  }

  const entries: MixedEntry[] = []
  for (const c of flat) {
    const kind = c.is_group_chat ? 'group-chat' : 'conversation'
    entries.push({ kind, conversation: c, sortKey: ts(c.updated_at) })
  }
  for (const topic of topics) {
    // Merge the full per-topic list (from the topic-detail endpoint, no limit)
    // with the window subset (from the limited flat list). Dedup by id,
    // preferring the window copy since it carries live updates (new messages,
    // pin toggles). This decouples a topic's conversations from the flat
    // list's limit, so old conversations under a topic aren't truncated.
    const merged = new Map<string, Conversation>()
    for (const c of topicConversations[topic.id] ?? []) merged.set(c.id, c)
    for (const c of byTopic.get(topic.id) ?? []) merged.set(c.id, c)
    const convs = [...merged.values()].sort((a, b) => {
      if (a.is_pinned !== b.is_pinned) return a.is_pinned ? -1 : 1
      return ts(b.updated_at) - ts(a.updated_at)
    })
    // last_activity_at bumps on every message; updated_at only on metadata
    // edits. Without this, topics freeze in place after the first message.
    const newest = convs.reduce((m, c) => Math.max(m, ts(c.updated_at)), 0)
    const sortKey = Math.max(ts(topic.last_activity_at), newest)
    entries.push({ kind: 'topic', topic, conversations: convs, sortKey })
  }
  // Pinned entries float to the top, sorted among themselves by sortKey;
  // unpinned follow with the same sort.
  entries.sort((a, b) => {
    const aPinned = a.kind === 'topic' ? a.topic.is_pinned : a.conversation.is_pinned
    const bPinned = b.kind === 'topic' ? b.topic.is_pinned : b.conversation.is_pinned
    if (aPinned !== bPinned) return aPinned ? -1 : 1
    return b.sortKey - a.sortKey
  })
  return entries
}

interface SidebarProps {
  onCollapse?: () => void
  onExpand?: () => void
  collapsed?: boolean
}

export function Sidebar({ onCollapse, onExpand, collapsed }: SidebarProps): React.ReactElement {
  const tSidebar = useTranslations('sidebar')
  const tShell = useTranslations('shellLayout')
  const t = useTranslations('topics')
  const { conversations, activeId } = useConversationStore()
  const { topics, topicConversations } = useTopicStore()
  const pathname = usePathname()
  const [groupDialogOpen, setGroupDialogOpen] = useState(false)

  // Current workspace inferred from URL (no WorkspaceContext dependency).
  const wsMatch = pathname?.match(/^\/w\/([^/]+)/)
  const currentWsId = wsMatch ? wsMatch[1] : null
  const newChatHref = currentWsId ? `/w/${currentWsId}` : '/'

  // Build a mixed list: standalone conversations (no topic_id) and topics with
  // their grouped conversations, ordered by most-recent activity in the group.
  const mixedList = buildMixedList(topics, conversations, topicConversations)

  return (
    <aside
      aria-label={tShell('sidebar')}
      className={cn(
        'bg-card border-r border-border flex flex-col h-full shrink-0 overflow-hidden',
        'transition-[width] duration-200 ease-in-out',
        collapsed ? 'w-12' : 'w-56',
      )}
    >
      {/* Brand — shows logo + wordmark + collapse button when expanded;
          logo only (centered) when collapsed. */}
      <div
        className={cn('border-b border-border', collapsed ? 'px-2 pt-3 pb-2.5' : 'px-3 pt-4 pb-3')}
      >
        <div className={cn('flex items-center mb-3', collapsed ? 'justify-center' : 'px-0.5')}>
          <div className={cn('flex items-center gap-2 min-w-0', !collapsed && 'flex-1')}>
            <CubePlexLogo
              markClassName="size-6"
              wordmarkClassName={cn(
                'text-sm whitespace-nowrap overflow-hidden transition-all duration-200',
                collapsed ? 'max-w-0 opacity-0' : 'max-w-full opacity-100',
              )}
            />
          </div>
          {/* Collapse button — desktop only (onCollapse provided). In the
              mobile drawer there's no collapse handler and the Sheet renders
              its own close X, so showing this would be a dead, overlapping icon. */}
          {!collapsed && onCollapse && (
            <button
              type="button"
              onClick={onCollapse}
              className="grid size-6 place-items-center rounded text-muted-foreground hover:bg-accent transition-colors duration-fast shrink-0"
              aria-label={tSidebar('collapseSidebar')}
            >
              <PanelLeftClose className="size-3.5" />
            </button>
          )}
        </div>
        {/* WorkspaceSelector fades out before width transition completes */}
        <div
          className={cn(
            'overflow-hidden transition-all duration-150',
            collapsed ? 'max-h-0 opacity-0' : 'max-h-24 opacity-100',
          )}
        >
          <WorkspaceSelector />
        </div>
      </div>

      {/* Expand button — rail mode only, same px-2 structure as nav items */}
      {collapsed && (
        <div className="px-2 pt-1.5 pb-0.5">
          <RailTooltip label={tSidebar('expandSidebar')}>
            <button
              type="button"
              onClick={onExpand}
              className="flex items-center justify-center w-full py-1.5 rounded text-muted-foreground hover:text-foreground hover:bg-accent transition-colors duration-fast"
              aria-label={tSidebar('expandSidebar')}
            >
              <PanelLeftOpen className="size-3.5" />
            </button>
          </RailTooltip>
        </div>
      )}

      {/* Primary actions: new chat + new group chat + search */}
      <div className="px-2 pt-1.5 pb-1 space-y-0.5">
        {/* New chat */}
        {(() => {
          const newChatLink = (
            <Link
              href={newChatHref}
              className={cn(
                'flex items-center px-2 py-1.5 rounded transition-colors duration-fast text-xs text-muted-foreground hover:text-foreground hover:bg-accent',
                collapsed ? 'justify-center' : 'gap-2',
              )}
            >
              <SquarePen className="size-3.5 shrink-0" />
              {!collapsed && <span className="whitespace-nowrap">{tSidebar('newChat')}</span>}
            </Link>
          )
          return collapsed ? (
            <RailTooltip label={tSidebar('newChat')}>{newChatLink}</RailTooltip>
          ) : (
            newChatLink
          )
        })()}
        {/* New group chat */}
        {currentWsId &&
          (() => {
            const newGroupBtn = (
              <button
                type="button"
                onClick={() => setGroupDialogOpen(true)}
                className={cn(
                  'flex w-full items-center px-2 py-1.5 rounded transition-colors duration-fast text-xs text-muted-foreground hover:text-foreground hover:bg-accent',
                  collapsed ? 'justify-center' : 'gap-2',
                )}
                aria-label={t('newTopic')}
              >
                <Layers className="size-3.5 shrink-0" />
                {!collapsed && <span className="whitespace-nowrap">{t('newTopic')}</span>}
              </button>
            )
            return collapsed ? (
              <RailTooltip label={t('newTopic')}>{newGroupBtn}</RailTooltip>
            ) : (
              newGroupBtn
            )
          })()}
        {/* Search */}
        {collapsed ? (
          <RailTooltip label={tSidebar('search.open')}>
            <ConversationSearch wsId={currentWsId} railItem />
          </RailTooltip>
        ) : (
          <ConversationSearch wsId={currentWsId} listItem />
        )}
      </div>

      {/* Workspace nav */}
      {currentWsId && (
        <Suspense>
          <WorkspaceNav wsId={currentWsId} collapsed={collapsed} />
        </Suspense>
      )}

      {/* Recent conversations — flex-1 when expanded, hidden in rail */}
      <div
        className={cn(
          'flex flex-col min-h-0 overflow-hidden transition-all duration-150',
          collapsed ? 'max-h-0 opacity-0 flex-none' : 'flex-1 opacity-100',
        )}
      >
        <div className="px-2 pt-2 pb-1">
          <p className="px-2 text-2xs font-medium uppercase tracking-wider text-faint">
            {tSidebar('recentChats')}
          </p>
        </div>
        <ScrollArea className="flex-1 px-2">
          {mixedList.length === 0 ? (
            <p className="px-2 py-1.5 text-xs text-faint">{tSidebar('noRecentChats')}</p>
          ) : (
            <ul className="space-y-0.5">
              {mixedList.map((entry) => {
                if (entry.kind === 'conversation') {
                  return (
                    <ConversationRow
                      key={`c-${entry.conversation.id}`}
                      convo={entry.conversation}
                      isActive={activeId === entry.conversation.id}
                      currentWsId={currentWsId}
                    />
                  )
                }
                if (entry.kind === 'group-chat') {
                  return (
                    <ConversationRow
                      key={`g-${entry.conversation.id}`}
                      convo={entry.conversation}
                      isActive={activeId === entry.conversation.id}
                      currentWsId={currentWsId}
                      showGroupIcon
                    />
                  )
                }
                return (
                  <TopicNode
                    key={`t-${entry.topic.id}`}
                    topic={entry.topic}
                    conversations={entry.conversations}
                    activeConvId={activeId}
                    currentWsId={currentWsId}
                    renderConversationRow={(convo) => (
                      <ConversationRow
                        key={`c-${convo.id}`}
                        convo={convo}
                        isActive={activeId === convo.id}
                        currentWsId={currentWsId}
                      />
                    )}
                  />
                )
              })}
            </ul>
          )}
        </ScrollArea>
      </div>

      {/* Footer: avatar */}
      <div className="mt-auto border-t border-border p-2">
        <AvatarPopover collapsed={collapsed} />
      </div>

      {currentWsId && (
        <CreateGroupChatDialog
          wsId={currentWsId}
          open={groupDialogOpen}
          onOpenChange={setGroupDialogOpen}
        />
      )}
    </aside>
  )
}
