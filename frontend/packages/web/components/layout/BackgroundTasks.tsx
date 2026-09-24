'use client'

import { useCallback, useEffect, useState } from 'react'
import { CircleStop, ListTodo, Loader2, RefreshCw } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useShallow } from 'zustand/react/shallow'
import { createApiClient, useMessageStore, usePanelStore } from '@cubeplex/core'
import type { BackgroundTask } from '@cubeplex/core'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/shared/EmptyState'
import { cn } from '@/lib/utils'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'

const ACTIVE_REFRESH_MS = 5_000
const BASELINE_REFRESH_MS = 30_000
const MAX_RETRY_MS = 120_000
const EMPTY_BACKGROUND_TASKS: BackgroundTask[] = []

interface BackgroundTasksProps {
  conversationId: string
}

function taskIsInflight(state: string): boolean {
  return ['starting', 'running', 'waiting_input', 'unknown'].includes(state)
}

function useBackgroundTaskRefresh(conversationId: string) {
  const { workspaceId } = useWorkspaceContext()
  const refreshBackground = useMessageStore((state) => state.refreshBackground)
  const loadMessages = useMessageStore((state) => state.loadMessages)
  const streamingConversationId = useMessageStore((state) => state.streamingConversationId)
  const client = useCallback(() => {
    const next = createApiClient('')
    if (workspaceId) next.setWorkspaceId(workspaceId)
    return next
  }, [workspaceId])

  useEffect(() => {
    if (!refreshBackground || !loadMessages) return
    let disposed = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let tickInFlight = false
    let failureCount = 0
    let lastBaselineAt = Date.now()

    const hasCurrentStopAllCleanup = () => {
      const state = useMessageStore.getState()
      const observedGeneration = (state.backgroundTasks[conversationId] ?? []).reduce(
        (latest, task) => Math.max(latest, task.execution_generation),
        state.executionGeneration[conversationId] ?? 0,
      )
      const status = state.stopAllStatus[conversationId]
      return status?.execution_generation === observedGeneration && status.cleanup_pending
    }

    const hasCurrentRunCleanup = () =>
      useMessageStore.getState().runControl[conversationId]?.cleanup_pending === true

    const schedule = (delay: number) => {
      if (disposed) return
      if (timer) clearTimeout(timer)
      timer = setTimeout(() => {
        timer = null
        void tick()
      }, delay)
    }
    const tick = async () => {
      if (disposed || tickInFlight) return
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
      tickInFlight = true
      try {
        await refreshBackground(client(), conversationId)
        failureCount = 0
        let state = useMessageStore.getState()
        const now = Date.now()
        if (
          (hasCurrentStopAllCleanup() ||
            hasCurrentRunCleanup() ||
            now - lastBaselineAt >= BASELINE_REFRESH_MS) &&
          state.streamingConversationId !== conversationId
        ) {
          await loadMessages(client(), conversationId, {
            preserveLoadedHistory: true,
            preserveOtherConversationStream: true,
            throwOnError: true,
          })
          lastBaselineAt = now
          state = useMessageStore.getState()
        }
        const nextSummary = state.backgroundSummary[conversationId]
        const hasWork = Boolean(
          nextSummary?.has_inflight ||
          nextSummary?.has_pending ||
          nextSummary?.has_cleanup ||
          hasCurrentRunCleanup() ||
          hasCurrentStopAllCleanup() ||
          state.streamingConversationId === conversationId,
        )
        schedule(hasWork ? ACTIVE_REFRESH_MS : BASELINE_REFRESH_MS)
      } catch {
        failureCount += 1
        schedule(Math.min(ACTIVE_REFRESH_MS * 2 ** failureCount, MAX_RETRY_MS))
      } finally {
        tickInFlight = false
      }
    }
    const onVisibility = () => {
      if (document.visibilityState !== 'visible') return
      if (timer) clearTimeout(timer)
      timer = null
      if (tickInFlight) return
      void tick()
    }
    document.addEventListener('visibilitychange', onVisibility)
    schedule(0)
    return () => {
      disposed = true
      if (timer) clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [client, conversationId, loadMessages, refreshBackground, streamingConversationId])
}

export function BackgroundTasksButton({ conversationId }: BackgroundTasksProps) {
  const t = useTranslations('backgroundTasks')
  const tasks = useMessageStore(
    (state) => state.backgroundTasks?.[conversationId] ?? EMPTY_BACKGROUND_TASKS,
  )
  const view = usePanelStore((state) => state.view)
  const openBackgroundTasks = usePanelStore((state) => state.openBackgroundTasks)
  const close = usePanelStore((state) => state.close)
  useBackgroundTaskRefresh(conversationId)

  const count = tasks.filter((task) => taskIsInflight(task.state)).length
  const selected = view.type === 'background-tasks' && view.conversationId === conversationId
  return (
    <button
      type="button"
      onClick={() => (selected ? close() : openBackgroundTasks(conversationId))}
      className={cn(
        'relative mr-1 cursor-pointer rounded p-1.5 text-muted-foreground',
        'hover:bg-accent transition-colors duration-fast',
      )}
      aria-label={count > 0 ? t('openWithCount', { count }) : t('open')}
      title={t('open')}
      aria-pressed={selected}
    >
      <ListTodo className="size-4" aria-hidden />
      {count > 0 && (
        <span
          className={cn(
            'absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center',
            'rounded-full bg-primary px-1 text-2xs tabular-nums text-primary-foreground',
          )}
          aria-hidden
        >
          {count}
        </span>
      )}
    </button>
  )
}

export function BackgroundTasks({ conversationId }: BackgroundTasksProps) {
  const { workspaceId } = useWorkspaceContext()
  const t = useTranslations('backgroundTasks')
  const { tasks, summary, stopAll, refreshError, refreshing, executionGeneration } =
    useMessageStore(
      useShallow((state) => ({
        tasks: state.backgroundTasks?.[conversationId] ?? EMPTY_BACKGROUND_TASKS,
        summary: state.backgroundSummary?.[conversationId],
        stopAll: state.stopAllStatus?.[conversationId],
        refreshError: state.backgroundRefreshError?.[conversationId],
        refreshing: state.refreshingBackground?.[conversationId] ?? false,
        executionGeneration: state.executionGeneration?.[conversationId] ?? 0,
      })),
    )
  const refreshBackground = useMessageStore((state) => state.refreshBackground)
  const stopTask = useMessageStore((state) => state.stopTask)
  const stopAllWork = useMessageStore((state) => state.stopAllWork)
  const [stoppingTaskId, setStoppingTaskId] = useState<string | null>(null)
  const [stoppingAll, setStoppingAll] = useState(false)

  const client = useCallback(() => {
    const next = createApiClient('')
    if (workspaceId) next.setWorkspaceId(workspaceId)
    return next
  }, [workspaceId])

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
    } catch {
      toast.error(t('stopAllFailed'))
      return
    } finally {
      setStoppingAll(false)
    }
    try {
      await refreshBackground(client(), conversationId)
    } catch {
      // The durable Stop-all request already succeeded. The polling loop will
      // retry this display-only refresh without misreporting the stop itself.
    }
  }

  const observedGeneration = tasks.reduce(
    (latest, task) => Math.max(latest, task.execution_generation),
    executionGeneration,
  )
  const currentStopAll = stopAll?.execution_generation === observedGeneration ? stopAll : null
  const canStopAll = Boolean(summary?.can_stop || tasks.some((task) => task.capabilities.can_stop))
  const orderedTasks = [...tasks].sort((left, right) =>
    right.created_at.localeCompare(left.created_at),
  )
  return (
    <section className="space-y-4 p-4 text-xs">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-muted-foreground">
            {currentStopAll?.cleanup_pending ? t('stoppingAll') : t('description')}
          </p>
        </div>
        {canStopAll && !currentStopAll?.cleanup_pending ? (
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
      {orderedTasks.length > 0 ? (
        <ul className="space-y-2">
          {orderedTasks.map((task) => {
            const stopping =
              taskIsInflight(task.state) &&
              (task.stop_requested_at !== null || stoppingTaskId === task.id)
            return (
              <li
                key={task.id}
                className="flex items-start gap-2 rounded-lg border border-border bg-card p-3"
              >
                <span className="min-w-0 flex-1">
                  <span className="block break-words font-medium text-foreground">
                    {task.description || t('unnamed')}
                  </span>
                  <span className="mt-1 block text-muted-foreground">
                    {stopping
                      ? t('stopping')
                      : task.cleanup_pending && !taskIsInflight(task.state)
                        ? t('finalizing')
                        : t(`states.${task.state}`)}
                  </span>
                  {task.result_summary && (
                    <span className="mt-2 block break-words text-muted-foreground">
                      {task.result_summary}
                    </span>
                  )}
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
      ) : refreshing ? (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden />
          {t('loading')}
        </div>
      ) : (
        <EmptyState icon={ListTodo} title={t('empty')} size="sm" />
      )}
      {refreshError ? (
        <div className="mt-2 flex items-center gap-2 text-destructive">
          <RefreshCw className="size-3" />
          <span>{t('refreshFailed')}</span>
        </div>
      ) : null}
    </section>
  )
}
