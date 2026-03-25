'use client'

import { useEffect } from 'react'
import { useMessageStore, createApiClient } from '@cubebox/core'
import { UserMessage } from './UserMessage'
import { AssistantMessage } from './AssistantMessage'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useMessages } from '@/hooks/useMessages'

interface MessageListProps {
  conversationId: string
}

export function MessageList({ conversationId }: MessageListProps) {
  const { messages, streamingEvents, isStreaming } = useMessages(conversationId)
  const { fetchHistory } = useMessageStore()

  useEffect(() => {
    const client = createApiClient('')
    fetchHistory(client, conversationId)
  }, [conversationId, fetchHistory])

  return (
    <ScrollArea className="flex-1 p-4">
      <div className="space-y-4 max-w-2xl mx-auto">
        {messages.map((msg) => (
          <div key={msg.id}>
            {msg.role === 'user' && <UserMessage content={msg.content ?? ''} />}
            {msg.role === 'assistant' && <AssistantMessage message={msg} />}
          </div>
        ))}
        {isStreaming && (
          <AssistantMessage streamingEvents={streamingEvents} isStreaming={true} />
        )}
      </div>
    </ScrollArea>
  )
}
