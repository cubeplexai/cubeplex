'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Hash, MessageCircle, MessageSquare } from 'lucide-react'
import { createApiClient, getTopic, getConversation } from '@cubebox/core'
import type { ScheduledTaskOut } from '@cubebox/core'
import { cn } from '@/lib/utils'

interface DestinationCellProps {
  task: ScheduledTaskOut
  className?: string
}

/**
 * Compact destination indicator for the scheduled-task list/detail.
 *
 * Renders a chip per `target_mode`:
 *   - ``fixed``        → conversation title (fetched once on mount).
 *   - ``new_each_run`` → topic title chip if `topic_id` is set, otherwise a
 *                        muted "New conversation" label.
 *   - ``im_channel``   → IM-channel pill showing the channel id.
 */
export function DestinationCell({ task, className }: DestinationCellProps) {
  const t = useTranslations('scheduledTasks')
  const client = useMemo(() => createApiClient(''), [])

  const [conversationTitle, setConversationTitle] = useState<string | null>(null)
  const [topicTitle, setTopicTitle] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    if (task.target_mode === 'fixed' && task.target_conversation_id) {
      getConversation(client, task.target_conversation_id)
        .then((conv) => {
          if (!cancelled) setConversationTitle(conv.title)
        })
        .catch(() => {
          if (!cancelled) setConversationTitle(null)
        })
    } else if (task.target_mode === 'new_each_run' && task.topic_id) {
      getTopic(client, task.topic_id)
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
  }, [client, task.target_mode, task.target_conversation_id, task.topic_id])

  if (task.target_mode === 'fixed') {
    return (
      <span
        className={cn(
          'inline-flex items-center gap-1 rounded-full border border-border bg-muted/40 px-2 py-0.5 text-xs',
          className,
        )}
        title={task.target_conversation_id ?? undefined}
        data-testid="destination-fixed"
      >
        <MessageSquare className="size-3 shrink-0 text-muted-foreground" />
        <span className="max-w-[14ch] truncate">
          {conversationTitle ?? t('destinationFixedFallback')}
        </span>
      </span>
    )
  }

  if (task.target_mode === 'new_each_run') {
    if (task.topic_id) {
      return (
        <span
          className={cn(
            'inline-flex items-center gap-1 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-xs text-primary',
            className,
          )}
          title={task.topic_id}
          data-testid="destination-topic"
        >
          <MessageSquare className="size-3 shrink-0" />
          <span className="max-w-[14ch] truncate">
            {topicTitle ?? t('destinationTopicFallback')}
          </span>
        </span>
      )
    }
    return (
      <span
        className={cn('text-xs italic text-muted-foreground', className)}
        data-testid="destination-new"
      >
        {t('destinationNewConversation')}
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
      title={task.im_scope_key ?? undefined}
      data-testid="destination-im-channel"
    >
      <MessageCircle className="size-3 shrink-0" />
      <Hash className="size-3 shrink-0 -ml-0.5" />
      <span className="max-w-[18ch] truncate font-mono">{task.im_channel_id ?? '—'}</span>
    </span>
  )
}
