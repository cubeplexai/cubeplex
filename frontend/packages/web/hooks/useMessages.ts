'use client'

import { useMessageStore } from '@cubebox/core'

export function useMessages(conversationId: string) {
  const messages = useMessageStore((s) => s.messages[conversationId] ?? [])
  const streamingEvents = useMessageStore((s) => s.streamingEvents[conversationId] ?? [])
  const isStreaming = useMessageStore((s) => s.streamingConversationId === conversationId)

  return { messages, streamingEvents, isStreaming }
}
