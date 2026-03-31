'use client'

import { useMessageStore } from '@cubebox/core'
import type { AgentStream } from '@cubebox/core'

export function useMessages() {
  const messages = useMessageStore((s) => s.messages)
  const isStreaming = useMessageStore((s) => s.isStreaming)
  const streamAgents = useMessageStore((s) => s.streamAgents)

  const mainStream = streamAgents['main'] ?? null
  const subAgentStreams = Object.entries(streamAgents).filter(([key]) => key !== 'main')

  return { messages, isStreaming, mainStream, subAgentStreams }
}
