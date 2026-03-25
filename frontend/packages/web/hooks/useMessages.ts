'use client'

import { useMessageStore } from '@cubebox/core'

export function useMessages(conversationId: string) {
  const messages = useMessageStore((s) => s.messages[conversationId] ?? [])
  const streamingEvents = useMessageStore((s) => s.streamingEvents)
  const isStreaming = useMessageStore((s) => s.isStreaming)

  return { messages, streamingEvents, isStreaming }
}
