'use client'

import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { createApiClient, useMessageStore } from '@cubeplex/core'
import type { BackgroundTaskEvent } from '@cubeplex/core'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'

interface BackgroundTaskEventsProps {
  conversationId: string
}

const EMPTY_BACKGROUND_EVENTS: BackgroundTaskEvent[] = []

export function BackgroundTaskEvents({ conversationId }: BackgroundTaskEventsProps) {
  const t = useTranslations('backgroundTasks')
  const events = useMessageStore(
    (state) => state.backgroundEvents?.[conversationId] ?? EMPTY_BACKGROUND_EVENTS,
  )
  const hasMore = useMessageStore(
    (state) => state.backgroundEventsHasMore?.[conversationId] ?? false,
  )
  const loadMore = useMessageStore((state) => state.loadMoreBackgroundEvents)
  const { workspaceId } = useWorkspaceContext()
  const [loadingMore, setLoadingMore] = useState(false)
  if (events.length === 0) return null

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
    <div className="space-y-2" aria-label={t('results')}>
      {[...events]
        .sort(
          (left, right) =>
            left.created_at.localeCompare(right.created_at) || left.id.localeCompare(right.id),
        )
        .map((event) => (
          <details
            key={event.id}
            className="rounded-lg border border-border/70 bg-muted/20 px-3 py-2 text-sm"
          >
            <summary className="cursor-pointer list-none text-muted-foreground">
              <span className="font-medium text-foreground">{t('resultLabel')}</span>
              <span className="mx-1.5">·</span>
              <span>{t(`events.${event.state}`)}</span>
              {event.summary ? <span className="ml-2">{event.summary}</span> : null}
            </summary>
            <div className="mt-2 border-t border-border/60 pt-2 text-xs text-muted-foreground">
              {event.result_ref ? (
                <pre className="overflow-x-auto whitespace-pre-wrap break-all font-mono">
                  {event.result_ref}
                </pre>
              ) : (
                <span>{event.summary || t('noResultDetails')}</span>
              )}
            </div>
          </details>
        ))}
      {hasMore ? (
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
      ) : null}
    </div>
  )
}
