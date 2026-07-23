'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { createApiClient, useConversationStore } from '@cubeplex/core'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'

function buildClient(currentWsId: string | null) {
  const client = createApiClient('')
  if (currentWsId) client.setWorkspaceId(currentWsId)
  return client
}

function describeErr(err: unknown): string {
  if (err instanceof Error) return err.message
  return String(err)
}

export interface DeleteConversationDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  conversationId: string
  conversationTitle: string
  currentWsId: string | null
}

/**
 * Confirm dialog for soft-deleting a sidebar conversation.
 * Cancel / Esc: no API. Confirm: conversationStore.remove; toast on failure.
 */
export function DeleteConversationDialog({
  open,
  onOpenChange,
  conversationId,
  conversationTitle,
  currentWsId,
}: DeleteConversationDialogProps): React.ReactElement {
  const t = useTranslations('shellLayout')
  const tSidebar = useTranslations('sidebar')
  const remove = useConversationStore((s) => s.remove)
  const [deleting, setDeleting] = useState(false)

  const displayTitle = conversationTitle.trim() || tSidebar('untitledChat')

  const handleOpenChange = (next: boolean): void => {
    // Block dismiss while the DELETE is in flight so we don't lose the
    // failure surface or double-fire from a re-open race.
    if (deleting) return
    onOpenChange(next)
  }

  const onConfirm = async (): Promise<void> => {
    if (deleting) return
    setDeleting(true)
    try {
      await remove(buildClient(currentWsId), conversationId)
      onOpenChange(false)
    } catch (err) {
      toast.error(t('deleteConversationFailed'), { description: describeErr(err) })
    } finally {
      setDeleting(false)
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={handleOpenChange}>
      <AlertDialogContent
        // Keep the dialog above the sidebar row link; stop row navigation.
        onClick={(e) => e.stopPropagation()}
      >
        <AlertDialogHeader>
          <AlertDialogTitle>{t('deleteConversationTitle')}</AlertDialogTitle>
          <AlertDialogDescription>
            {t('deleteConversationDescription', { title: displayTitle })}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={deleting}>{t('deleteConversationCancel')}</AlertDialogCancel>
          <AlertDialogAction
            disabled={deleting}
            variant="destructive"
            onClick={() => void onConfirm()}
            data-testid="conversation-delete-confirm"
          >
            {deleting ? t('deleteConversationDeleting') : t('deleteConversationConfirm')}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
