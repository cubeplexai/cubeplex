'use client'

import { useEffect } from 'react'
import { useMessageStore, createApiClient } from '@cubebox/core'
import { UserMessage } from './UserMessage'
import { AssistantMessage } from './AssistantMessage'
import { SubAgentCard } from './SubAgentCard'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useMessages } from '@/hooks/useMessages'

interface MessageListProps {
  conversationId: string
}

export function MessageList({ conversationId }: MessageListProps) {
  const { messages, isStreaming, mainStream, subAgentStreams } = useMessages()
  const loadMessages = useMessageStore((s) => s.loadMessages)

  useEffect(() => {
    const client = createApiClient('')
    loadMessages(client, conversationId)
  }, [conversationId, loadMessages])

  return (
    <ScrollArea className="flex-1 p-4">
      <div className="space-y-4 max-w-2xl mx-auto">
        {messages.map((msg) => (
          <div key={msg.id}>
            {msg.role === 'user' && <UserMessage content={msg.content ?? ''} />}
            {msg.role === 'assistant' && <AssistantMessage message={msg} />}
          </div>
        ))}

        {isStreaming && mainStream && (
          <>
            {subAgentStreams.map(([agentId, stream]) => (
              <SubAgentCard
                key={agentId}
                agentId={agentId}
                stream={stream}
                isRunning={isStreaming}
              />
            ))}
            <AssistantMessage stream={mainStream} isStreaming />
          </>
        )}
      </div>
    </ScrollArea>
  )
}
