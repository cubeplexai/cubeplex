'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import type { MySandboxOut } from '@cubebox/core'
import { deleteMySandbox, restartMySandbox, useMySandboxes } from '@/hooks/useMySandboxes'
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
import { topicDisplayTitle } from '@/lib/topicTitle'
import { StatusBadge } from './StatusBadge'

interface SandboxCardProps {
  sandbox: MySandboxOut
  wsId: string
  onMutated: () => void
}

/**
 * One sandbox row in the settings list: status badge, scope label, last
 * active time, and Restart / Delete actions guarded by confirm dialogs.
 * Restart = stop the container, keep the row + files (spec §7.3).
 * Delete = soft-delete the row + stop the container; files left for operator.
 */
export function SandboxCard({ sandbox, wsId, onMutated }: SandboxCardProps) {
  const t = useTranslations('wsSandboxes')
  const tTopics = useTranslations('topics')
  const tSidebar = useTranslations('sidebar')
  const tTime = useTranslations('time')
  // `mutate` is bound to the same SWR key the panel reads, so a restart/
  // delete revalidates the whole list (status flips to "Off", row disappears).
  const { mutate } = useMySandboxes(wsId)

  const [restartOpen, setRestartOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [busy, setBusy] = useState<'restart' | 'delete' | null>(null)

  // scope → human label (spec §7.4). conversation/topic fall back to
  // "(deleted)" when the backing row is gone (scope_title null). Empty
  // titles (IM topics without a resolved channel name) keep the row and
  // use the localized empty-name label — do not treat "" as deleted.
  const deleted = t('scope.deleted')
  let label: string
  switch (sandbox.scope_type) {
    case 'user':
      label = t('scope.user')
      break
    case 'conversation':
      label = t('scope.conversation', {
        title:
          sandbox.scope_title === null
            ? deleted
            : topicDisplayTitle(sandbox.scope_title, tSidebar('untitledChat')),
      })
      break
    case 'topic':
      label = t('scope.topic', {
        title:
          sandbox.scope_title === null
            ? deleted
            : topicDisplayTitle(sandbox.scope_title, tTopics('newGroupChat')),
      })
      break
    default:
      label = t('scope.unknown', { type: sandbox.scope_type })
  }

  const lastActive = sandbox.last_activity_at
    ? t('lastActive', { time: relativeFromIso(sandbox.last_activity_at) })
    : t('lastActiveNever')

  const onConfirmRestart = async () => {
    setBusy('restart')
    try {
      await restartMySandbox(wsId, sandbox.id)
      toast.success(t('restartSuccess'))
      setRestartOpen(false)
      await mutate()
      onMutated()
    } catch (err) {
      toast.error(t('restartFailed'), { description: describeErr(err) })
    } finally {
      setBusy(null)
    }
  }

  const onConfirmDelete = async () => {
    setBusy('delete')
    try {
      await deleteMySandbox(wsId, sandbox.id)
      toast.success(t('deleteSuccess'))
      setDeleteOpen(false)
      await mutate()
      onMutated()
    } catch (err) {
      toast.error(t('deleteFailed'), { description: describeErr(err) })
    } finally {
      setBusy(null)
    }
  }

  return (
    <li className="flex items-center justify-between gap-4 p-4">
      <div className="min-w-0 space-y-1">
        <div className="flex items-center gap-2">
          <StatusBadge status={sandbox.status} />
          <span className="truncate text-sm font-medium">{label}</span>
        </div>
        <p className="text-xs text-muted-foreground">{lastActive}</p>
      </div>
      <div className="flex shrink-0 gap-2">
        <Button variant="outline" size="sm" onClick={() => setRestartOpen(true)}>
          {t('restart')}
        </Button>
        <Button variant="destructive" size="sm" onClick={() => setDeleteOpen(true)}>
          {t('delete')}
        </Button>
      </div>

      <ConfirmDialog
        open={restartOpen}
        onOpenChange={setRestartOpen}
        title={t('restartConfirmTitle')}
        body={t('restartConfirmBody')}
        confirmLabel={busy === 'restart' ? t('restarting') : t('restartConfirm')}
        confirmVariant="default"
        cancelLabel={t('cancel')}
        busy={busy !== null}
        onConfirm={() => void onConfirmRestart()}
      />
      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title={t('deleteConfirmTitle')}
        body={t('deleteConfirmBody')}
        confirmLabel={busy === 'delete' ? t('deleting') : t('deleteConfirm')}
        confirmVariant="destructive"
        cancelLabel={t('cancel')}
        busy={busy !== null}
        onConfirm={() => void onConfirmDelete()}
      />
    </li>
  )

  /** Relative time ("5m ago") from an ISO string using the shared `time` keys. */
  function relativeFromIso(iso: string): string {
    const then = new Date(iso).getTime()
    if (Number.isNaN(then)) return '—'
    const diffMs = Date.now() - then
    if (diffMs < 0) return tTime('justNow')
    const min = Math.floor(diffMs / 60000)
    if (min < 1) return tTime('justNow')
    if (min < 60) return tTime('minutesAgo', { n: min })
    const hr = Math.floor(min / 60)
    if (hr < 24) return tTime('hoursAgo', { n: hr })
    return tTime('daysAgo', { n: Math.floor(hr / 24) })
  }
}

function describeErr(err: unknown): string {
  if (err instanceof Error) return err.message
  return String(err)
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
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{body}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>{cancelLabel}</AlertDialogCancel>
          <AlertDialogAction disabled={busy} variant={confirmVariant} onClick={onConfirm}>
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
