'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { cn } from '@/lib/utils'
import {
  type Conversation,
  type Topic,
  type TopicParticipant,
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
  MoreHorizontal,
  Plus,
  Trash2,
  UserPlus,
  Users,
} from 'lucide-react'
import { MemberPanel } from '@/components/chat/MemberPanel'

type ApiClient = ReturnType<typeof createApiClient>

function buildClient(currentWsId: string | null): ApiClient {
  const client = createApiClient('')
  if (currentWsId) client.setWorkspaceId(currentWsId)
  return client
}

function ParticipantAvatars({
  participants,
  max = 3,
}: {
  participants: TopicParticipant[]
  max?: number
}): React.ReactElement {
  const shown = participants.slice(0, max)
  const overflow = participants.length - shown.length
  return (
    <div className="flex -space-x-1.5 shrink-0">
      {shown.map((p) => (
        <div
          key={p.id}
          className={cn(
            'size-4 rounded-full bg-muted ring-1 ring-card',
            'flex items-center justify-center text-[8px] font-medium text-muted-foreground',
          )}
          title={p.display_name || p.email || p.user_id}
        >
          {(p.display_name || p.email || p.user_id).slice(0, 1).toUpperCase()}
        </div>
      ))}
      {overflow > 0 && (
        <div
          className={cn(
            'size-4 rounded-full bg-muted ring-1 ring-card',
            'flex items-center justify-center text-[8px] font-medium text-muted-foreground',
          )}
        >
          +{overflow}
        </div>
      )}
    </div>
  )
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
  const [expanded, setExpanded] = useState<boolean>(
    conversations.some((c) => c.id === activeConvId),
  )
  const router = useRouter()
  const { topicParticipants, fetchDetail, remove, createConversation } = useTopicStore()
  const fetchConversations = useConversationStore((s) => s.fetchList)
  const participants = topicParticipants[topic.id] ?? []
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
    if (next && participants.length === 0) {
      void fetchDetail(buildClient(currentWsId), topic.id).catch((err) =>
        console.error('Failed to load topic detail:', err),
      )
    }
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
        <Users className="size-3 shrink-0 text-primary/70" />
        <div className="flex-1 min-w-0 truncate text-[12.5px] font-medium leading-tight">
          {topic.title || tTopics('newGroupChat')}
        </div>
        {participants.length > 0 ? (
          <ParticipantAvatars participants={participants} />
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
            'p-1 rounded hover:bg-accent text-muted-foreground hover:text-foreground',
            'shrink-0 opacity-0 group-hover:opacity-100 transition-opacity',
            'disabled:opacity-50',
          )}
          aria-label={tTopics('newConversation')}
          title={tTopics('newConversation')}
        >
          <Plus className="size-3.5" />
        </button>
        <DropdownMenu>
          <DropdownMenuTrigger
            onClick={(e) => {
              e.preventDefault()
              e.stopPropagation()
            }}
            className={cn(
              'p-1 rounded hover:bg-accent text-muted-foreground hover:text-foreground',
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
