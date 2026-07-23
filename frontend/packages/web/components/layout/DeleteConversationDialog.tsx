'use client'

import { useRef, useState } from 'react'
import { usePathname, useRouter } from 'next/navigation'
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

/** True when the current route is this conversation's chat page. */
function isViewingConversation(
  pathname: string | null,
  wsId: string | null,
  conversationId: string,
): boolean {
  if (!pathname || !wsId) return false
  return pathname === `/w/${wsId}/conversations/${conversationId}`
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
 * Deleting the open conversation navigates to the workspace home.
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
  const router = useRouter()
  const pathname = usePathname()
  // Read latest route at await completion — the onConfirm closure can outlive
  // a mid-flight navigation (browser back / other UI) and must not bounce the
  // user back to workspace home from their new page.
  const pathnameRef = useRef(pathname)
  pathnameRef.current = pathname
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
      // Store clears activeId, but the chat route stays mounted unless we leave.
      if (currentWsId && isViewingConversation(pathnameRef.current, currentWsId, conversationId)) {
        router.replace(`/w/${currentWsId}`)
      }
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
