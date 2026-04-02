'use client'

import { useMessageStore } from '@cubebox/core'

export function useMessages(conversationId: string) {
  const messagesMap =
    useMessageStore((s) => s.messages) ?? {}
  const messages = messagesMap[conversationId] ?? []
  const isStreaming =
    useMessageStore((s) => s.isStreaming) ?? false
  const statusPhase =
    useMessageStore((s) => s.statusPhase)
  const streamAgents =
    useMessageStore((s) => s.streamAgents)
  const todos = useMessageStore((s) => s.todos)
  const toolResultMap =
    useMessageStore((s) => s.toolResultMap)

  const agents = streamAgents ?? {}
  const mainStream = agents['main'] ?? null
  const subAgentStreams = Object.entries(agents).filter(
    ([key]) => key !== 'main',
  )

  return {
    messages,
    isStreaming,
    statusPhase,
    mainStream,
    subAgentStreams,
    todos,
    toolResultMap,
  }
}
