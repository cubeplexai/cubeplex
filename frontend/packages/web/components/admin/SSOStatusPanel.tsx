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
import { CheckCircle2, ExternalLink, FlaskConical, Power, Trash2, XCircle } from 'lucide-react'
import {
  ApiError,
  activateSsoConnection,
  createApiClient,
  deactivateSsoConnection,
  deleteSsoConnection,
  validateSsoConnection,
  type SsoConnectionResponse,
  type SsoValidateCheck,
} from '@cubeplex/core'

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
  const [validating, setValidating] = useState(false)
  const [validateResult, setValidateResult] = useState<SsoValidateCheck[] | null>(null)
  const [showIdpAttrs, setShowIdpAttrs] = useState(false)

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

  const onValidate = useCallback(async () => {
    setValidating(true)
    setValidateResult(null)
    try {
      const res = await validateSsoConnection(client, connection.id)
      setValidateResult(res.checks)
      if (res.all_passed) {
        toast.success(t('panel.validateAllPassed'))
      } else {
        toast.error(t('panel.validateFailed'))
      }
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err)
      toast.error(msg)
    } finally {
      setValidating(false)
    }
  }, [client, connection.id, t])

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

        <Button
          type="button"
          variant="outline"
          onClick={() => void onValidate()}
          disabled={validating}
          data-testid="sso-validate"
          className="gap-1.5"
        >
          <FlaskConical className="size-3.5" />
          {validating ? t('panel.validating') : t('panel.validate')}
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

      {validateResult !== null && (
        <div className="border-t border-border px-5 py-4 space-y-2">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
            {t('panel.validateResults')}
          </p>
          {validateResult.map((check) => (
            <div key={check.name} className="flex items-start gap-2 text-sm">
              {check.passed ? (
                <CheckCircle2 className="size-4 text-success-fg mt-0.5 shrink-0" />
              ) : (
                <XCircle className="size-4 text-destructive mt-0.5 shrink-0" />
              )}
              <div className="min-w-0">
                <span className="font-medium">{check.name}</span>
                <span className="ml-2 text-muted-foreground">{check.detail}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {connection.last_idp_attributes && (
        <div className="border-t border-border px-5 py-4">
          <button
            type="button"
            onClick={() => setShowIdpAttrs((v) => !v)}
            className="text-xs font-medium text-muted-foreground uppercase tracking-wider hover:text-foreground transition-colors"
          >
            {t('panel.lastIdpAttributes')} {showIdpAttrs ? '▲' : '▼'}
          </button>
          {showIdpAttrs && (
            <pre className="mt-2 overflow-x-auto rounded-md bg-muted/50 p-3 text-[11px] leading-relaxed text-muted-foreground font-mono">
              {JSON.stringify(connection.last_idp_attributes, null, 2)}
            </pre>
          )}
        </div>
      )}

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
