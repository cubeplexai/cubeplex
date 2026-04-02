'use client'

import { useParams } from 'next/navigation'
import { useEffect } from 'react'
import { useConversationStore, useToolDetailStore, createApiClient } from '@cubebox/core'
import { AppShell } from '@/components/layout/AppShell'
import { MessageList } from '@/components/chat/MessageList'
import { InputBar } from '@/components/layout/InputBar'
import { TaskProgressBar } from '@/components/chat/TaskProgressBar'
import { useMessages } from '@/hooks/useMessages'

export default function ChatPage() {
  const params = useParams()
  const conversationId = params.id as string
  const { setActive, fetchList, conversations } = useConversationStore()
  const { todos } = useMessages(conversationId)

  useEffect(() => {
    useToolDetailStore.getState().close()
    setActive(conversationId)
    const client = createApiClient('')
    fetchList(client)
  }, [conversationId, setActive, fetchList])

  const currentConvo = conversations.find((c) => c.id === conversationId)

  return (
    <AppShell headerTitle={currentConvo?.title}>
      <MessageList conversationId={conversationId} />
      <TaskProgressBar todos={todos} />
      <div className="border-t border-border px-4 py-3 bg-background">
        <InputBar conversationId={conversationId} />
      </div>
    </AppShell>
  )
}
