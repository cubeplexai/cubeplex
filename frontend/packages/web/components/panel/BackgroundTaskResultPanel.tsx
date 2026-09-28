'use client'

import { Terminal } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { usePanelStore } from '@cubeplex/core'
import type { BackgroundTaskEvent } from '@cubeplex/core'
import { BackgroundTaskDetails } from '@/components/chat/BackgroundTaskDetails'
import { ScrollArea } from '@/components/ui/scroll-area'
import { PanelHeader } from './PanelHeader'

export function BackgroundTaskResultPanel({
  conversationId,
  event,
}: {
  conversationId: string
  event: BackgroundTaskEvent
}) {
  const t = useTranslations('backgroundTasks')
  const close = usePanelStore((state) => state.close)

  return (
    <div className="flex h-full flex-col bg-background">
      <PanelHeader
        source={{
          kind: 'plain',
          icon: <Terminal className="size-4" aria-hidden />,
          title: t('resultLabel'),
        }}
        onClose={close}
      />
      <ScrollArea className="flex-1">
        <div className="min-w-0 space-y-4 p-4 text-xs text-muted-foreground">
          {event.summary && (
            <div>
              <p className="mb-1 font-medium">{t('executionRecord')}</p>
              <p className="break-words font-mono">{event.summary}</p>
            </div>
          )}
          <BackgroundTaskDetails conversationId={conversationId} taskId={event.task_id} />
        </div>
      </ScrollArea>
    </div>
  )
}
