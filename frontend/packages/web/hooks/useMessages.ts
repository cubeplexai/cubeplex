'use client'

import { useMemo, useRef } from 'react'
import { useMessageStore } from '@cubebox/core'
import type { AgentStream } from '@cubebox/core'

/**
 * Shallow-compare two Record<string, T> by keys and reference-equal values.
 * Returns the previous reference if nothing changed, avoiding unnecessary re-renders.
 */
function useStableRecord<T>(
  record: Record<string, T>,
): Record<string, T> {
  const prev = useRef(record)
  const prevKeys = Object.keys(prev.current)
  const nextKeys = Object.keys(record)
  if (
    prevKeys.length === nextKeys.length &&
    nextKeys.every(
      (k) => prev.current[k] === record[k],
    )
  ) {
    return prev.current
  }
  prev.current = record
  return record
}

export function useMessages(conversationId: string) {
  const messages = useMessageStore(
    (s) => s.messages[conversationId] ?? [],
  )
  const isStreaming =
    useMessageStore((s) => s.isStreaming) ?? false
  const statusPhase =
    useMessageStore((s) => s.statusPhase)
  const mainStream = useMessageStore(
    (s) => s.streamAgents['main'] ?? null,
  )
  const todos = useMessageStore((s) => s.todos)
  const error = useMessageStore((s) => s.error)
  const toolResultMap = useMessageStore(
    (s) => s.toolResultMap,
  )

  // Derive subagent streams with stable reference
  const rawSubAgents = useMessageStore((s) => {
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
    isStreaming,
    statusPhase,
    mainStream,
    subAgentStreams,
    todos,
    error,
    toolResultMap,
  }
}
