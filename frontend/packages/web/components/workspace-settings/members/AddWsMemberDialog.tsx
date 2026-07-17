'use client'

import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { X } from 'lucide-react'
import type { AvailableMember } from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'

interface AddWsMemberDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  available: AvailableMember[]
  onAdd: (userId: string, role: string) => Promise<void>
}

export function AddWsMemberDialog({
  open,
  onOpenChange,
  available,
  onAdd,
}: AddWsMemberDialogProps) {
  const t = useTranslations('wsMembers.addDialog')
  const [userId, setUserId] = useState('')
  const [role, setRole] = useState('member')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      /* eslint-disable react-hooks/set-state-in-effect */
      setUserId('')
      setRole('member')
      setError(null)
      setSaving(false)
      /* eslint-enable react-hooks/set-state-in-effect */
    }
  }, [open])

  async function handleAdd(): Promise<void> {
    setSaving(true)
    setError(null)
    try {
      await onAdd(userId, role)
      onOpenChange(false)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const noAvailable = available.length === 0

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
            'w-[min(420px,calc(100vw-32px))]',
            '-translate-x-1/2 -translate-y-1/2',
            'rounded-xl border border-border bg-popover p-5',
            'text-popover-foreground shadow-2xl',
            'data-[ending-style]:opacity-0',
            'data-[starting-style]:opacity-0',
            'transition-opacity duration-200',
          )}
          data-testid="add-ws-member-dialog"
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

          <div className="mt-4 flex flex-col gap-3">
            {noAvailable ? (
              <p className="text-xs text-muted-foreground">{t('noAvailable')}</p>
            ) : (
              <>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="add-ws-user">{t('selectLabel')}</Label>
                  <Select value={userId} onValueChange={(v) => setUserId(v ?? '')}>
                    <SelectTrigger id="add-ws-user">
                      <SelectValue placeholder={t('selectPlaceholder')} />
                    </SelectTrigger>
                    <SelectContent>
                      {available.map((m) => (
                        <SelectItem key={m.user_id} value={m.user_id}>
                          {m.email}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="add-ws-role">{t('roleLabel')}</Label>
                  <Select value={role} onValueChange={(v) => setRole(v ?? 'member')}>
                    <SelectTrigger id="add-ws-role">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="admin">admin</SelectItem>
                      <SelectItem value="member">member</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </>
            )}

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

          <div className="mt-4 flex items-center justify-end gap-2">
            <DialogPrimitive.Close
              render={
                <Button type="button" variant="ghost" size="sm" disabled={saving}>
                  {t('cancel')}
                </Button>
              }
            />
            {!noAvailable && (
              <Button
                type="button"
                size="sm"
                onClick={() => void handleAdd()}
                disabled={saving || !userId}
              >
                {t('add')}
              </Button>
            )}
          </div>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
