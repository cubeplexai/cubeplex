'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import {
  type Conversation,
  type Topic,
  type TopicParticipant,
  createApiClient,
  useTopicStore,
} from '@cubebox/core'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ChevronDown, ChevronRight, MoreHorizontal, Trash2, UserPlus, Users } from 'lucide-react'

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
          title={p.user_id}
        >
          {p.user_id.slice(0, 1).toUpperCase()}
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
  const { topicParticipants, fetchDetail, remove } = useTopicStore()
  const participants = topicParticipants[topic.id] ?? []

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
            {tTopics('members', { count: 0 })}
          </span>
        )}
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
                void fetchDetail(buildClient(currentWsId), topic.id).catch((err) =>
                  console.error('Failed to load topic detail:', err),
                )
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
    </li>
  )
}
