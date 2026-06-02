'use client'

import { useState } from 'react'
import { createApiClient, useMemoryEventStore } from '@cubebox/core'
import { cn } from '@/lib/utils'
import { Sparkle } from 'lucide-react'

interface Props {
  conversationId: string
}

export function MemoryUpdateChip({ conversationId }: Props) {
  const events = useMemoryEventStore((s) => s.byConversation[conversationId] ?? [])
  const markRead = useMemoryEventStore((s) => s.markRead)
  const [busy, setBusy] = useState(false)

  if (events.length === 0) return null

  const totalItems = events.reduce((n, e) => n + e.payload.items.length, 0)
  const verb = events.every((e) => e.payload.items.every((i) => i.op === 'update'))
    ? '已更新'
    : '已记住'

  const handleClick = async () => {
    if (busy) return
    setBusy(true)
    const client = createApiClient('')
    await Promise.all(
      events.map(async (ev) => {
        try {
          await client.post(`/api/v1/user/events/${ev.id}/read`, {})
        } catch {
          // Best-effort: server-side read_at stays null on failure, but the
          // SSE cursor (localStorage.lastSeenId) is already past these events,
          // so they won't be redelivered. Local hide is the right behavior;
          // a missed read_at is just metadata.
        }
      }),
    )
    for (const ev of events) markRead(ev.id)
    setBusy(false)
    // TODO: navigate to /memory panel filtered to these items
  }

  return (
    <div className="flex justify-center pt-2">
      <button
        type="button"
        onClick={handleClick}
        disabled={busy}
        className={cn(
          'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs',
          'bg-muted/60 text-muted-foreground hover:text-foreground hover:bg-muted',
          'transition-colors disabled:opacity-50',
        )}
        aria-label={`${verb} ${totalItems} 条记忆`}
      >
        <Sparkle aria-hidden className="size-3" />
        <span>
          {verb} {totalItems} 条记忆
        </span>
      </button>
    </div>
  )
}
