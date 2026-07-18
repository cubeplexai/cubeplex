'use client'

import { useShallow } from 'zustand/react/shallow'
import { useMessageStore, createApiClient } from '@cubeplex/core'
import { X } from 'lucide-react'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'

interface PendingSteersProps {
  conversationId: string
}

export function PendingSteers({ conversationId }: PendingSteersProps): React.ReactElement | null {
  const pending = useMessageStore(useShallow((s) => s.pendingSteers[conversationId] ?? []))
  const cancelSteer = useMessageStore((s) => s.cancelSteer)
  const { workspaceId } = useWorkspaceContext()

  if (pending.length === 0) return null

  const onCancel = (steerId: string): void => {
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    void cancelSteer(client, conversationId, steerId)
  }

  return (
    <div className="mb-2 flex flex-col gap-1.5">
      {pending.map((p) => (
        <div
          key={p.steerId}
          data-testid="pending-steer"
          className="flex items-center gap-2 rounded-lg border border-dashed border-border/60 bg-muted/40 px-3 py-1.5 text-sm text-muted-foreground"
        >
          <span className="flex-1 truncate opacity-70">{p.text}</span>
          <span className="text-[10px] uppercase tracking-wide opacity-50">steering…</span>
          <button
            type="button"
            aria-label="Cancel pending steer"
            onClick={() => onCancel(p.steerId)}
            className="grid size-5 place-items-center rounded text-muted-foreground/70 transition-colors hover:bg-muted hover:text-foreground"
          >
            <X className="size-3" />
          </button>
        </div>
      ))}
    </div>
  )
}
