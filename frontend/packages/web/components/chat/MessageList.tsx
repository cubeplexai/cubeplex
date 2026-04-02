'use client'

import { useEffect, useMemo } from 'react'
import { useMessageStore, createApiClient } from '@cubebox/core'
import type { Message, SubagentSummary } from '@cubebox/core'
import { AlertCircle } from 'lucide-react'
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

/** Build toolResultMap from historical tool messages so panel works after refresh. */
function buildHistoricalToolResultMap(
  messages: Message[],
): Record<string, { content: string; receivedAt: number; startedAt?: number; contentType?: string }> {
  const map: Record<string, {
    content: string; receivedAt: number; startedAt?: number; contentType?: string
  }> = {}
  // Build a map of tool_call_id → assistant message created_at (= tool call start time)
  const toolCallStartMap: Record<string, number> = {}
  for (const msg of messages) {
    if (msg.role === 'assistant' && msg.tool_calls && msg.created_at) {
      const ts = new Date(msg.created_at).getTime()
      for (const tc of msg.tool_calls) {
        if (tc.tool_call_id) toolCallStartMap[tc.tool_call_id] = ts
      }
    }
  }
  for (const msg of messages) {
    if (msg.role === 'tool' && msg.tool_call_id && msg.content) {
      map[msg.tool_call_id] = {
        content: msg.content,
        receivedAt: new Date(msg.created_at ?? 0).getTime(),
        startedAt: toolCallStartMap[msg.tool_call_id],
      }
    }
  }
  return map
}

export function MessageList({ conversationId }: MessageListProps) {
  const { messages, isStreaming, statusPhase, mainStream, subAgentStreams, error, toolResultMap } =
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

  const historicalToolResults = useMemo(
    () => buildHistoricalToolResultMap(messages ?? []),
    [messages],
  )

  // Merge: streaming results take precedence over historical
  const mergedToolResultMap = useMemo(
    () => ({ ...historicalToolResults, ...toolResultMap }),
    [historicalToolResults, toolResultMap],
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
                toolResultMap={mergedToolResultMap}
              />
            )}
          </div>
        ))}

        {isStreaming && mainStream && (
          <AssistantMessage
            stream={mainStream}
            isStreaming
            statusPhase={statusPhase}
            subAgentStreams={subAgentStreams}
            toolResultMap={mergedToolResultMap}
          />
        )}

        {error && (
          <div className="flex items-start gap-2 px-3 py-2.5 rounded-lg
            bg-destructive/10 border border-destructive/20 text-destructive text-sm">
            <AlertCircle className="size-4 shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}
      </div>
    </ScrollArea>
  )
}
