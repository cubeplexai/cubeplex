'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { createApiClient, useConversationStore, type ConversationParticipant } from '@cubeplex/core'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { AvatarStack } from '@/components/ui/avatar-stack'
import { cn } from '@/lib/utils'
import { ConversationMemberPanel } from '@/components/chat/ConversationMemberPanel'

interface ConversationMemberStripProps {
  wsId: string
  conversationId: string
}

const EMPTY_PARTICIPANTS: ConversationParticipant[] = []

export function ConversationMemberStrip({
  wsId,
  conversationId,
}: ConversationMemberStripProps): React.ReactElement | null {
  const t = useTranslations('conversation.members')
  const [open, setOpen] = useState(false)
  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])
  const fetchConversationParticipants = useConversationStore((s) => s.fetchConversationParticipants)
  const participants: ConversationParticipant[] = useConversationStore(
    (s) => s.conversationParticipants[conversationId] ?? EMPTY_PARTICIPANTS,
  )

  // Lazy fetch on mount when state is empty. The store keeps the list fresh
  // after inviteToGroup actions, so this only fires on first view.
  useEffect(() => {
    if (participants.length === 0) {
      void fetchConversationParticipants(client, conversationId).catch(() => undefined)
    }
    // Intentionally omit `participants.length` from deps — we only want the
    // first-mount fetch; subsequent updates flow through store actions.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, conversationId, fetchConversationParticipants])

  // Suppress flash of empty strip while the lazy fetch is in flight.
  if (participants.length === 0) return null

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        className={cn(
          'mr-1 flex items-center gap-2 rounded-md px-1.5 py-1',
          'text-muted-foreground hover:bg-accent hover:text-foreground',
          'transition-colors duration-fast',
        )}
        aria-label={t('openLabel')}
        title={t('openLabel')}
        data-testid="conversation-member-strip"
      >
        <AvatarStack
          items={participants.map((p) => ({
            src: p.avatar_url,
            seed: p.avatar_seed ?? p.user_id,
            name: p.display_name,
            userId: p.user_id,
          }))}
          size={20}
        />
      </PopoverTrigger>
      <PopoverContent align="end" sideOffset={6} className="w-auto p-3">
        <ConversationMemberPanel
          wsId={wsId}
          conversationId={conversationId}
          onClose={() => setOpen(false)}
        />
      </PopoverContent>
    </Popover>
  )
}
