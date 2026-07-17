'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { RotateCcw, X } from 'lucide-react'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'

interface SecretRevealAndRotateProps {
  onRotate: (newSecret: string, overlapSeconds: number) => Promise<void>
  previousSecretExpiresAt: string | null
}

export function SecretRevealAndRotate({
  onRotate,
  previousSecretExpiresAt,
}: SecretRevealAndRotateProps) {
  const t = useTranslations('triggers')
  const [rotateOpen, setRotateOpen] = useState(false)
  const [newSecret, setNewSecret] = useState('')
  const [overlapSeconds, setOverlapSeconds] = useState(86400)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function formatExpiry(iso: string | null): string | null {
    if (!iso) return null
    try {
      return new Date(iso).toLocaleString()
    } catch {
      return iso
    }
  }

  async function handleRotate(): Promise<void> {
    setSaving(true)
    setError(null)
    try {
      await onRotate(newSecret, overlapSeconds)
      setRotateOpen(false)
      setNewSecret('')
      setOverlapSeconds(86400)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const expiry = formatExpiry(previousSecretExpiresAt)

  return (
    <div className="flex flex-col gap-2">
      {expiry && (
        <p className="text-xs text-muted-foreground" data-testid="prev-secret-expiry">
          {t('previousSecretExpiresAt', { date: expiry })}
        </p>
      )}
      <Button
        variant="outline"
        size="sm"
        className="gap-1.5 w-fit"
        onClick={() => setRotateOpen(true)}
        data-testid="rotate-secret-btn"
      >
        <RotateCcw className="size-3.5" />
        {t('rotateSecret')}
      </Button>

      <DialogPrimitive.Root open={rotateOpen} onOpenChange={setRotateOpen}>
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
              'w-[min(480px,calc(100vw-32px))]',
              '-translate-x-1/2 -translate-y-1/2',
              'rounded-xl border border-border bg-popover p-5',
              'text-popover-foreground shadow-2xl',
              'data-[ending-style]:opacity-0 data-[starting-style]:opacity-0',
              'transition-opacity duration-200',
            )}
            data-testid="rotate-secret-dialog"
          >
            <div className="flex items-start justify-between gap-3">
              <DialogPrimitive.Title className="text-base font-semibold">
                {t('rotateSecretTitle')}
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
              <p className="text-xs text-muted-foreground">{t('saveSecretWarning')}</p>

              <div className="flex flex-col gap-1.5">
                <Label htmlFor="new-secret">{t('newSecret')}</Label>
                <Input
                  id="new-secret"
                  type="password"
                  name="trigger-rotated-secret"
                  value={newSecret}
                  onChange={(e) => setNewSecret(e.target.value)}
                  placeholder={t('newSecretPlaceholder')}
                  autoComplete="new-password"
                  autoCapitalize="off"
                  autoCorrect="off"
                  spellCheck={false}
                  data-testid="new-secret-input"
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <Label htmlFor="overlap-seconds">{t('overlapSeconds')}</Label>
                <Input
                  id="overlap-seconds"
                  type="number"
                  min={0}
                  max={604800}
                  value={overlapSeconds}
                  onChange={(e) => setOverlapSeconds(Number(e.target.value))}
                  data-testid="overlap-seconds-input"
                />
                <p className="text-xs text-muted-foreground">{t('overlapSecondsHint')}</p>
              </div>

              {error && (
                <div
                  className={cn(
                    'rounded-md border border-destructive/30',
                    'bg-destructive/5 px-2.5 py-1.5 text-xs text-destructive',
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
              <Button
                type="button"
                size="sm"
                onClick={() => void handleRotate()}
                disabled={saving || !newSecret.trim()}
                data-testid="confirm-rotate-btn"
              >
                {saving ? t('rotating') : t('rotateSecret')}
              </Button>
            </div>
          </DialogPrimitive.Popup>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>
    </div>
  )
}
