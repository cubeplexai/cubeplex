'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { createApiClient, useTopicStore, type TopicParticipant } from '@cubebox/core'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { cn } from '@/lib/utils'
import { MemberPanel } from '@/components/chat/MemberPanel'

interface ChatHeaderGroupBadgeProps {
  wsId: string
  topicId: string
}

const AVATAR_MAX = 3

export function ChatHeaderGroupBadge({
  wsId,
  topicId,
}: ChatHeaderGroupBadgeProps): React.ReactElement | null {
  const t = useTranslations('topics.memberPanel')
  const [open, setOpen] = useState(false)
  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])
  const fetchDetail = useTopicStore((s) => s.fetchDetail)
  const participants: TopicParticipant[] = useTopicStore((s) => s.topicParticipants[topicId] ?? [])

  // Refetch on every topic change rather than gating on participants.length:
  // a legitimately empty response (user just left) would otherwise be
  // indistinguishable from "never fetched" and lock the badge in a stale
  // state. fetchDetail is cheap (single GET) so this is fine.
  useEffect(() => {
    void fetchDetail(client, topicId).catch(() => undefined)
  }, [client, topicId, fetchDetail])

  // Group UI only renders when there are 2+ participants — otherwise the
  // conversation is effectively a 1:1 even though it has a topic_id.
  if (participants.length < 2) return null

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
        <MemberPanel wsId={wsId} topicId={topicId} onClose={() => setOpen(false)} />
      </PopoverContent>
    </Popover>
  )
}
