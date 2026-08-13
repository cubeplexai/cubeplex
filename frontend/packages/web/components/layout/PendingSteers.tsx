'use client'

import { useShallow } from 'zustand/react/shallow'
import { useMessageStore, createApiClient } from '@cubeplex/core'
import { RotateCcw, X } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { useComposerDraft } from '@/hooks/useComposerDraft'

interface PendingSteersProps {
  conversationId: string
}

export function PendingSteers({ conversationId }: PendingSteersProps): React.ReactElement | null {
  const pending = useMessageStore(useShallow((s) => s.pendingSteers[conversationId] ?? []))
  const cancelSteer = useMessageStore((s) => s.cancelSteer)
  const lifecycle = useMessageStore((s) => s.runLifecycle[conversationId] ?? 'idle')
  const isStreaming = useMessageStore(
    (s) => s.isStreaming && s.streamingConversationId === conversationId,
  )
  const { workspaceId } = useWorkspaceContext()
  const t = useTranslations('input')
  const canRecover = lifecycle === 'idle' && !isStreaming

  if (pending.length === 0) return null

  const onCancel = async (steerId: string): Promise<boolean> => {
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    return cancelSteer(client, conversationId, steerId)
  }

  const recoverFailed = async (steerId: string, text: string): Promise<void> => {
    if (await onCancel(steerId)) {
      useComposerDraft.getState().setDraft(text, conversationId, 'prepend')
    }
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
          <span className="text-[10px] uppercase tracking-wide opacity-50">
            {p.state === 'failed'
              ? t('pendingSteerFailed')
              : p.state === 'queued'
                ? t('pendingSteerQueued')
                : t('pendingSteerSending')}
          </span>
          {p.state === 'failed' && canRecover ? (
            <button
              type="button"
              aria-label={t('pendingSteerRestore')}
              onClick={() => void recoverFailed(p.steerId, p.text)}
              className="grid size-5 place-items-center rounded text-muted-foreground/70 transition-colors hover:bg-muted hover:text-foreground"
            >
              <RotateCcw className="size-3" />
            </button>
          ) : p.state !== 'submitting' ? (
            <button
              type="button"
              aria-label={p.state === 'failed' ? t('pendingSteerDismiss') : t('pendingSteerCancel')}
              onClick={() => void onCancel(p.steerId)}
              className="grid size-5 place-items-center rounded text-muted-foreground/70 transition-colors hover:bg-muted hover:text-foreground"
            >
              <X className="size-3" />
            </button>
          ) : null}
        </div>
      ))}
    </div>
  )
}
