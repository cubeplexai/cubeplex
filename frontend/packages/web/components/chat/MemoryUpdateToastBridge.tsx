'use client'

import { useEffect, useRef } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import { toast } from 'sonner'
import { useMemoryEventStore } from '@cubebox/core'

export function MemoryUpdateToastBridge() {
  const events = useMemoryEventStore((s) => s.byConversation)
  const pathname = usePathname()
  const router = useRouter()
  const shownRef = useRef<Set<string>>(new Set())

  useEffect(() => {
    for (const [convId, list] of Object.entries(events)) {
      // Conversation routes live at /w/[wsId]/conversations/[id]
      const visible = pathname?.includes(`/conversations/${convId}`)
      if (visible) continue // inline chip handles it
      for (const ev of list) {
        if (shownRef.current.has(ev.id)) continue
        shownRef.current.add(ev.id)
        const wsId = ev.workspace_id
        const href = wsId ? `/w/${wsId}/conversations/${convId}` : null
        const count = ev.payload.items.length
        const allUpdate = ev.payload.items.every((i) => i.op === 'update')
        const verb = allUpdate ? '已更新' : '已记住'
        const msg = count === 1 ? `${verb}一条新记忆` : `${verb} ${count} 条新记忆`
        toast(msg, {
          ...(href ? { action: { label: '查看', onClick: () => router.push(href) } } : undefined),
        })
      }
    }
  }, [events, pathname, router])

  return null
}
