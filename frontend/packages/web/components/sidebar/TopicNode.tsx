'use client'

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { cn } from '@/lib/utils'
import {
  type Conversation,
  type Topic,
  createApiClient,
  useConversationStore,
  useTopicStore,
} from '@cubebox/core'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  ChevronDown,
  ChevronRight,
  Layers,
  MoreHorizontal,
  Pin,
  PinOff,
  SquarePen,
  Trash2,
  UserPlus,
} from 'lucide-react'
import { MemberPanel } from '@/components/chat/MemberPanel'
import { AvatarStack } from '@/components/ui/avatar-stack'
import { PlatformLogo } from '@/components/im/PlatformLogo'

type ApiClient = ReturnType<typeof createApiClient>

function buildClient(currentWsId: string | null): ApiClient {
  const client = createApiClient('')
  if (currentWsId) client.setWorkspaceId(currentWsId)
  return client
}

export function TopicNode({
  topic,
  conversations,
  activeConvId,
  currentWsId,
  renderConversationRow,
}: {
  topic: Topic
  conversations: Conversation[]
  activeConvId: string | null
  currentWsId: string | null
  renderConversationRow: (convo: Conversation) => React.ReactNode
}): React.ReactElement {
  const tTopics = useTranslations('topics')
  const tSidebar = useTranslations('sidebar')
  const activeTopicId = useConversationStore((s) => s.activeTopicId)
  const [expanded, setExpanded] = useState<boolean>(
    conversations.some((c) => c.id === activeConvId) || topic.id === activeTopicId,
  )
  const router = useRouter()
  const { topicParticipants, topicConversations, fetchDetail, remove, createConversation, setPin } =
    useTopicStore()
  const fetchConversations = useConversationStore((s) => s.fetchList)
  const participants = topicParticipants[topic.id] ?? []

  // Load the topic's full conversation list once (the detail endpoint has no
  // limit, unlike the flat list). Idempotent: gated on whether we've fetched.
  const ensureDetailLoaded = useCallback((): void => {
    if (!currentWsId) return
    if (topicConversations[topic.id] !== undefined) return
    void fetchDetail(buildClient(currentWsId), topic.id).catch((err) =>
      console.error('Failed to load topic detail:', err),
    )
  }, [currentWsId, topicConversations, topic.id, fetchDetail])

  // When this topic becomes the active conversation's topic (e.g. a deep link
  // to an old conversation outside the flat list), auto-expand it. Tracked via
  // the previous activeTopicId so it fires once per switch and doesn't override
  // a later manual collapse. Adjusted during render (the React-recommended
  // alternative to setState-in-effect).
  const [prevActiveTopicId, setPrevActiveTopicId] = useState(activeTopicId)
  if (activeTopicId !== prevActiveTopicId) {
    setPrevActiveTopicId(activeTopicId)
    if (activeTopicId === topic.id) setExpanded(true)
  }

  // Load the active topic's conversations when it auto-expands. The fetch is a
  // side effect (and calls a store action, not React setState), so it lives in
  // an effect rather than the render-time block above.
  useEffect(() => {
    if (topic.id === activeTopicId) ensureDetailLoaded()
  }, [activeTopicId, topic.id, ensureDetailLoaded])
  const [creating, setCreating] = useState<boolean>(false)
  const [memberDialogOpen, setMemberDialogOpen] = useState<boolean>(false)

  const handleCreateConversation = async (e: React.MouseEvent): Promise<void> => {
    e.preventDefault()
    e.stopPropagation()
    if (creating || !currentWsId) return
    setCreating(true)
    const client = buildClient(currentWsId)
    try {
      const { conversationId } = await createConversation(client, topic.id)
      // Refresh the global conversation list so the sidebar picks up
      // the new row and the topic node renders it under itself.
      await fetchConversations(client)
      setExpanded(true)
      router.push(`/w/${currentWsId}/conversations/${conversationId}`)
    } catch (err) {
      console.error('Failed to create conversation in topic:', err)
    } finally {
      setCreating(false)
    }
  }

  const hasActiveChild = conversations.some((c) => c.id === activeConvId)
  const stateClass = hasActiveChild
    ? 'text-foreground bg-accent/60'
    : 'text-muted-foreground hover:text-foreground hover:bg-accent'

  const toggle = (): void => {
    const next = !expanded
    setExpanded(next)
    if (next) ensureDetailLoaded()
  }

  return (
    <li>
      <div
        className={cn(
          'group relative flex items-center gap-1 pl-1.5 pr-1 py-1.5 rounded',
          'transition-colors duration-fast cursor-pointer',
          stateClass,
        )}
        onClick={toggle}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            toggle()
          }
        }}
      >
        {expanded ? (
          <ChevronDown className="size-3 shrink-0" />
        ) : (
          <ChevronRight className="size-3 shrink-0" />
        )}
        {topic.is_pinned ? (
          <Pin className="size-3 shrink-0 text-primary/70 fill-primary/30" />
        ) : topic.im_platform ? (
          <PlatformLogo platform={topic.im_platform} className="size-3 shrink-0" />
        ) : (
          <Layers className="size-3 shrink-0 text-primary/70" />
        )}
        <div className="flex-1 min-w-0 truncate text-[12.5px] font-medium leading-tight">
          {topic.title || tTopics('newGroupChat')}
        </div>
        {participants.length > 0 ? (
          <AvatarStack
            items={participants.map((p) => ({
              src: p.avatar_url,
              seed: p.avatar_seed ?? p.user_id,
              name: p.display_name,
              userId: p.user_id,
            }))}
            size={16}
          />
        ) : (
          <span className="text-[10px] text-faint shrink-0">
            {tTopics('members', { count: topic.participant_count ?? 0 })}
          </span>
        )}
        <button
          type="button"
          onClick={(e) => void handleCreateConversation(e)}
          disabled={creating}
          className={cn(
            'p-1 rounded cursor-pointer hover:bg-accent text-muted-foreground hover:text-foreground',
            'shrink-0 opacity-0 group-hover:opacity-100 transition-opacity',
            'disabled:opacity-50 disabled:cursor-not-allowed',
          )}
          aria-label={tTopics('newConversation')}
          title={tTopics('newConversation')}
        >
          <SquarePen className="size-3.5" />
        </button>
        <DropdownMenu>
          <DropdownMenuTrigger
            onClick={(e) => {
              e.preventDefault()
              e.stopPropagation()
            }}
            className={cn(
              'p-1 rounded cursor-pointer hover:bg-accent text-muted-foreground hover:text-foreground',
              'shrink-0 opacity-0 group-hover:opacity-100 data-[popup-open]:opacity-100',
              'transition-opacity',
            )}
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
                // Refresh participants in the background — MemberPanel
                // also fetches on mount, but having the data primed makes
                // the dialog feel instant.
                void fetchDetail(buildClient(currentWsId), topic.id).catch((err) =>
                  console.error('Failed to load topic detail:', err),
                )
                setMemberDialogOpen(true)
              }}
            >
              <UserPlus className="size-3.5" />
              {tTopics('inviteMembers')}
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => {
                void setPin(buildClient(currentWsId), topic.id, !topic.is_pinned).catch((err) =>
                  console.error('Failed to toggle topic pin:', err),
                )
              }}
            >
              {topic.is_pinned ? <PinOff className="size-3.5" /> : <Pin className="size-3.5" />}
              {topic.is_pinned ? tSidebar('unpinConversation') : tSidebar('pinConversation')}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              variant="destructive"
              onClick={() => {
                void remove(buildClient(currentWsId), topic.id).catch((err) =>
                  console.error('Failed to delete topic:', err),
                )
              }}
            >
              <Trash2 className="size-3.5" />
              {tTopics('leaveGroup')}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {expanded && conversations.length > 0 && (
        <ul className="pl-4 mt-0.5 space-y-0.5 border-l border-border/60 ml-2.5">
          {conversations.map((convo) => renderConversationRow(convo))}
        </ul>
      )}
      {currentWsId && (
        <DialogPrimitive.Root open={memberDialogOpen} onOpenChange={setMemberDialogOpen}>
          <DialogPrimitive.Portal>
            <DialogPrimitive.Backdrop
              className={cn(
                'fixed inset-0 z-50 bg-black/40 backdrop-blur-sm',
                'data-[ending-style]:opacity-0',
                'data-[starting-style]:opacity-0',
                'transition-opacity duration-200',
              )}
            />
            <DialogPrimitive.Popup
              className={cn(
                'fixed left-1/2 top-1/2 z-50',
                'w-[min(460px,calc(100vw-32px))]',
                '-translate-x-1/2 -translate-y-1/2',
                'rounded-xl border border-border bg-popover p-3',
                'text-popover-foreground shadow-2xl',
                'data-[ending-style]:opacity-0',
                'data-[starting-style]:opacity-0',
                'transition-opacity duration-200',
              )}
            >
              <DialogPrimitive.Title className="sr-only">
                {tTopics('inviteMembers')}
              </DialogPrimitive.Title>
              <MemberPanel
                wsId={currentWsId}
                topicId={topic.id}
                onClose={() => setMemberDialogOpen(false)}
              />
            </DialogPrimitive.Popup>
          </DialogPrimitive.Portal>
        </DialogPrimitive.Root>
      )}
    </li>
  )
}
