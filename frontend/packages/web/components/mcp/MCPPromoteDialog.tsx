'use client'

import { useState } from 'react'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import type { MCPConnector, PromoteDistribution } from '@cubebox/core'
import { Loader2, X } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { cn } from '@/lib/utils'

export interface MCPPromoteDialogProps {
  install: MCPConnector
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: (distribution: PromoteDistribution) => Promise<void>
}

type Mode = 'all' | 'none'

export function MCPPromoteDialog({
  install,
  open,
  onOpenChange,
  onConfirm,
}: MCPPromoteDialogProps) {
  const t = useTranslations('mcp.promote')
  const [mode, setMode] = useState<Mode>('none')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [prevOpen, setPrevOpen] = useState(open)
  if (prevOpen !== open) {
    setPrevOpen(open)
    if (open) {
      setMode('none')
      setError(null)
    }
  }

  async function handleConfirm(): Promise<void> {
    setSubmitting(true)
    setError(null)
    try {
      await onConfirm({ mode })
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
                {t('title')}
              </DialogPrimitive.Title>
              <DialogPrimitive.Description className="text-sm text-muted-foreground">
                {t('description', { name: install.name })}
              </DialogPrimitive.Description>
            </div>
            <DialogPrimitive.Close
              render={
                <button
                  type="button"
                  aria-label={t('close')}
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
                <AlertTitle>{t('failed')}</AlertTitle>
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}

            <RadioGroup value={mode} onValueChange={(value) => setMode((value as Mode) ?? 'none')}>
              <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-border p-3">
                <RadioGroupItem value="none" disabled={submitting} />
                <span className="flex flex-col gap-1">
                  <span className="text-sm font-medium">{t('distNoneTitle')}</span>
                  <span className="text-xs text-muted-foreground">{t('distNoneHelp')}</span>
                </span>
              </label>
              <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-border p-3">
                <RadioGroupItem value="all" disabled={submitting} />
                <span className="flex flex-col gap-1">
                  <span className="text-sm font-medium">{t('distAllTitle')}</span>
                  <span className="text-xs text-muted-foreground">{t('distAllHelp')}</span>
                </span>
              </label>
            </RadioGroup>

            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                disabled={submitting}
                onClick={() => onOpenChange(false)}
              >
                {t('cancel')}
              </Button>
              <Button type="button" disabled={submitting} onClick={() => void handleConfirm()}>
                {submitting ? <Loader2 data-icon="inline-start" className="animate-spin" /> : null}
                {t('confirm')}
              </Button>
            </div>
          </div>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
