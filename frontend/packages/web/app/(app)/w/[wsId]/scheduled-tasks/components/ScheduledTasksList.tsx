'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { CalendarClock, Plus } from 'lucide-react'
import {
  createApiClient,
  deleteScheduledTask,
  listScheduledTasks,
  pauseScheduledTask,
  resumeScheduledTask,
  useAuthStore,
  useWorkspaceStore,
} from '@cubebox/core'
import type { ScheduledTaskOut } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/shared/EmptyState'
import { PANE_CONTENT_WIDTH } from '@/components/shared/SectionHeader'
import { ListDetailLayout } from '@/components/shared/ListDetailLayout'
import { ScheduledTaskCard } from './ScheduledTaskCard'
import { ScheduledTaskDetail } from './ScheduledTaskDetail'

interface ScheduledTasksListProps {
  wsId: string
  onEdit: (task: ScheduledTaskOut) => void
  onCreate: () => void
  refreshKey: number
}

export function ScheduledTasksList({
  wsId,
  onEdit,
  onCreate,
  refreshKey,
}: ScheduledTasksListProps): React.ReactElement {
  const t = useTranslations('scheduledTasks')
  const [tasks, setTasks] = useState<ScheduledTaskOut[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [pendingId, setPendingId] = useState<string | null>(null)

  const currentUserId = useAuthStore((s) => s.user?.id)
  const wsRole = useWorkspaceStore((s) => s.workspaces.find((w) => w.id === wsId)?.role)
  const isAdmin = wsRole === 'admin'

  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  useEffect(() => {
    let cancelled = false
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true)
    setError(null)
    listScheduledTasks(client)
      .then((data) => {
        if (!cancelled) setTasks(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : t('loadFailed'))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [client, refreshKey, t])

  function handleUpdate(updated: ScheduledTaskOut): void {
    setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)))
  }

  function handleDelete(id: string): void {
    setTasks((prev) => prev.filter((t) => t.id !== id))
    if (selectedId === id) setSelectedId(null)
  }

  async function handlePause(task: ScheduledTaskOut): Promise<void> {
    setPendingId(task.id)
    try {
      handleUpdate(await pauseScheduledTask(client, task.id))
    } finally {
      setPendingId(null)
    }
  }

  async function handleResume(task: ScheduledTaskOut): Promise<void> {
    setPendingId(task.id)
    try {
      handleUpdate(await resumeScheduledTask(client, task.id))
    } finally {
      setPendingId(null)
    }
  }

  async function handleConfirmDelete(task: ScheduledTaskOut): Promise<void> {
    if (!confirm(t('deleteConfirm', { name: task.name }))) return
    setPendingId(task.id)
    try {
      await deleteScheduledTask(client, task.id)
      handleDelete(task.id)
    } finally {
      setPendingId(null)
    }
  }

  const selectedTask = tasks.find((t) => t.id === selectedId) ?? null

  const wrapBody = (node: React.ReactNode): React.ReactElement => (
    <div className="flex-1 overflow-y-auto px-6 py-6">
      <div className={PANE_CONTENT_WIDTH}>{node}</div>
    </div>
  )

  if (loading) {
    return wrapBody(
      <div className="flex flex-col gap-3">
        {[...Array(3)].map((_, i) => (
          <div key={i} className="h-28 rounded-xl border border-border bg-muted/30 animate-pulse" />
        ))}
      </div>,
    )
  }

  if (error) {
    return wrapBody(
      <div className="rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
        {error}
      </div>,
    )
  }

  if (tasks.length === 0) {
    return wrapBody(
      <EmptyState
        icon={CalendarClock}
        title={t('emptyTitle')}
        description={t('emptyHint')}
        data-testid="empty-state"
        action={
          <Button size="sm" className="gap-1.5" onClick={onCreate}>
            <Plus className="size-3.5" />
            {t('newTask')}
          </Button>
        }
      />,
    )
  }

  const selectedCanMutate =
    selectedTask != null && (isAdmin || selectedTask.owner_user_id === currentUserId)

  return (
    <ListDetailLayout
      selected={selectedTask !== null}
      list={
        <div className="flex flex-col gap-2">
          {tasks.map((task) => {
            const canMutate = isAdmin || task.owner_user_id === currentUserId
            return (
              <ScheduledTaskCard
                key={task.id}
                task={task}
                isSelected={selectedId === task.id}
                canMutate={canMutate}
                pending={pendingId === task.id}
                onSelect={() => setSelectedId(task.id)}
                onPause={() => void handlePause(task)}
                onResume={() => void handleResume(task)}
                onDelete={() => void handleConfirmDelete(task)}
                onEdit={onEdit}
              />
            )
          })}
        </div>
      }
      detail={
        selectedTask ? (
          <ScheduledTaskDetail
            wsId={wsId}
            task={selectedTask}
            canMutate={selectedCanMutate}
            pending={pendingId === selectedTask.id}
            backLabel={t('back')}
            onBack={() => setSelectedId(null)}
            onPause={() => void handlePause(selectedTask)}
            onResume={() => void handleResume(selectedTask)}
            onDelete={() => void handleConfirmDelete(selectedTask)}
            onEdit={onEdit}
          />
        ) : null
      }
      placeholder={t('selectHint')}
    />
  )
}
