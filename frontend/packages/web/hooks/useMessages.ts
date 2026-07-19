'use client'

import { useRef } from 'react'
import { useMessageStore } from '@cubeplex/core'
import type { AgentStream, Message, MessageStore } from '@cubeplex/core'

// Stable empties returned by selectors when the asked-for slice is absent or
// inactive. Without these, every selector with a `?? []` / `?? {}` fallback
// hands back a fresh literal on each store update, marking the slice "changed"
// under Zustand's `===` and forcing the host component (MessageList) to re-render.
const EMPTY_MESSAGES: Message[] = []
const EMPTY_TOOL_RESULTS: MessageStore['toolResultMap'] = {}
const EMPTY_SUBAGENTS: Record<string, AgentStream> = {}

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
  const messages = useMessageStore((s) => s.messages[conversationId] ?? EMPTY_MESSAGES)
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
  const conversationError = useMessageStore((s) => s.errors[conversationId] ?? null)
  const toolResultMap = useMessageStore((s) =>
    s.streamingConversationId === conversationId ? s.toolResultMap : EMPTY_TOOL_RESULTS,
  )
  const turnUsage = useMessageStore((s) => s.turnUsage[conversationId] ?? null)
  const sessionUsage = useMessageStore((s) => s.sessionUsage[conversationId] ?? null)
  const contextWindow = useMessageStore((s) => s.contextWindow[conversationId] ?? null)
  const contextTokens = useMessageStore((s) => s.contextTokens[conversationId] ?? null)

  // Select the store-owned record so useSyncExternalStore always gets a stable
  // snapshot. Derive the subagent-only view after the selector has returned.
  const streamAgents = useMessageStore((s) =>
    s.streamingConversationId === conversationId ? s.streamAgents : EMPTY_SUBAGENTS,
  )
  const rawSubAgents: Record<string, AgentStream> = {}
  for (const key in streamAgents) {
    if (key !== 'main') rawSubAgents[key] = streamAgents[key]
  }
  const subAgentStreams = useStableRecord(rawSubAgents)

  return {
    messages,
    isStreaming: isStreamingThis,
    statusPhase,
    mainStream,
    subAgentStreams,
    todos,
    conversationError,
    toolResultMap,
    turnUsage,
    sessionUsage,
    contextWindow,
    contextTokens,
  }
}
