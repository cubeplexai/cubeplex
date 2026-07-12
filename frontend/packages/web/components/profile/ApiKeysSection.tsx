'use client'

import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { Copy, KeyRound, Trash2, X } from 'lucide-react'
import { toast } from 'sonner'
import {
  createApiClient,
  createApiKey,
  deleteApiKey,
  listApiKeys,
  type ApiKeyCreated,
  type ApiKeyListItem,
} from '@cubeplex/core'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const MAX_KEYS = 10

function formatDateTime(value: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString()
}

export function ApiKeysSection() {
  const t = useTranslations('profile.apiKeys')
  const [keys, setKeys] = useState<ApiKeyListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [createOpen, setCreateOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<ApiKeyListItem | null>(null)
  const [createdKey, setCreatedKey] = useState<ApiKeyCreated | null>(null)

  const reload = async () => {
    setLoading(true)
    try {
      const client = createApiClient('')
      setKeys(await listApiKeys(client))
    } catch {
      toast.error(t('loadError'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- load-on-mount
    void reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const atLimit = keys.length >= MAX_KEYS

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-medium">{t('title')}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t('subtitle')}</p>
        </div>
        <Button
          size="sm"
          onClick={() => setCreateOpen(true)}
          disabled={atLimit}
          title={atLimit ? t('atLimit', { max: MAX_KEYS }) : undefined}
        >
          {t('create')}
        </Button>
      </div>

      {loading ? (
        <p className="text-sm text-muted-foreground">{t('loading')}</p>
      ) : keys.length === 0 ? (
        <div className="rounded-md border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
          {t('empty')}
        </div>
      ) : (
        <ul className="divide-y divide-border rounded-md border border-border">
          {keys.map((k) => (
            <li key={k.id} className="flex items-center gap-3 px-4 py-3">
              <KeyRound className="size-4 shrink-0 text-muted-foreground" aria-hidden />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{k.label}</div>
                <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
                  <span className="font-mono">{k.prefix}…</span>
                  <span>{t('lastUsed', { value: formatDateTime(k.last_used_at) })}</span>
                  <span>{t('createdAt', { value: formatDateTime(k.created_at) })}</span>
                </div>
              </div>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setDeleteTarget(k)}
                aria-label={t('deleteAria', { label: k.label })}
              >
                <Trash2 className="size-4" />
              </Button>
            </li>
          ))}
        </ul>
      )}

      {atLimit && (
        <p className="text-xs text-muted-foreground">{t('atLimitHint', { max: MAX_KEYS })}</p>
      )}

      <CreateApiKeyDialog
        open={createOpen}
        onOpenChange={(open) => {
          setCreateOpen(open)
          if (!open) setCreatedKey(null)
        }}
        onCreated={(created) => {
          setCreatedKey(created)
          void reload()
        }}
        createdKey={createdKey}
      />
      <DeleteApiKeyDialog
        target={deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onDeleted={() => {
          setDeleteTarget(null)
          void reload()
        }}
      />
    </section>
  )
}

function CreateApiKeyDialog({
  open,
  onOpenChange,
  onCreated,
  createdKey,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: (created: ApiKeyCreated) => void
  createdKey: ApiKeyCreated | null
}) {
  const t = useTranslations('profile.apiKeys.createDialog')
  const [label, setLabel] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- reset dialog state on close
      setLabel('')
      setError(null)
      setSubmitting(false)
    }
  }, [open])

  const submit = async () => {
    if (!label.trim()) return
    setSubmitting(true)
    setError(null)
    try {
      const created = await createApiKey(createApiClient(''), label.trim())
      onCreated(created)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg.includes('409') ? t('atLimit') : t('error'))
    } finally {
      setSubmitting(false)
    }
  }

  const copy = async () => {
    if (!createdKey) return
    try {
      await navigator.clipboard.writeText(createdKey.token)
      toast.success(t('copied'))
    } catch {
      toast.error(t('copyFailed'))
    }
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
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
        >
          <div className="flex items-start justify-between gap-3">
            <DialogPrimitive.Title className="text-base font-semibold">
              {createdKey ? t('createdTitle') : t('title')}
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

          {!createdKey ? (
            <>
              <p className="mt-3 text-sm text-muted-foreground">{t('description')}</p>
              <div className="mt-3">
                <label className="block">
                  <span className="text-xs text-muted-foreground">{t('labelInput')}</span>
                  <input
                    type="text"
                    value={label}
                    onChange={(e) => setLabel(e.target.value)}
                    placeholder={t('labelPlaceholder')}
                    autoFocus
                    maxLength={100}
                    disabled={submitting}
                    className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') void submit()
                    }}
                  />
                </label>
                {error && <p className="mt-2 text-sm text-destructive">{error}</p>}
              </div>
              <div className="mt-5 flex items-center justify-end gap-2">
                <DialogPrimitive.Close
                  render={
                    <Button type="button" variant="ghost" size="sm" disabled={submitting}>
                      {t('cancel')}
                    </Button>
                  }
                />
                <Button
                  type="button"
                  size="sm"
                  onClick={() => void submit()}
                  disabled={submitting || label.trim().length === 0}
                >
                  {submitting ? t('creating') : t('confirm')}
                </Button>
              </div>
            </>
          ) : (
            <>
              <p className="mt-3 rounded-md bg-warning-surface px-3 py-2 text-sm text-warning-fg">
                {t('oneTimeWarning')}
              </p>
              <div className="mt-3 flex items-center gap-2 rounded-md border border-border bg-muted px-3 py-2">
                <code className="min-w-0 flex-1 truncate font-mono text-xs">
                  {createdKey.token}
                </code>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => void copy()}
                  aria-label={t('copy')}
                >
                  <Copy className="size-4" />
                </Button>
              </div>
              <div className="mt-5 flex items-center justify-end">
                <DialogPrimitive.Close
                  render={
                    <Button type="button" size="sm">
                      {t('done')}
                    </Button>
                  }
                />
              </div>
            </>
          )}
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}

function DeleteApiKeyDialog({
  target,
  onClose,
  onDeleted,
}: {
  target: ApiKeyListItem | null
  onClose: () => void
  onDeleted: () => void
}) {
  const t = useTranslations('profile.apiKeys.deleteDialog')
  const [submitting, setSubmitting] = useState(false)

  const handleDelete = async () => {
    if (!target) return
    setSubmitting(true)
    try {
      await deleteApiKey(createApiClient(''), target.id)
      toast.success(t('revoked'))
      onDeleted()
    } catch {
      toast.error(t('error'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <DialogPrimitive.Root
      open={target !== null}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
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
          <DialogPrimitive.Title className="text-base font-semibold">
            {t('title', { label: target?.label ?? '' })}
          </DialogPrimitive.Title>
          <p className="mt-3 text-sm text-muted-foreground">{t('warning')}</p>
          <div className="mt-5 flex items-center justify-end gap-2">
            <DialogPrimitive.Close
              render={
                <Button type="button" variant="ghost" size="sm" disabled={submitting}>
                  {t('cancel')}
                </Button>
              }
            />
            <Button
              type="button"
              size="sm"
              variant="destructive"
              onClick={() => void handleDelete()}
              disabled={submitting}
            >
              {submitting ? t('revoking') : t('confirm')}
            </Button>
          </div>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
