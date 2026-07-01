'use client'

import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { Check, Copy, X } from 'lucide-react'
import { createApiClient, createOrgInvite } from '@cubebox/core'
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

interface CreateOrgInviteDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function CreateOrgInviteDialog({ open, onOpenChange }: CreateOrgInviteDialogProps) {
  const t = useTranslations('adminMembers.inviteDialog')
  const [role, setRole] = useState('member')
  const [creating, setCreating] = useState(false)
  const [inviteUrl, setInviteUrl] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      /* eslint-disable react-hooks/set-state-in-effect */
      setRole('member')
      setInviteUrl(null)
      setCopied(false)
      setError(null)
      setCreating(false)
      /* eslint-enable react-hooks/set-state-in-effect */
    }
  }, [open])

  async function handleCreate(): Promise<void> {
    setCreating(true)
    setError(null)
    try {
      const client = createApiClient('')
      const result = await createOrgInvite(client, role as 'admin' | 'member')
      setInviteUrl(result.invite_url)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setCreating(false)
    }
  }

  async function handleCopy(): Promise<void> {
    if (!inviteUrl) return
    try {
      await navigator.clipboard.writeText(inviteUrl)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // clipboard may be unavailable; the link remains selectable
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
            'w-[min(420px,calc(100vw-32px))]',
            '-translate-x-1/2 -translate-y-1/2',
            'rounded-xl border border-border bg-popover p-5',
            'text-popover-foreground shadow-2xl',
            'data-[ending-style]:opacity-0',
            'data-[starting-style]:opacity-0',
            'transition-opacity duration-200',
          )}
          data-testid="create-org-invite-dialog"
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

          {inviteUrl ? (
            <div className="mt-4 flex flex-col gap-3">
              <p className="text-sm text-muted-foreground">{t('linkReady')}</p>
              <div className="flex items-center gap-2">
                <input
                  readOnly
                  value={inviteUrl}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-xs"
                  onFocus={(e) => e.target.select()}
                />
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="gap-1.5"
                  onClick={() => void handleCopy()}
                >
                  {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
                  {copied ? t('copied') : t('copy')}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">{t('expiresNote')}</p>
              <Button type="button" onClick={() => onOpenChange(false)}>
                {t('done')}
              </Button>
            </div>
          ) : (
            <div className="mt-4 flex flex-col gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="org-invite-role">{t('roleLabel')}</Label>
                <Select value={role} onValueChange={(v) => setRole(v ?? 'member')}>
                  <SelectTrigger id="org-invite-role">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="admin">admin</SelectItem>
                    <SelectItem value="member">member</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <p className="text-xs text-muted-foreground">{t('description')}</p>
              {error && <div className="text-sm text-destructive">{error}</div>}
              <Button type="button" disabled={creating} onClick={() => void handleCreate()}>
                {creating ? t('creating') : t('create')}
              </Button>
            </div>
          )}
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
