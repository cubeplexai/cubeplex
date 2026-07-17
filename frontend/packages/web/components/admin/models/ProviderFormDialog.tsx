'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { X } from 'lucide-react'
import type { Provider, ProviderCreate, ProviderUpdate } from '@cubeplex/core'
import { cn } from '@/lib/utils'
import { ProviderConfigForm } from './ProviderConfigForm'

interface ProviderFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  provider: Provider
  onSave: (body: ProviderUpdate) => Promise<void>
}

export function ProviderFormDialog({
  open,
  onOpenChange,
  provider,
  onSave,
}: ProviderFormDialogProps) {
  const t = useTranslations('adminModels')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(body: ProviderCreate | ProviderUpdate) {
    setSaving(true)
    setError(null)
    try {
      await onSave(body as ProviderUpdate)
      onOpenChange(false)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 transition-opacity duration-200" />
        <DialogPrimitive.Popup
          className={cn(
            'fixed left-1/2 top-1/2 z-50 w-[min(520px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2',
            'max-h-[calc(100vh-32px)] overflow-y-auto',
            'rounded-xl border border-border bg-popover p-5 text-popover-foreground shadow-2xl',
            'data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 transition-opacity duration-200',
          )}
          data-testid="provider-form-dialog"
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <DialogPrimitive.Title className="text-base font-semibold">
                {t('editTitle')}
              </DialogPrimitive.Title>
              <DialogPrimitive.Description className="mt-0.5 text-xs text-muted-foreground">
                {t('editDesc')}
              </DialogPrimitive.Description>
            </div>
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

          <div className="mt-4">
            <ProviderConfigForm
              mode="edit"
              provider={provider}
              saving={saving}
              error={error}
              submitLabel={t('save')}
              onSubmit={(body) => void handleSubmit(body)}
            />
          </div>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
