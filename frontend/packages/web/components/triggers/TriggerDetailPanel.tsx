'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { Inbox, RotateCcw } from 'lucide-react'
import { createApiClient, useTriggerStore, type TriggerEvent } from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { DetailPanel } from '@/components/shared/DetailPanel'
import { EmptyState } from '@/components/shared/EmptyState'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { CopyIngestUrl } from './CopyIngestUrl'
import { SecretRevealAndRotate } from './SecretRevealAndRotate'
import { DestinationCell } from './DestinationCell'

interface TriggerDetailPanelProps {
  wsId: string
  triggerId: string
  onClose?: () => void
}

export function TriggerDetailPanel({ wsId, triggerId, onClose }: TriggerDetailPanelProps) {
  const t = useTranslations('triggers')
  const router = useRouter()
  const client = useMemo(() => createApiClient(''), [])

  const {
    triggers,
    loading,
    load,
    update,
    remove,
    rotate,
    eventsByTrigger,
    eventsLoading,
    loadEvents,
    replay,
  } = useTriggerStore()

  const [deletingConfirm, setDeletingConfirm] = useState(false)
  const [statusFilter, setStatusFilter] = useState<string>('')

  const trigger = triggers.find((t) => t.id === triggerId)
  const events = eventsByTrigger[triggerId] ?? []

  useEffect(() => {
    void load(client, wsId)
  }, [client, wsId, load])

  useEffect(() => {
    void loadEvents(client, wsId, triggerId, statusFilter ? { status: statusFilter } : undefined)
  }, [client, wsId, triggerId, statusFilter, loadEvents])

  const handleToggleEnabled = useCallback(async () => {
    if (!trigger) return
    await update(client, wsId, triggerId, { enabled: !trigger.enabled })
  }, [client, wsId, triggerId, trigger, update])

  const handleRotate = useCallback(
    async (newSecret: string, overlapSeconds: number) => {
      await rotate(client, wsId, triggerId, {
        new_webhook_secret: newSecret,
        overlap_seconds: overlapSeconds,
      })
    },
    [client, wsId, triggerId, rotate],
  )

  const handleBack = useCallback(() => {
    if (onClose) onClose()
    else router.push(`/w/${wsId}/triggers`)
  }, [onClose, router, wsId])

  const handleDelete = useCallback(async () => {
    await remove(client, wsId, triggerId)
    handleBack()
  }, [client, wsId, triggerId, remove, handleBack])

  const handleReplay = useCallback(
    async (eventId: string) => {
      await replay(client, wsId, triggerId, eventId)
    },
    [client, wsId, triggerId, replay],
  )

  function formatDate(iso: string | null | undefined): string {
    if (!iso) return '—'
    try {
      return new Date(iso).toLocaleString()
    } catch {
      return iso
    }
  }

  if (loading && !trigger) {
    return (
      <div className="flex h-full items-center justify-center py-10 text-center text-xs text-muted-foreground">
        {t('loading')}
      </div>
    )
  }

  if (!trigger) {
    return (
      <DetailPanel title={t('notFound')} onBack={handleBack} backLabel={t('backToList')}>
        <p className="text-sm text-muted-foreground">{t('notFound')}</p>
      </DetailPanel>
    )
  }

  const statusFilterOptions = [
    '',
    'accepted',
    'failed',
    'dead_lettered',
    'rate_limited',
    'filtered_out',
    'duplicate',
  ]

  return (
    <DetailPanel
      onBack={handleBack}
      backLabel={t('backToList')}
      title={trigger.name}
      badge={
        trigger.enabled ? (
          <Badge
            variant="default"
            className="bg-success-solid/15 text-success-fg border-success-border hover:bg-success-solid/15"
          >
            {t('statusEnabled')}
          </Badge>
        ) : (
          <Badge variant="secondary">{t('statusDisabled')}</Badge>
        )
      }
      subtitle={t('createdAt', { date: formatDate(trigger.created_at) })}
      actions={
        <Button
          variant="destructive"
          size="sm"
          onClick={() => setDeletingConfirm(true)}
          data-testid="delete-trigger-btn"
        >
          {t('delete')}
        </Button>
      }
    >
      <div className="flex flex-col gap-6">
        {/* Counters */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <CounterCard
            label={t('counterTotal')}
            value={trigger.events_total}
            testId="counter-total"
          />
          <CounterCard
            label={t('counterSuccess')}
            value={trigger.events_success}
            testId="counter-success"
          />
          <CounterCard
            label={t('counterFailed')}
            value={trigger.events_failed}
            testId="counter-failed"
          />
          <CounterCard
            label={t('counterDedup')}
            value={trigger.events_dedup_dropped}
            testId="counter-dedup"
          />
        </div>

        {/* Actions */}
        <div className="rounded-xl border border-border/70 bg-card/40 p-4 flex flex-col gap-4 shadow-sm">
          <div className="flex items-center justify-between">
            <div className="flex flex-col gap-0.5">
              <span className="text-sm font-medium">{t('destinationLabel')}</span>
              <span className="text-xs text-muted-foreground">
                {t('destinationLabelDetailHint')}
              </span>
            </div>
            <DestinationCell wsId={wsId} trigger={trigger} />
          </div>

          <div className="border-t border-border/40 pt-4 flex items-center justify-between">
            <div className="flex flex-col gap-0.5">
              <span className="text-sm font-medium">{t('ingestUrl')}</span>
              <span className="text-xs text-muted-foreground">{t('ingestUrlHint')}</span>
            </div>
            <CopyIngestUrl wsId={wsId} triggerId={triggerId} />
          </div>

          <div className="border-t border-border/40 pt-4 flex items-center justify-between">
            <div className="flex flex-col gap-0.5">
              <span className="text-sm font-medium">{t('secretManagement')}</span>
              <span className="text-xs text-muted-foreground">{t('secretManagementHint')}</span>
            </div>
            <SecretRevealAndRotate
              onRotate={handleRotate}
              previousSecretExpiresAt={trigger.previous_secret_expires_at}
            />
          </div>

          <div className="border-t border-border/40 pt-4 flex items-center justify-between">
            <div className="flex flex-col gap-0.5">
              <span className="text-sm font-medium">{t('enabledSwitch')}</span>
              <span className="text-xs text-muted-foreground">{t('enabledSwitchHint')}</span>
            </div>
            <Switch
              checked={trigger.enabled}
              onCheckedChange={() => void handleToggleEnabled()}
              data-testid="trigger-enabled-switch"
            />
          </div>
        </div>

        {/* Events */}
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">{t('recentEvents')}</h3>
            <Button
              variant="ghost"
              size="sm"
              className="h-6 gap-1 text-xs"
              onClick={() =>
                void loadEvents(
                  client,
                  wsId,
                  triggerId,
                  statusFilter ? { status: statusFilter } : undefined,
                )
              }
            >
              <RotateCcw className="size-3" />
              {t('refresh')}
            </Button>
          </div>

          <div className="flex flex-wrap gap-1">
            {statusFilterOptions.map((s) => (
              <button
                key={s || 'all'}
                onClick={() => setStatusFilter(s)}
                className={`rounded px-2 py-0.5 text-xs transition-colors ${
                  statusFilter === s
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground'
                }`}
              >
                {s || t('filterAll')}
              </button>
            ))}
          </div>

          {eventsLoading ? (
            <div className="py-6 text-center text-xs text-muted-foreground">{t('loading')}</div>
          ) : events.length === 0 ? (
            <EmptyState size="sm" icon={Inbox} title={t('noEvents')} />
          ) : (
            <div className="rounded-xl border border-border/70 bg-card/40 shadow-sm">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="text-xs">{t('eventColStatus')}</TableHead>
                    <TableHead className="text-xs">{t('eventColReceived')}</TableHead>
                    <TableHead className="text-xs">{t('eventColAttempts')}</TableHead>
                    <TableHead className="text-xs">{t('eventColRunId')}</TableHead>
                    <TableHead className="text-xs w-[80px]"></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {events.map((event) => (
                    <EventRow
                      key={event.id}
                      event={event}
                      onReplay={handleReplay}
                      formatDate={formatDate}
                      t={t}
                    />
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </div>

        {/* Delete confirm overlay */}
        {deletingConfirm && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
            <div className="w-[min(400px,calc(100vw-32px))] rounded-xl border border-border bg-popover p-5 shadow-2xl">
              <h3 className="text-base font-semibold">{t('deleteTitle')}</h3>
              <p className="mt-2 text-sm text-muted-foreground">{t('deleteConfirm')}</p>
              <div className="mt-4 flex items-center justify-end gap-2">
                <Button variant="ghost" size="sm" onClick={() => setDeletingConfirm(false)}>
                  {t('cancel')}
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => void handleDelete()}
                  data-testid="confirm-delete-trigger-btn"
                >
                  {t('delete')}
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>
    </DetailPanel>
  )
}

function CounterCard({ label, value, testId }: { label: string; value: number; testId: string }) {
  return (
    <div
      className="rounded-xl border border-border/70 bg-card/40 p-4 shadow-sm flex flex-col gap-1"
      data-testid={testId}
    >
      <span className="text-2xl font-bold tabular-nums">{value}</span>
      <span className="text-xs text-muted-foreground">{label}</span>
    </div>
  )
}

interface EventRowProps {
  event: TriggerEvent
  onReplay: (eventId: string) => Promise<void>
  formatDate: (iso: string | null | undefined) => string
  t: ReturnType<typeof useTranslations<'triggers'>>
}

function EventRow({ event, onReplay, formatDate, t }: EventRowProps) {
  const [replaying, setReplaying] = useState(false)

  async function handleReplay(): Promise<void> {
    setReplaying(true)
    try {
      await onReplay(event.id)
    } finally {
      setReplaying(false)
    }
  }

  const statusColorMap: Record<string, string> = {
    accepted: 'text-success-fg',
    dead_lettered: 'text-destructive',
    failed: 'text-destructive',
    rate_limited: 'text-warning-fg',
    filtered_out: 'text-muted-foreground',
    duplicate: 'text-muted-foreground',
  }

  const color = statusColorMap[event.status] ?? 'text-foreground'

  return (
    <TableRow>
      <TableCell>
        <span className={`text-xs font-medium ${color}`}>{event.status}</span>
        {event.last_error && (
          <p
            className="text-xs text-muted-foreground truncate max-w-[200px]"
            title={event.last_error}
          >
            {event.last_error}
          </p>
        )}
      </TableCell>
      <TableCell className="text-xs text-muted-foreground">
        {formatDate(event.received_at)}
      </TableCell>
      <TableCell className="text-xs">{event.attempts}</TableCell>
      <TableCell className="text-xs font-mono text-muted-foreground">
        {event.resulting_run_id ? (
          <span className="truncate block max-w-[140px]" title={event.resulting_run_id}>
            {event.resulting_run_id}
          </span>
        ) : (
          '—'
        )}
      </TableCell>
      <TableCell>
        {event.status === 'dead_lettered' && (
          <Button
            variant="outline"
            size="sm"
            className="h-6 text-xs"
            onClick={() => void handleReplay()}
            disabled={replaying}
          >
            {replaying ? t('replaying') : t('replay')}
          </Button>
        )}
      </TableCell>
    </TableRow>
  )
}
