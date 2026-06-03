'use client'

import { useRef } from 'react'
import { useMessageStore } from '@cubebox/core'
import type { AgentStream } from '@cubebox/core'

/**
 * Shallow-compare two Record<string, T> by keys and reference-equal values.
 * Returns the previous reference if nothing changed, avoiding unnecessary re-renders.
 */
function useStableRecord<T>(record: Record<string, T>): Record<string, T> {
  const prev = useRef(record)
  // eslint-disable-next-line react-hooks/refs
  const prevKeys = Object.keys(prev.current)
  const nextKeys = Object.keys(record)
  // eslint-disable-next-line react-hooks/refs
  if (prevKeys.length === nextKeys.length && nextKeys.every((k) => prev.current[k] === record[k])) {
    // eslint-disable-next-line react-hooks/refs
    return prev.current
  }
  // eslint-disable-next-line react-hooks/refs
  prev.current = record
  return record
}

export function useMessages(conversationId: string) {
  const messages = useMessageStore((s) => s.messages[conversationId] ?? [])
  // Only expose streaming state when this conversation is the one streaming
  const isStreamingThis = useMessageStore(
    (s) => s.isStreaming && s.streamingConversationId === conversationId,
  )
  const statusPhase = useMessageStore((s) =>
    s.streamingConversationId === conversationId ? s.statusPhase : null,
  )
  const mainStream = useMessageStore((s) =>
    s.streamingConversationId === conversationId ? (s.streamAgents['main'] ?? null) : null,
  )
  const todos = useMessageStore((s) => s.todos)
  const error = useMessageStore((s) => s.error)
  const toolResultMap = useMessageStore((s) =>
    s.streamingConversationId === conversationId ? s.toolResultMap : {},
  )
  const turnUsage = useMessageStore((s) => s.turnUsage[conversationId] ?? null)
  const sessionUsage = useMessageStore((s) => s.sessionUsage[conversationId] ?? null)
  const contextWindow = useMessageStore((s) => s.contextWindow[conversationId] ?? null)
  const contextTokens = useMessageStore((s) => s.contextTokens[conversationId] ?? null)

  // Derive subagent streams with stable reference — only for the streaming conversation
  const rawSubAgents = useMessageStore((s) => {
    if (s.streamingConversationId !== conversationId) return {}
    const agents = s.streamAgents
    const sub: Record<string, AgentStream> = {}
    for (const key in agents) {
      if (key !== 'main') sub[key] = agents[key]
    }
    return sub
  })
  const subAgentStreams = useStableRecord(rawSubAgents)

  return {
    messages,
    isStreaming: isStreamingThis,
    statusPhase,
    mainStream,
    subAgentStreams,
    todos,
    error,
    toolResultMap,
    turnUsage,
    sessionUsage,
    contextWindow,
    contextTokens,
  }
}
