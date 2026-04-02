'use client'

import { useEffect, useMemo } from 'react'
import { useMessageStore, createApiClient } from '@cubebox/core'
import type { Message, SubagentSummary } from '@cubebox/core'
import { UserMessage } from './UserMessage'
import { AssistantMessage } from './AssistantMessage'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useMessages } from '@/hooks/useMessages'

interface MessageListProps {
  conversationId: string
}

/**
 * Build a map from tool_call_id -> SubagentSummary by scanning tool messages
 * that follow each assistant message.
 */
function buildSubagentDataMap(
  messages: Message[],
): Record<string, SubagentSummary> {
  const map: Record<string, SubagentSummary> = {}
  for (const msg of messages) {
    if (
      msg.role === 'tool' &&
      msg.name === 'subagent' &&
      msg.tool_call_id &&
      msg.subagent_events
    ) {
      map[`subagent:${msg.tool_call_id}`] = msg.subagent_events
    }
  }
  return map
}

export function MessageList({ conversationId }: MessageListProps) {
  const { messages, isStreaming, statusPhase, mainStream, subAgentStreams, toolResultMap } =
    useMessages(conversationId)
  const loadMessages = useMessageStore((s) => s.loadMessages)

  useEffect(() => {
    const client = createApiClient('')
    loadMessages(client, conversationId)
  }, [conversationId, loadMessages])

  const subagentDataMap = useMemo(
    () => buildSubagentDataMap(messages ?? []),
    [messages],
  )

  return (
    <ScrollArea className="flex-1 p-4">
      <div className="space-y-4 max-w-2xl mx-auto">
        {(messages ?? []).map((msg) => (
          <div key={msg.id}>
            {msg.role === 'user' && <UserMessage content={msg.content ?? ''} />}
            {msg.role === 'assistant' && (
              <AssistantMessage
                message={msg}
                subagentDataMap={subagentDataMap}
                toolResultMap={toolResultMap}
              />
            )}
          </div>
        ))}

        {isStreaming && mainStream && (
          <AssistantMessage
            stream={mainStream}
            isStreaming
            statusPhase={statusPhase}
            subAgentStreams={Object.fromEntries(subAgentStreams)}
            toolResultMap={toolResultMap}
          />
        )}
      </div>
    </ScrollArea>
  )
}
