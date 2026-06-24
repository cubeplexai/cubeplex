'use client'

import { useCallback, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { createApiClient, listMemory, type MemoryItem } from '@cubebox/core'
import { cn } from '@/lib/utils'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { ArrowUpRight, Sparkle } from 'lucide-react'
import { useMemoryCount } from '@/hooks/useMemoryCount'

interface Props {
  conversationId: string
  workspaceId: string
}

/**
 * Per-conversation memory count chip.
 *
 * Click opens a Popover with the actual memory contents (lazy-fetched on
 * open) and a button to jump to the full memory page filtered to this
 * conversation. Count comes from a refresh-safe backend query — SSE events
 * just trigger a refetch.
 */
export function MemoryUpdateChip({ conversationId, workspaceId }: Props) {
  const router = useRouter()
  const count = useMemoryCount(workspaceId, conversationId)

  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(workspaceId)
    return c
  }, [workspaceId])

  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<MemoryItem[] | null>(null)
  const [itemsLoading, setItemsLoading] = useState(false)

  // Lazy-load memory contents each time the popover opens — small list, no
  // staleness window worth optimizing for.
  const handleOpenChange = useCallback(
    async (next: boolean) => {
      setOpen(next)
      if (!next) return
      setItemsLoading(true)
      try {
        const list = await listMemory(client, {
          source_conversation_id: conversationId,
          status: 'active',
        })
        setItems(list)
      } catch {
        setItems([])
      } finally {
        setItemsLoading(false)
      }
    },
    [client, conversationId],
  )

  if (count === null || count === 0) return null

  const goToMemoryPage = () => {
    router.push(`/w/${workspaceId}/memory?conversation=${conversationId}`)
  }

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger
        className={cn(
          'group/chip inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs',
          'text-muted-foreground hover:text-foreground hover:bg-muted/60',
          'transition-colors',
        )}
        aria-label={`${count} 条记忆`}
      >
        <Sparkle aria-hidden className="size-3.5" />
        <span className="font-mono tabular-nums">{count}</span>
        <span className="hidden group-hover/chip:inline">条记忆</span>
      </PopoverTrigger>

      <PopoverContent align="start" sideOffset={6} className="w-80 p-0">
        <div className="flex items-center justify-between px-3 py-2 border-b border-border/50">
          <span className="text-xs font-medium text-foreground/70">本对话产生的记忆</span>
          <button
            type="button"
            onClick={goToMemoryPage}
            className={cn(
              'inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px]',
              'text-muted-foreground hover:text-foreground hover:bg-muted/60',
              'transition-colors',
            )}
            aria-label="跳转到记忆页面"
          >
            打开记忆页
            <ArrowUpRight className="size-3" />
          </button>
        </div>

        <div className="max-h-72 overflow-y-auto px-3 py-2 space-y-2">
          {itemsLoading && (
            <div className="space-y-2">
              {[...Array(3)].map((_, i) => (
                <div
                  key={i}
                  className="h-9 rounded-md border border-border/40 bg-muted/30 animate-pulse"
                />
              ))}
            </div>
          )}

          {!itemsLoading && items && items.length === 0 && (
            <p className="text-xs text-muted-foreground/60 py-2 text-center">暂无记忆</p>
          )}

          {!itemsLoading &&
            items &&
            items.length > 0 &&
            items.map((m) => (
              <div
                key={m.id}
                className="rounded-md border border-border/40 bg-muted/20 px-2.5 py-1.5"
              >
                <p className="text-xs leading-snug text-foreground/85 break-words">{m.content}</p>
                <p className="mt-1 text-[10px] text-muted-foreground/60">
                  {m.type} · {m.scope}
                </p>
              </div>
            ))}
        </div>
      </PopoverContent>
    </Popover>
  )
}
