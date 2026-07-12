'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Hash, MessageCircle, MessageSquare } from 'lucide-react'
import { createApiClient, getTopic } from '@cubeplex/core'
import type { Trigger } from '@cubeplex/core'
import { cn } from '@/lib/utils'
import { topicDisplayTitle } from '@/lib/topicTitle'

interface DestinationCellProps {
  /** Workspace for resolving /api/v1/topics → workspace-scoped path. */
  wsId: string
  trigger: Trigger
  className?: string
}

/**
 * Compact destination indicator for a trigger row/detail.
 *
 * Triggers have only two destination shapes:
 *   - ``new_each_time``  → topic chip (if topic_id set) or "New conversation"
 *   - ``im_channel``     → IM-channel pill showing the channel id
 *
 * Trigger destinations are immutable once created (see ``UpdateTriggerBody``
 * in @cubeplex/core); we never have to keep this cell in sync with form edits.
 */
export function DestinationCell({ wsId, trigger, className }: DestinationCellProps) {
  const t = useTranslations('triggers')
  const tTopics = useTranslations('topics')
  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  const [topicTitle, setTopicTitle] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    if (trigger.conversation_policy === 'new_each_time' && trigger.topic_id) {
      getTopic(client, trigger.topic_id)
        .then((data) => {
          if (!cancelled) setTopicTitle(data.topic.title)
        })
        .catch(() => {
          if (!cancelled) setTopicTitle(null)
        })
    }
    return () => {
      cancelled = true
    }
  }, [client, trigger.conversation_policy, trigger.topic_id])

  if (trigger.conversation_policy === 'new_each_time') {
    if (trigger.topic_id) {
      return (
        <span
          className={cn(
            'inline-flex items-center gap-1 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-xs text-primary',
            className,
          )}
          title={trigger.topic_id}
          data-testid="trigger-destination-topic"
        >
          <MessageSquare className="size-3 shrink-0" />
          <span className="max-w-[14ch] truncate">
            {topicTitle === null
              ? t('destTopicFallback')
              : topicDisplayTitle(topicTitle, tTopics('newGroupChat'))}
          </span>
        </span>
      )
    }
    return (
      <span
        className={cn('text-xs italic text-muted-foreground', className)}
        data-testid="trigger-destination-new"
      >
        {t('destNewConversation')}
      </span>
    )
  }

  // im_channel
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border border-warning-border bg-warning-surface px-2 py-0.5 text-xs text-warning-fg',
        className,
      )}
      title={trigger.im_scope_key ?? undefined}
      data-testid="trigger-destination-im-channel"
    >
      <MessageCircle className="size-3 shrink-0" />
      <Hash className="size-3 shrink-0 -ml-0.5" />
      <span className="max-w-[18ch] truncate font-mono">{trigger.im_channel_id ?? '—'}</span>
    </span>
  )
}
