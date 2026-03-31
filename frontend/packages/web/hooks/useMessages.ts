'use client'

import { useMessageStore } from '@cubebox/core'
import type { AgentStream } from '@cubebox/core'

export function useMessages() {
  const messages = useMessageStore((s) => s.messages) ?? []
  const isStreaming = useMessageStore((s) => s.isStreaming) ?? false
  const streamAgents = useMessageStore((s) => s.streamAgents)

  const agents = streamAgents ?? {}
  const mainStream = agents['main'] ?? null
  const subAgentStreams = Object.entries(agents).filter(([key]) => key !== 'main')

  return { messages, isStreaming, mainStream, subAgentStreams }
}
