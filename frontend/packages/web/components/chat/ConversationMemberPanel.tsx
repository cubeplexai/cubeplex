'use client'

import { useMemo } from 'react'
import { useTranslations } from 'next-intl'
import { useConversationStore, type ConversationParticipant } from '@cubebox/core'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Avatar } from '@/components/ui/avatar-resolved'
import { cn } from '@/lib/utils'

interface ConversationMemberPanelProps {
  wsId: string
  conversationId: string
  onClose: () => void
}

export function ConversationMemberPanel({
  conversationId,
}: ConversationMemberPanelProps): React.ReactElement {
  const t = useTranslations('conversation.members')
  const participants: ConversationParticipant[] = useConversationStore(
    (s) => s.conversationParticipants[conversationId] ?? [],
  )
  const ordered = useMemo(
    () =>
      [...participants].sort((a, b) => {
        const an = (a.display_name || a.email || a.user_id).toLowerCase()
        const bn = (b.display_name || b.email || b.user_id).toLowerCase()
        return an.localeCompare(bn)
      }),
    [participants],
  )

  return (
    <div className="flex flex-col gap-2 w-64" data-testid="conversation-member-panel">
      <div className="text-sm font-medium">{t('title', { count: participants.length })}</div>
      <ScrollArea className="max-h-72">
        <ul className="flex flex-col gap-0.5">
          {ordered.map((p) => {
            const name = p.display_name || p.email || p.user_id
            return (
              <li
                key={p.id}
                className={cn(
                  'flex items-center gap-2 rounded-md px-1.5 py-1.5 text-xs',
                  'hover:bg-accent/40',
                )}
              >
                <Avatar
                  src={p.avatar_url}
                  seed={p.avatar_seed ?? p.user_id}
                  name={name}
                  userId={p.user_id}
                  size="sm"
                />
                <div className="flex-1 min-w-0 truncate">{name}</div>
                <span
                  className={cn(
                    'shrink-0 rounded bg-muted px-1.5 py-0.5',
                    'text-[10px] font-medium text-muted-foreground',
                  )}
                >
                  {t('participantTag')}
                </span>
              </li>
            )
          })}
        </ul>
      </ScrollArea>
    </div>
  )
}
