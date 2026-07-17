'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { Loader2, X } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export interface MCPDistributeDialogProps {
  templateName: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: (opts: { enable_existing: boolean; auto_enroll: boolean }) => Promise<void>
}

export function MCPDistributeDialog({
  templateName,
  open,
  onOpenChange,
  onConfirm,
}: MCPDistributeDialogProps) {
  const t = useTranslations('mcpAdmin')
  const [enableExisting, setEnableExisting] = useState(true)
  const [autoEnroll, setAutoEnroll] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Reset state when dialog opens.
  const [prevOpen, setPrevOpen] = useState(open)
  if (prevOpen !== open) {
    setPrevOpen(open)
    if (open) {
      setEnableExisting(true)
      setAutoEnroll(true)
      setError(null)
    }
  }

  async function handleConfirm(): Promise<void> {
    setSubmitting(true)
    setError(null)
    try {
      await onConfirm({ enable_existing: enableExisting, auto_enroll: autoEnroll })
      onOpenChange(false)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 transition-opacity duration-200" />
        <DialogPrimitive.Popup
          className={cn(
            'fixed left-1/2 top-1/2 z-50 w-[min(520px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2',
            'rounded-xl border border-border bg-popover p-5 text-popover-foreground shadow-2xl',
            'data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 transition-opacity duration-200',
          )}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="flex flex-col gap-1">
              <DialogPrimitive.Title className="text-base font-semibold">
                {t('distributeDialogTitle')}
              </DialogPrimitive.Title>
              <DialogPrimitive.Description className="text-sm text-muted-foreground">
                {t('distributeDialogDesc', { name: templateName })}
              </DialogPrimitive.Description>
            </div>
            <DialogPrimitive.Close
              render={
                <button
                  type="button"
                  aria-label={t('distributeDialogClose')}
                  className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                  disabled={submitting}
                >
                  <X />
                </button>
              }
            />
          </div>

          <div className="mt-4 flex flex-col gap-4">
            {error && (
              <Alert variant="destructive">
                <AlertTitle>{t('distributeError')}</AlertTitle>
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}

            <div className="flex flex-col gap-3 rounded-lg border border-border p-4">
              <label className="flex cursor-pointer items-start gap-3">
                <input
                  type="checkbox"
                  checked={enableExisting}
                  onChange={(e) => setEnableExisting(e.target.checked)}
                  disabled={submitting}
                  className="mt-0.5 size-4 cursor-pointer rounded border-border accent-primary"
                />
                <span className="flex flex-col gap-0.5">
                  <span className="text-sm font-medium">{t('distributeEnableExisting')}</span>
                  <span className="text-xs text-muted-foreground">
                    {t('distributeEnableExistingDesc')}
                  </span>
                </span>
              </label>

              <label className="flex cursor-pointer items-start gap-3">
                <input
                  type="checkbox"
                  checked={autoEnroll}
                  onChange={(e) => setAutoEnroll(e.target.checked)}
                  disabled={submitting}
                  className="mt-0.5 size-4 cursor-pointer rounded border-border accent-primary"
                />
                <span className="flex flex-col gap-0.5">
                  <span className="text-sm font-medium">{t('distributeAutoEnroll')}</span>
                  <span className="text-xs text-muted-foreground">
                    {t('distributeAutoEnrollDesc')}
                  </span>
                </span>
              </label>
            </div>

            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                disabled={submitting}
                onClick={() => onOpenChange(false)}
              >
                {t('distributeCancel')}
              </Button>
              <Button type="button" disabled={submitting} onClick={() => void handleConfirm()}>
                {submitting ? <Loader2 data-icon="inline-start" className="animate-spin" /> : null}
                {t('distributeConfirm')}
              </Button>
            </div>
          </div>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
