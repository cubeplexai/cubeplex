'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { createApiClient, useConversationStore, type ConversationParticipant } from '@cubebox/core'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { cn } from '@/lib/utils'
import { ConversationMemberPanel } from '@/components/chat/ConversationMemberPanel'

interface ConversationMemberStripProps {
  wsId: string
  conversationId: string
}

const AVATAR_MAX = 3

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
    (s) => s.conversationParticipants[conversationId] ?? [],
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

  const shown = participants.slice(0, AVATAR_MAX)
  const overflow = participants.length - shown.length

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
        <div className="flex -space-x-1.5">
          {shown.map((p) => {
            const label = p.display_name || p.email || p.user_id
            return (
              <div
                key={p.id}
                title={label}
                className={cn(
                  'size-5 rounded-full bg-muted ring-1 ring-background',
                  'flex items-center justify-center text-[9px] font-medium',
                  'text-muted-foreground',
                )}
              >
                {label.slice(0, 1).toUpperCase()}
              </div>
            )
          })}
          {overflow > 0 && (
            <div
              className={cn(
                'size-5 rounded-full bg-muted ring-1 ring-background',
                'flex items-center justify-center text-[9px] font-medium',
                'text-muted-foreground',
              )}
            >
              +{overflow}
            </div>
          )}
        </div>
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
