'use client'

/**
 * SSO status panel — sits above the SSOConfigForm when an SSO connection
 * exists. Shows the current status badge, opens the org login page in a
 * new tab to test the SSO flow, and surfaces the Activate / Deactivate /
 * Delete actions guarded by a confirmation dialog.
 */

import { useCallback, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { ExternalLink, Power, Trash2 } from 'lucide-react'
import {
  ApiError,
  activateSsoConnection,
  createApiClient,
  deactivateSsoConnection,
  deleteSsoConnection,
  type SsoConnectionResponse,
} from '@cubebox/core'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'

interface SSOStatusPanelProps {
  connection: SsoConnectionResponse
  orgSlug: string
  onUpdated: (next: SsoConnectionResponse) => void
  onDeleted: () => void
}

type DialogKind = 'activate' | 'deactivate' | 'delete' | null

export function SSOStatusPanel({ connection, orgSlug, onUpdated, onDeleted }: SSOStatusPanelProps) {
  const t = useTranslations('adminAuthentication')
  const client = useMemo(() => createApiClient(''), [])
  const [dialog, setDialog] = useState<DialogKind>(null)
  const [busy, setBusy] = useState(false)

  const status = connection.status as 'testing' | 'active' | 'inactive'

  const statusBadge = useMemo(() => {
    if (status === 'active') {
      return <Badge className="bg-success-surface text-success-fg">{t('status.active')}</Badge>
    }
    if (status === 'testing') {
      return <Badge className="bg-warning-surface text-warning-fg">{t('status.testing')}</Badge>
    }
    return (
      <Badge variant="secondary" className="text-muted-foreground">
        {t('status.inactive')}
      </Badge>
    )
  }, [status, t])

  const statusHelp =
    status === 'active'
      ? t('status.activeHelp')
      : status === 'testing'
        ? t('status.testingHelp')
        : t('status.inactiveHelp')

  const onTestSso = useCallback(() => {
    if (!orgSlug) return
    window.open(`/login/${encodeURIComponent(orgSlug)}`, '_blank', 'noopener,noreferrer')
  }, [orgSlug])

  const runAction = useCallback(
    async (kind: 'activate' | 'deactivate' | 'delete') => {
      setBusy(true)
      try {
        if (kind === 'activate') {
          const next = await activateSsoConnection(client, connection.id)
          onUpdated(next)
          toast.success(t('status.active'))
        } else if (kind === 'deactivate') {
          const next = await deactivateSsoConnection(client, connection.id)
          onUpdated(next)
          toast.success(t('status.inactive'))
        } else {
          await deleteSsoConnection(client, connection.id)
          onDeleted()
        }
        setDialog(null)
      } catch (err) {
        const msg = err instanceof ApiError ? err.message : String(err)
        toast.error(msg)
      } finally {
        setBusy(false)
      }
    },
    [client, connection.id, onUpdated, onDeleted, t],
  )

  return (
    <section
      className="rounded-xl border border-border/70 bg-card shadow-sm"
      data-testid="sso-status-panel"
    >
      <header className="border-b border-border px-5 py-3.5">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className="text-sm font-medium">{connection.display_name}</span>
            {statusBadge}
            <span className="text-xs uppercase tracking-wider text-muted-foreground">
              {connection.protocol}
            </span>
          </div>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">{statusHelp}</p>
      </header>
      <div className="flex flex-wrap items-center gap-2 px-5 py-4">
        <Button
          type="button"
          variant="outline"
          onClick={onTestSso}
          disabled={!orgSlug}
          data-testid="sso-test"
          className="gap-1.5"
        >
          <ExternalLink className="size-3.5" />
          {t('panel.testSso')}
        </Button>

        {status === 'testing' && (
          <Button
            type="button"
            onClick={() => setDialog('activate')}
            data-testid="sso-activate"
            className="gap-1.5"
          >
            <Power className="size-3.5" />
            {t('panel.activate')}
          </Button>
        )}
        {status === 'active' && (
          <Button
            type="button"
            variant="outline"
            onClick={() => setDialog('deactivate')}
            data-testid="sso-deactivate"
            className="gap-1.5"
          >
            <Power className="size-3.5" />
            {t('panel.deactivate')}
          </Button>
        )}
        {status === 'inactive' && (
          <Button
            type="button"
            variant="destructive"
            onClick={() => setDialog('delete')}
            data-testid="sso-delete"
            className="gap-1.5"
          >
            <Trash2 className="size-3.5" />
            {t('panel.delete')}
          </Button>
        )}
        <p className="ml-auto max-w-md text-xs text-muted-foreground">{t('panel.testSsoHelp')}</p>
      </div>

      <ConfirmDialog
        open={dialog === 'activate'}
        onOpenChange={(v: boolean) => {
          if (!v) setDialog(null)
        }}
        title={t('panel.activateConfirmTitle')}
        body={t('panel.activateConfirmBody')}
        confirmLabel={busy ? t('panel.activating') : t('panel.activateConfirm')}
        confirmVariant="default"
        cancelLabel={t('panel.cancel')}
        busy={busy}
        onConfirm={() => void runAction('activate')}
      />
      <ConfirmDialog
        open={dialog === 'deactivate'}
        onOpenChange={(v: boolean) => {
          if (!v) setDialog(null)
        }}
        title={t('panel.deactivateConfirmTitle')}
        body={t('panel.deactivateConfirmBody')}
        confirmLabel={busy ? t('panel.deactivating') : t('panel.deactivateConfirm')}
        confirmVariant="default"
        cancelLabel={t('panel.cancel')}
        busy={busy}
        onConfirm={() => void runAction('deactivate')}
      />
      <ConfirmDialog
        open={dialog === 'delete'}
        onOpenChange={(v: boolean) => {
          if (!v) setDialog(null)
        }}
        title={t('panel.deleteConfirmTitle')}
        body={t('panel.deleteConfirmBody')}
        confirmLabel={busy ? t('panel.deleting') : t('panel.deleteConfirm')}
        confirmVariant="destructive"
        cancelLabel={t('panel.cancel')}
        busy={busy}
        onConfirm={() => void runAction('delete')}
      />
    </section>
  )
}

function ConfirmDialog({
  open,
  onOpenChange,
  title,
  body,
  confirmLabel,
  confirmVariant,
  cancelLabel,
  busy,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  title: string
  body: string
  confirmLabel: string
  confirmVariant: 'default' | 'destructive'
  cancelLabel: string
  busy: boolean
  onConfirm: () => void
}) {
  const handleOpenChange = (v: boolean): void => onOpenChange(v)
  return (
    <AlertDialog open={open} onOpenChange={handleOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{body}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>{cancelLabel}</AlertDialogCancel>
          <AlertDialogAction
            disabled={busy}
            variant={confirmVariant}
            onClick={onConfirm}
            data-testid="sso-confirm"
          >
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
