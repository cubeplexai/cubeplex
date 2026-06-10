'use client'

import { useRef, useState } from 'react'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { Upload, X } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { csrfHeaders, readApiError } from '@/lib/csrf'
import { cn } from '@/lib/utils'

interface UploadWorkspaceSkillModalProps {
  wsId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onUploaded: () => void
}

export function UploadWorkspaceSkillModal({
  wsId,
  open,
  onOpenChange,
  onUploaded,
}: UploadWorkspaceSkillModalProps) {
  const t = useTranslations('wsSettings.uploadModal')
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  function reset(): void {
    setFile(null)
    setBusy(false)
    setError(null)
    setSuccess(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  function handleOpenChange(next: boolean): void {
    if (!next) reset()
    onOpenChange(next)
  }

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault()
    if (!file) {
      setError(t('selectError'))
      return
    }
    setBusy(true)
    setError(null)
    setSuccess(null)
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch(`/api/v1/ws/${wsId}/settings/skills/upload`, {
        method: 'POST',
        credentials: 'include',
        headers: csrfHeaders(),
        body: form,
      })
      if (!res.ok) throw new Error(await readApiError(res))
      const data = (await res.json()) as { skill_id: string; version: string }
      setSuccess(t('successPattern', { skill: data.skill_id, version: data.version }))
      onUploaded()
      // Auto-close after a short pause so the toast is visible.
      setTimeout(() => {
        handleOpenChange(false)
      }, 1200)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={handleOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm transition-opacity duration-200 data-[ending-style]:opacity-0 data-[starting-style]:opacity-0" />
        <DialogPrimitive.Popup
          className={cn(
            'fixed left-1/2 top-1/2 z-50 w-[min(480px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2',
            'rounded-xl border border-border bg-popover p-5 text-popover-foreground shadow-2xl',
            'transition-opacity duration-200 data-[ending-style]:opacity-0 data-[starting-style]:opacity-0',
          )}
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <DialogPrimitive.Title className="text-base font-semibold">
                {t('title')}
              </DialogPrimitive.Title>
              <DialogPrimitive.Description className="mt-0.5 text-xs text-muted-foreground">
                {t('description')}
              </DialogPrimitive.Description>
            </div>
            <DialogPrimitive.Close
              render={
                <button
                  type="button"
                  aria-label={t('close')}
                  className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <X className="size-4" />
                </button>
              }
            />
          </div>

          <form onSubmit={(e) => void handleSubmit(e)} className="mt-4 flex flex-col gap-3">
            <label
              htmlFor="upload-ws-skill-file"
              className={cn(
                'flex cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border-2 border-dashed border-border bg-muted/30 px-4 py-6 text-center transition-colors',
                'hover:border-primary/40 hover:bg-primary/5',
              )}
            >
              <Upload className="size-5 text-muted-foreground" />
              <span className="text-sm font-medium">{file ? file.name : t('selectFile')}</span>
              <span className="text-[11px] text-muted-foreground">
                {file ? `${(file.size / 1024).toFixed(1)} KB` : t('filesizeHint')}
              </span>
              <input
                ref={inputRef}
                id="upload-ws-skill-file"
                type="file"
                accept=".zip,application/zip"
                className="sr-only"
                onChange={(e) => {
                  const f = e.target.files?.[0] ?? null
                  setFile(f)
                  setError(null)
                  setSuccess(null)
                }}
              />
            </label>

            {error && (
              <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                {error}
              </div>
            )}
            {success && (
              <div className="rounded-md border border-success-border bg-success-solid/5 px-3 py-2 text-xs text-success-fg">
                {success}
              </div>
            )}

            <div className="flex items-center justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => handleOpenChange(false)}
                disabled={busy}
              >
                {t('cancel')}
              </Button>
              <Button type="submit" size="sm" disabled={busy || !file}>
                {busy ? t('uploading') : t('upload')}
              </Button>
            </div>
          </form>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
