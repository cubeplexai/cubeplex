'use client'

import { ListTodo } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { usePanelStore } from '@cubeplex/core'
import { BackgroundTasks } from '@/components/layout/BackgroundTasks'
import { ScrollArea } from '@/components/ui/scroll-area'
import { PanelHeader } from './PanelHeader'

export function BackgroundTaskPanel({ conversationId }: { conversationId: string }) {
  const t = useTranslations('backgroundTasks')
  const close = usePanelStore((state) => state.close)
  return (
    <div className="flex h-full flex-col bg-background">
      <PanelHeader
        source={{
          kind: 'plain',
          icon: <ListTodo className="size-4" aria-hidden />,
          title: t('title'),
        }}
        onClose={close}
      />
      <ScrollArea className="flex-1">
        <BackgroundTasks conversationId={conversationId} />
      </ScrollArea>
    </div>
  )
}
