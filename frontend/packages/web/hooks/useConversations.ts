'use client'

import { useConversationStore } from '@cubeplex/core'

export function useConversations() {
  const conversations = useConversationStore((s) => s.conversations)
  const activeId = useConversationStore((s) => s.activeId)
  const fetchList = useConversationStore((s) => s.fetchList)

  return { conversations, activeId, fetchList }
}
