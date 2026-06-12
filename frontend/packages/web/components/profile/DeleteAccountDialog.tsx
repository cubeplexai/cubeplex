'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { X } from 'lucide-react'
import { createApiClient, useAuthStore, useWorkspaceStore } from '@cubebox/core'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface DeleteAccountDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function DeleteAccountDialog({ open, onOpenChange }: DeleteAccountDialogProps) {
  const t = useTranslations('profile.deleteAccount')
  const router = useRouter()
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  const handleOpenChange = (next: boolean): void => {
    onOpenChange(next)
    if (!next) {
      setPassword('')
      setError(null)
    }
  }

  const handleDelete = async (): Promise<void> => {
    setError(null)
    setDeleting(true)
    try {
      const client = createApiClient('')
      const res = await client.post('/api/v1/auth/delete-account', { password })
      if (!res.ok) {
        const body = (await res.json()) as { detail?: string }
        if (body.detail === 'incorrect_password') {
          setError(t('wrongPassword'))
        } else if (body.detail === 'transfer_ownership_first') {
          setError(t('transferFirst'))
        } else {
          setError(t('error'))
        }
        return
      }
      useAuthStore.getState().reset()
      useWorkspaceStore.getState().reset()
      router.replace('/login')
    } catch {
      setError(t('error'))
    } finally {
      setDeleting(false)
    }
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={handleOpenChange}>
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
              {t('title')}
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
          <p className="mt-3 text-sm text-muted-foreground">{t('warning')}</p>
          <div className="mt-3">
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={t('passwordPlaceholder')}
              autoComplete="current-password"
              disabled={deleting}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              onKeyDown={(e) => {
                if (e.key === 'Enter') void handleDelete()
              }}
            />
          </div>
          {error && <p className="mt-2 text-sm text-destructive">{error}</p>}
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
              disabled={deleting || password.length === 0}
            >
              {deleting ? t('deleting') : t('confirm')}
            </Button>
          </div>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
