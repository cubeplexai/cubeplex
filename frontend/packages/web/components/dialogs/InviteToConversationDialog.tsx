'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { Info, X } from 'lucide-react'
import {
  createApiClient,
  useAuthStore,
  useConversationStore,
  useMemberStore,
  type WsMember,
} from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'
import { WorkspaceMemberPicker } from '@/components/dialogs/WorkspaceMemberPicker'

interface InviteToConversationDialogProps {
  wsId: string
  conversationId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function InviteToConversationDialog({
  wsId,
  conversationId,
  open,
  onOpenChange,
}: InviteToConversationDialogProps): React.ReactElement {
  const t = useTranslations('conversation.invite')
  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])
  const currentUserId = useAuthStore((s) => s.user?.id ?? null)
  const { wsMembers, loadWsMembers } = useMemberStore()
  const inviteToGroup = useConversationStore((s) => s.inviteToGroup)
  const fetchParticipants = useConversationStore((s) => s.fetchConversationParticipants)
  const participants = useConversationStore((s) => s.conversationParticipants[conversationId])
  const conversation = useConversationStore((s) =>
    s.conversations.find((c) => c.id === conversationId),
  )
  // The note only matters on the 1:1 → group chat transition (memory turns
  // off, sandbox starts being shared). Already-group chats don't change.
  const showPromotionNote = conversation && !conversation.is_group_chat

  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    /* eslint-disable react-hooks/set-state-in-effect */
    setSelected(new Set())
    setError(null)
    setSubmitting(false)
    /* eslint-enable react-hooks/set-state-in-effect */
    void loadWsMembers(client, wsId)
    if (!participants) {
      void fetchParticipants(client, conversationId)
    }
  }, [open, client, wsId, conversationId, loadWsMembers, fetchParticipants, participants])

  const existingIds = useMemo(
    () => new Set((participants ?? []).map((p) => p.user_id)),
    [participants],
  )

  const invitable: WsMember[] = useMemo(
    () => wsMembers.filter((m) => m.user_id !== currentUserId && !existingIds.has(m.user_id)),
    [wsMembers, currentUserId, existingIds],
  )

  const toggleMember = (userId: string): void => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(userId)) next.delete(userId)
      else next.add(userId)
      return next
    })
  }

  const canSubmit = selected.size > 0 && !submitting

  const handleSubmit = async (): Promise<void> => {
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      await inviteToGroup(client, conversationId, Array.from(selected))
      onOpenChange(false)
    } catch {
      setError(t('inviteError'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop
          className={cn(
            'fixed inset-0 z-50 bg-black/40 backdrop-blur-sm',
            'data-[ending-style]:opacity-0',
            'data-[starting-style]:opacity-0',
            'transition-opacity duration-200',
          )}
        />
        <DialogPrimitive.Popup
          className={cn(
            'fixed left-1/2 top-1/2 z-50',
            'w-[min(460px,calc(100vw-32px))]',
            '-translate-x-1/2 -translate-y-1/2',
            'rounded-xl border border-border bg-popover p-5',
            'text-popover-foreground shadow-2xl',
            'data-[ending-style]:opacity-0',
            'data-[starting-style]:opacity-0',
            'transition-opacity duration-200',
          )}
          data-testid="invite-to-conversation-dialog"
        >
          <div className="flex items-start justify-between gap-3">
            <DialogPrimitive.Title className="text-base font-semibold">
              {t('title')}
            </DialogPrimitive.Title>
            <DialogPrimitive.Close
              render={
                <button
                  type="button"
                  aria-label="close"
                  className={cn(
                    'rounded-md p-1 text-muted-foreground',
                    'hover:bg-muted hover:text-foreground',
                  )}
                >
                  <X className="size-4" />
                </button>
              }
            />
          </div>

          <p className="mt-1 text-xs text-muted-foreground">{t('description')}</p>

          <div className="mt-4 flex flex-col gap-4">
            {showPromotionNote && (
              <div
                className={cn(
                  'flex items-start gap-2 rounded-md border border-info-border',
                  'bg-info-surface px-2.5 py-2 text-xs text-info-fg leading-relaxed',
                )}
              >
                <Info className="size-3.5 shrink-0 mt-0.5" />
                <div className="flex flex-col gap-0.5">
                  <p>{t('noteMemory')}</p>
                  <p>{t('noteSandbox')}</p>
                </div>
              </div>
            )}

            <div className="flex flex-col gap-1.5">
              <Label>{t('button')}</Label>
              <WorkspaceMemberPicker
                invitable={invitable}
                selected={selected}
                onToggle={toggleMember}
                emptyText={t('emptyMembers')}
              />
            </div>

            {error && (
              <div
                className={cn(
                  'rounded-md border border-destructive/30',
                  'bg-destructive/5 px-2.5 py-1.5',
                  'text-xs text-destructive',
                )}
              >
                {error}
              </div>
            )}
          </div>

          <div className="mt-5 flex items-center justify-end gap-2">
            <DialogPrimitive.Close
              render={
                <Button type="button" variant="ghost" size="sm" disabled={submitting}>
                  {t('cancel')}
                </Button>
              }
            />
            <Button
              type="button"
              size="sm"
              onClick={() => void handleSubmit()}
              disabled={!canSubmit}
            >
              {submitting ? t('inviting') : t('invite')}
            </Button>
          </div>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
