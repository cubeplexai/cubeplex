'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Copy, Check } from 'lucide-react'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { createApiClient, createInvite } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'

interface CreateInviteDialogProps {
  wsId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function CreateInviteDialog({ wsId, open, onOpenChange }: CreateInviteDialogProps) {
  const t = useTranslations('wsMembers.invite')
  const [role, setRole] = useState('member')
  const [link, setLink] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const onCreate = async () => {
    setCreating(true)
    setError(null)
    try {
      const client = createApiClient('')
      const result = await createInvite(client, wsId, role)
      setLink(`${window.location.origin}/invite/accept?token=${result.token}`)
    } catch {
      setError(t('createError'))
    } finally {
      setCreating(false)
    }
  }

  const onCopy = async () => {
    if (!link) return
    await navigator.clipboard.writeText(link)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const onClose = () => {
    setLink(null)
    setCopied(false)
    setError(null)
    setRole('member')
    onOpenChange(false)
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onClose}>
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
        >
          <DialogPrimitive.Title className="text-base font-semibold mb-4">
            {t('title')}
          </DialogPrimitive.Title>
          {!link ? (
            <>
              <label className="block mb-3">
                <span className="text-sm text-foreground/80">{t('roleLabel')}</span>
                <Select value={role} onValueChange={(v) => setRole(v ?? 'member')}>
                  <SelectTrigger className="mt-1 w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="admin">{t('admin')}</SelectItem>
                    <SelectItem value="member">{t('member')}</SelectItem>
                  </SelectContent>
                </Select>
              </label>
              {error && <div className="text-sm text-destructive mb-3">{error}</div>}
              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={onClose}>
                  {t('cancel')}
                </Button>
                <Button onClick={() => void onCreate()} disabled={creating}>
                  {creating ? t('creating') : t('create')}
                </Button>
              </div>
            </>
          ) : (
            <>
              <p className="text-sm text-muted-foreground mb-2">{t('linkReady')}</p>
              <div className="flex items-center gap-2 rounded-md border border-border bg-muted/50 px-3 py-2">
                <code className="flex-1 text-xs break-all">{link}</code>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => void onCopy()}
                  className="shrink-0"
                >
                  {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
                </Button>
              </div>
              <div className="flex justify-end mt-4">
                <Button onClick={onClose}>{t('done')}</Button>
              </div>
            </>
          )}
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
