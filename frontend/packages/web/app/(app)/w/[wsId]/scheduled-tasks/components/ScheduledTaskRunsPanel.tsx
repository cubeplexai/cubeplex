'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import { History, RefreshCw, ExternalLink } from 'lucide-react'
import { createApiClient, listScheduledTaskRuns } from '@cubebox/core'
import type { ScheduledTaskRunOut, ScheduledTaskRunState } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/shared/EmptyState'
import { cn } from '@/lib/utils'

interface ScheduledTaskRunsPanelProps {
  wsId: string
  taskId: string
}

function formatDatetime(iso: string): string {
  return new Intl.DateTimeFormat('en', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(iso))
}

interface StateBadgeProps {
  state: ScheduledTaskRunState
}

function StateBadge({ state }: StateBadgeProps): React.ReactElement {
  const config: Record<ScheduledTaskRunState, { label: string; className: string }> = {
    claimed: {
      label: 'Claimed',
      className: 'bg-info-surface text-info-fg',
    },
    started: {
      label: 'Running',
      className: 'bg-warning-surface text-warning-fg',
    },
    succeeded: {
      label: 'Succeeded',
      className: 'bg-success-surface text-success-fg',
    },
    failed: {
      label: 'Failed',
      className: 'bg-danger-surface text-danger-fg',
    },
    skipped_missed: {
      label: 'Skipped (missed)',
      className: 'bg-muted text-muted-foreground',
    },
    skipped_busy_max_retries: {
      label: 'Skipped (busy)',
      className: 'bg-warning-surface text-warning-fg',
    },
  }
  const { label, className } = config[state] ?? {
    label: state,
    className: 'bg-muted text-muted-foreground',
  }
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium',
        className,
      )}
    >
      {label}
    </span>
  )
}

export function ScheduledTaskRunsPanel({
  wsId,
  taskId,
}: ScheduledTaskRunsPanelProps): React.ReactElement {
  const t = useTranslations('scheduledTasks')
  const [runs, setRuns] = useState<ScheduledTaskRunOut[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const client = (() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  })()

  const fetchRuns = useCallback(async (): Promise<void> => {
    try {
      const data = await listScheduledTaskRuns(client, taskId)
      setRuns(data)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load runs')
    } finally {
      setLoading(false)
    }
  }, [taskId, wsId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true)
    void fetchRuns()
    intervalRef.current = setInterval(() => void fetchRuns(), 10_000)
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [fetchRuns])

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">{t('runHistory')}</h3>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 gap-1.5 text-xs text-muted-foreground"
          onClick={() => void fetchRuns()}
        >
          <RefreshCw className="size-3" />
          {t('refresh')}
        </Button>
      </div>

      {loading && (
        <div className="flex flex-col gap-2">
          {[...Array(3)].map((_, i) => (
            <div
              key={i}
              className="h-14 rounded-lg border border-border bg-muted/30 animate-pulse"
            />
          ))}
        </div>
      )}

      {!loading && error && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      )}

      {!loading && !error && runs.length === 0 && (
        <EmptyState size="sm" icon={History} title={t('noRuns')} />
      )}

      {!loading && !error && runs.length > 0 && (
        <div className="flex flex-col divide-y divide-border rounded-lg border border-border">
          {runs.map((run) => (
            <div key={run.id} className="flex flex-col gap-1.5 px-3 py-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-muted-foreground font-mono">
                  {formatDatetime(run.scheduled_for)}
                </span>
                <StateBadge state={run.state} />
              </div>

              <div className="flex flex-wrap items-center gap-2">
                {run.retry_count > 0 && (
                  <span className="text-[11px] text-muted-foreground">
                    Retry #{run.retry_count}
                  </span>
                )}
                {run.next_retry_at && (
                  <span className="text-[11px] text-muted-foreground">
                    Next retry: {formatDatetime(run.next_retry_at)}
                  </span>
                )}
                {run.conversation_id && run.run_id && (
                  <a
                    href={`/w/${wsId}/conversations/${run.conversation_id}`}
                    className="inline-flex items-center gap-1 text-[11px] text-primary hover:underline"
                  >
                    <ExternalLink className="size-3" />
                    View conversation
                  </a>
                )}
              </div>

              {run.detail && (
                <p className="text-[11px] text-muted-foreground/70 truncate" title={run.detail}>
                  {run.detail}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
