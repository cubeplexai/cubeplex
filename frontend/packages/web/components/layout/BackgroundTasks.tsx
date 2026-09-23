'use client'

import { useCallback, useEffect, useState } from 'react'
import { CircleStop, Loader2, RefreshCw } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useShallow } from 'zustand/react/shallow'
import { createApiClient, useMessageStore } from '@cubeplex/core'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'

const ACTIVE_REFRESH_MS = 5_000
const BASELINE_REFRESH_MS = 30_000
const MAX_RETRY_MS = 120_000

interface BackgroundTasksProps {
  conversationId: string
}

function taskIsInflight(state: string): boolean {
  return ['starting', 'running', 'waiting_input', 'unknown'].includes(state)
}

export function BackgroundTasks({ conversationId }: BackgroundTasksProps) {
  const { workspaceId } = useWorkspaceContext()
  const t = useTranslations('backgroundTasks')
  const { tasks, summary, stopAll, runControl, refreshError } = useMessageStore(
    useShallow((state) => ({
      tasks: state.backgroundTasks?.[conversationId] ?? [],
      summary: state.backgroundSummary?.[conversationId],
      stopAll: state.stopAllStatus?.[conversationId],
      runControl: state.runControl?.[conversationId],
      refreshError: state.backgroundRefreshError?.[conversationId],
    })),
  )
  const refreshBackground = useMessageStore((state) => state.refreshBackground)
  const loadMessages = useMessageStore((state) => state.loadMessages)
  const stopTask = useMessageStore((state) => state.stopTask)
  const stopAllWork = useMessageStore((state) => state.stopAllWork)
  const [stoppingTaskId, setStoppingTaskId] = useState<string | null>(null)
  const [stoppingAll, setStoppingAll] = useState(false)

  const client = useCallback(() => {
    const next = createApiClient('')
    if (workspaceId) next.setWorkspaceId(workspaceId)
    return next
  }, [workspaceId])

  useEffect(() => {
    if (!refreshBackground || !loadMessages) return
    let disposed = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let failureCount = 0
    let lastBaselineAt = Date.now()

    const schedule = (delay: number) => {
      if (disposed) return
      timer = setTimeout(() => void tick(), delay)
    }
    const tick = async () => {
      if (disposed) return
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
      try {
        await refreshBackground(client(), conversationId)
        failureCount = 0
        const state = useMessageStore.getState()
        const nextSummary = state.backgroundSummary[conversationId]
        const now = Date.now()
        if (
          now - lastBaselineAt >= BASELINE_REFRESH_MS &&
          state.streamingConversationId !== conversationId
        ) {
          await loadMessages(client(), conversationId, {
            force: true,
            preserveOtherConversationStream: true,
          })
          lastBaselineAt = now
        }
        const hasWork = Boolean(
          nextSummary?.has_inflight || nextSummary?.has_pending || nextSummary?.has_cleanup,
        )
        schedule(hasWork ? ACTIVE_REFRESH_MS : BASELINE_REFRESH_MS)
      } catch {
        failureCount += 1
        schedule(Math.min(ACTIVE_REFRESH_MS * 2 ** failureCount, MAX_RETRY_MS))
      }
    }
    const onVisibility = () => {
      if (document.visibilityState !== 'visible') return
      if (timer) clearTimeout(timer)
      void tick()
    }
    document.addEventListener('visibilitychange', onVisibility)
    schedule(0)
    return () => {
      disposed = true
      if (timer) clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [client, conversationId, loadMessages, refreshBackground])

  const onStopTask = async (taskId: string) => {
    setStoppingTaskId(taskId)
    try {
      await stopTask(client(), conversationId, taskId)
    } catch {
      toast.error(t('stopFailed'))
    } finally {
      setStoppingTaskId(null)
    }
  }

  const onStopAll = async () => {
    setStoppingAll(true)
    try {
      await stopAllWork(client(), conversationId)
      await refreshBackground(client(), conversationId)
    } catch {
      toast.error(t('stopAllFailed'))
    } finally {
      setStoppingAll(false)
    }
  }

  const visibleTasks = tasks.filter(
    (task) => taskIsInflight(task.state) || task.cleanup_pending || task.notification.has_pending,
  )
  const hasBackground = Boolean(
    visibleTasks.length > 0 ||
    summary?.has_inflight ||
    summary?.has_pending ||
    summary?.has_cleanup ||
    refreshError,
  )
  if (!hasBackground) return null

  const canStopAll = Boolean(summary?.can_stop || runControl?.can_stop)
  return (
    <section className="mb-2 rounded-lg border border-border/70 bg-muted/30 px-3 py-2 text-xs">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="font-medium text-foreground">{t('title')}</p>
          <p className="truncate text-muted-foreground">
            {stopAll?.cleanup_pending ? t('stoppingAll') : t('description')}
          </p>
        </div>
        {canStopAll && !stopAll?.cleanup_pending ? (
          <Button
            type="button"
            variant="outline"
            size="xs"
            disabled={stoppingAll}
            onClick={() => void onStopAll()}
          >
            {stoppingAll ? <Loader2 className="size-3 animate-spin" /> : <CircleStop />}
            {stoppingAll ? t('submittingStop') : t('stopAll')}
          </Button>
        ) : null}
      </div>
      {visibleTasks.length > 0 ? (
        <ul className="mt-2 space-y-1.5">
          {visibleTasks.map((task) => {
            const stopping = task.stop_requested_at !== null || stoppingTaskId === task.id
            return (
              <li
                key={task.id}
                className="flex items-center gap-2 rounded-md bg-background/70 px-2 py-1.5"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-foreground">
                    {task.description || t('unnamed')}
                  </span>
                  <span className="text-muted-foreground">
                    {stopping
                      ? t('stopping')
                      : task.cleanup_pending
                        ? t('finalizing')
                        : t(`states.${task.state}`)}
                  </span>
                </span>
                {task.capabilities.can_stop ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="xs"
                    disabled={stopping}
                    onClick={() => void onStopTask(task.id)}
                  >
                    {stoppingTaskId === task.id ? (
                      <Loader2 className="size-3 animate-spin" />
                    ) : (
                      <CircleStop />
                    )}
                    {t('stopTask')}
                  </Button>
                ) : taskIsInflight(task.state) && stopping ? (
                  <Loader2 className="size-3 animate-spin text-muted-foreground" />
                ) : null}
              </li>
            )
          })}
        </ul>
      ) : null}
      {refreshError ? (
        <div className="mt-2 flex items-center gap-2 text-destructive">
          <RefreshCw className="size-3" />
          <span>{t('refreshFailed')}</span>
        </div>
      ) : null}
    </section>
  )
}
