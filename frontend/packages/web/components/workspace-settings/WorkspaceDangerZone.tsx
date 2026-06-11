'use client'

import { useCallback, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { X } from 'lucide-react'
import { toast } from 'sonner'
import { createApiClient, useWorkspaceStore } from '@cubebox/core'

import { DangerZone } from '@/components/management/DangerZone'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

interface WorkspaceDangerZoneProps {
  wsId: string
}

export function WorkspaceDangerZone({ wsId }: WorkspaceDangerZoneProps) {
  const t = useTranslations('wsSettings.dangerZone')
  const router = useRouter()

  const workspaceName = useWorkspaceStore(
    (s) => s.workspaces.find((w) => w.id === wsId)?.name ?? '',
  )
  const archiveWs = useWorkspaceStore((s) => s.archive)
  const deleteWs = useWorkspaceStore((s) => s.deleteWs)

  const [archiving, setArchiving] = useState(false)
  const [archiveOpen, setArchiveOpen] = useState(false)

  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleteNameInput, setDeleteNameInput] = useState('')
  const [deleting, setDeleting] = useState(false)

  const client = useCallback(() => createApiClient(''), [])

  const handleArchive = async (): Promise<void> => {
    setArchiving(true)
    try {
      await archiveWs(client(), wsId)
      toast.success(t('archiveSuccess'))
      setArchiveOpen(false)
      router.push('/')
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setArchiving(false)
    }
  }

  const handleDelete = async (): Promise<void> => {
    if (deleteNameInput !== workspaceName) return
    setDeleting(true)
    try {
      await deleteWs(client(), wsId)
      toast.success(t('deleteSuccess'))
      setDeleteOpen(false)
      router.push('/')
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setDeleting(false)
    }
  }

  const handleDeleteOpenChange = (open: boolean): void => {
    setDeleteOpen(open)
    if (!open) setDeleteNameInput('')
  }

  return (
    <DangerZone title={t('title')}>
      <div className="flex flex-col gap-4">
        {/* Archive */}
        <div className="flex items-center justify-between gap-4">
          <p className="text-sm text-muted-foreground">{t('archiveDescription')}</p>
          <Button
            variant="outline"
            size="sm"
            className="shrink-0 border-danger-border text-danger-fg hover:bg-danger-surface"
            onClick={() => setArchiveOpen(true)}
          >
            {t('archiveButton')}
          </Button>
        </div>

        <div className="border-t border-danger-border/40" />

        {/* Delete */}
        <div className="flex items-center justify-between gap-4">
          <p className="text-sm text-muted-foreground">{t('deleteDescription')}</p>
          <Button
            variant="outline"
            size="sm"
            className="shrink-0 border-danger-border text-danger-fg hover:bg-danger-surface"
            onClick={() => setDeleteOpen(true)}
          >
            {t('deleteButton')}
          </Button>
        </div>
      </div>

      {/* Archive confirm dialog */}
      <DialogPrimitive.Root open={archiveOpen} onOpenChange={setArchiveOpen}>
        <DialogPrimitive.Portal>
          <DialogPrimitive.Backdrop
            className={cn(
              'fixed inset-0 z-50 bg-black/40 backdrop-blur-sm',
              'data-[ending-style]:opacity-0 data-[starting-style]:opacity-0',
              'transition-opacity duration-200',
            )}
          />
          <DialogPrimitive.Popup
            className={cn(
              'fixed left-1/2 top-1/2 z-50',
              'w-[min(420px,calc(100vw-32px))]',
              '-translate-x-1/2 -translate-y-1/2',
              'rounded-xl border border-border bg-popover p-5',
              'text-popover-foreground shadow-2xl',
              'data-[ending-style]:opacity-0 data-[starting-style]:opacity-0',
              'transition-opacity duration-200',
            )}
          >
            <div className="flex items-start justify-between gap-3">
              <DialogPrimitive.Title className="text-base font-semibold">
                {t('archiveConfirmTitle')}
              </DialogPrimitive.Title>
              <DialogPrimitive.Close
                render={
                  <button
                    type="button"
                    aria-label="close"
                    className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    <X className="size-4" />
                  </button>
                }
              />
            </div>
            <p className="mt-3 text-sm text-muted-foreground">{t('archiveConfirmMessage')}</p>
            <div className="mt-4 flex items-center justify-end gap-2">
              <DialogPrimitive.Close
                render={
                  <Button type="button" variant="ghost" size="sm" disabled={archiving}>
                    {t('cancel')}
                  </Button>
                }
              />
              <Button
                type="button"
                size="sm"
                variant="destructive"
                onClick={() => void handleArchive()}
                disabled={archiving}
              >
                {t('archiveButton')}
              </Button>
            </div>
          </DialogPrimitive.Popup>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>

      {/* Delete confirm dialog */}
      <DialogPrimitive.Root open={deleteOpen} onOpenChange={handleDeleteOpenChange}>
        <DialogPrimitive.Portal>
          <DialogPrimitive.Backdrop
            className={cn(
              'fixed inset-0 z-50 bg-black/40 backdrop-blur-sm',
              'data-[ending-style]:opacity-0 data-[starting-style]:opacity-0',
              'transition-opacity duration-200',
            )}
          />
          <DialogPrimitive.Popup
            className={cn(
              'fixed left-1/2 top-1/2 z-50',
              'w-[min(420px,calc(100vw-32px))]',
              '-translate-x-1/2 -translate-y-1/2',
              'rounded-xl border border-border bg-popover p-5',
              'text-popover-foreground shadow-2xl',
              'data-[ending-style]:opacity-0 data-[starting-style]:opacity-0',
              'transition-opacity duration-200',
            )}
          >
            <div className="flex items-start justify-between gap-3">
              <DialogPrimitive.Title className="text-base font-semibold">
                {t('deleteConfirmTitle')}
              </DialogPrimitive.Title>
              <DialogPrimitive.Close
                render={
                  <button
                    type="button"
                    aria-label="close"
                    className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    <X className="size-4" />
                  </button>
                }
              />
            </div>
            <p className="mt-3 text-sm text-muted-foreground">{t('deleteConfirmMessage')}</p>
            <div className="mt-3">
              <Input
                value={deleteNameInput}
                onChange={(e) => setDeleteNameInput(e.target.value)}
                placeholder={t('deleteNamePlaceholder')}
                className="text-sm"
                disabled={deleting}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void handleDelete()
                }}
              />
            </div>
            <div className="mt-4 flex items-center justify-end gap-2">
              <DialogPrimitive.Close
                render={
                  <Button type="button" variant="ghost" size="sm" disabled={deleting}>
                    {t('cancel')}
                  </Button>
                }
              />
              <Button
                type="button"
                size="sm"
                variant="destructive"
                onClick={() => void handleDelete()}
                disabled={deleting || deleteNameInput !== workspaceName}
              >
                {t('deleteConfirmButton')}
              </Button>
            </div>
          </DialogPrimitive.Popup>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>
    </DangerZone>
  )
}
