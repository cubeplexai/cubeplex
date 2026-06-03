'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { createApiClient, getMemoryCount, useMemoryEventStore } from '@cubebox/core'
import { cn } from '@/lib/utils'
import { Sparkle } from 'lucide-react'

interface Props {
  conversationId: string
  workspaceId: string
}

/**
 * Permanent per-conversation memory count chip.
 *
 * Data source is the backend `GET /api/v1/ws/{ws}/memory/count` query (refresh-
 * safe). The `useMemoryEventStore` SSE pipeline is used only as a refresh
 * trigger — when an event arrives for this conversation, we re-fetch the count
 * rather than incrementing locally (cheaper to keep correct than to maintain
 * dedup state).
 *
 * No mark-read on click — the chip is a count display, not an unread badge.
 * Click navigates to the memory page filtered to this conversation.
 */
export function MemoryUpdateChip({ conversationId, workspaceId }: Props) {
  const router = useRouter()
  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(workspaceId)
    return c
  }, [workspaceId])

  const [count, setCount] = useState<number | null>(null)

  const refresh = useCallback(async () => {
    try {
      const n = await getMemoryCount(client, { source_conversation_id: conversationId })
      setCount(n)
    } catch {
      // best-effort: leave previous count visible
    }
  }, [client, conversationId])

  // Initial fetch + on conversation change.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setCount(null)
    void refresh()
  }, [refresh])

  // Re-fetch whenever the live SSE pipeline reports any new memory event for
  // this conversation. Subscribing to the bucket length keeps the effect from
  // re-running on unrelated store updates.
  const eventCount = useMemoryEventStore((s) => (s.byConversation[conversationId] ?? []).length)
  useEffect(() => {
    if (eventCount === 0) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh()
  }, [eventCount, refresh])

  if (count === null || count === 0) return null

  const handleClick = () => {
    router.push(`/w/${workspaceId}/memory?conversation=${conversationId}`)
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs',
        'bg-muted/60 text-muted-foreground hover:text-foreground hover:bg-muted',
        'transition-colors',
      )}
      aria-label={`${count} 条记忆 · 查看`}
    >
      <Sparkle aria-hidden className="size-3" />
      <span>{count} 条记忆 · 查看</span>
    </button>
  )
}
