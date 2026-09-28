'use client'

import { useState } from 'react'
import { ChevronRight, Loader2, Terminal } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { createApiClient, useMessageStore } from '@cubeplex/core'
import type { BackgroundTaskEvent } from '@cubeplex/core'
import { toast } from 'sonner'

import { BackgroundTaskDetails } from './BackgroundTaskDetails'
import { Button } from '@/components/ui/button'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'

interface BackgroundTaskEventsProps {
  conversationId: string
}

export function BackgroundTaskEventItem({
  event,
  conversationId,
}: {
  event: BackgroundTaskEvent
  conversationId: string
}) {
  const t = useTranslations('backgroundTasks')
  const [expanded, setExpanded] = useState(false)
  const task = useMessageStore((state) =>
    state.backgroundTasks?.[conversationId]?.find(
      (task) =>
        task.id === event.task_id && task.execution_generation === event.execution_generation,
    ),
  )
  return (
    <details
      onToggle={(e) => {
        if (e.target === e.currentTarget) setExpanded(e.currentTarget.open)
      }}
      className="group/background-result min-w-0 text-xs"
    >
      <summary
        className="flex w-full max-w-full cursor-pointer list-none items-center gap-2
          rounded-lg border border-transparent px-2 py-1 text-left leading-5
          text-muted-foreground transition-colors hover:border-border/60 hover:bg-muted/55
          hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Terminal aria-hidden className="size-3.5 shrink-0 opacity-70" />
        <span className="min-w-0 flex-1 truncate" title={task?.description || t('resultLabel')}>
          <span className="font-medium text-foreground/90">{t('resultLabel')}</span>
          {task?.description && (
            <span className="ml-2 text-muted-foreground">{task.description}</span>
          )}
        </span>
        <span className="shrink-0 text-2xs text-muted-foreground">
          {t(`events.${event.state}`)}
        </span>
        <ChevronRight
          aria-hidden
          className="size-3 shrink-0 group-open/background-result:rotate-90"
        />
      </summary>
      <div
        className="ml-3 mt-2 min-w-0 space-y-3 border-l border-border pl-3
          text-muted-foreground"
      >
        {expanded && event.summary && (
          <div>
            <p className="mb-1 font-medium">{t('executionRecord')}</p>
            <p className="break-words font-mono">{event.summary}</p>
          </div>
        )}
        {expanded && (
          <BackgroundTaskDetails conversationId={conversationId} taskId={event.task_id} />
        )}
      </div>
    </details>
  )
}

export function BackgroundTaskEventsLoadMore({ conversationId }: BackgroundTaskEventsProps) {
  const t = useTranslations('backgroundTasks')
  const hasMore = useMessageStore(
    (state) => state.backgroundEventsHasMore?.[conversationId] ?? false,
  )
  const loadMore = useMessageStore((state) => state.loadMoreBackgroundEvents)
  const { workspaceId } = useWorkspaceContext()
  const [loadingMore, setLoadingMore] = useState(false)
  if (!hasMore) return null

  const onLoadMore = async () => {
    if (!loadMore) return
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    setLoadingMore(true)
    try {
      await loadMore(client, conversationId)
    } catch {
      toast.error(t('loadMoreFailed'))
    } finally {
      setLoadingMore(false)
    }
  }

  return (
    <div className="flex justify-center">
      <Button
        type="button"
        variant="ghost"
        size="xs"
        disabled={loadingMore}
        onClick={() => void onLoadMore()}
      >
        {loadingMore ? <Loader2 className="size-3 animate-spin" /> : null}
        {t('loadOlderResults')}
      </Button>
    </div>
  )
}
