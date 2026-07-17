'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { createApiClient, useTopicStore, type TopicParticipant } from '@cubeplex/core'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { AvatarStack } from '@/components/ui/avatar-stack'
import { cn } from '@/lib/utils'
import { MemberPanel } from '@/components/chat/MemberPanel'

interface ChatHeaderGroupBadgeProps {
  wsId: string
  topicId: string
}

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
        <MemberPanel wsId={wsId} topicId={topicId} onClose={() => setOpen(false)} />
      </PopoverContent>
    </Popover>
  )
}
