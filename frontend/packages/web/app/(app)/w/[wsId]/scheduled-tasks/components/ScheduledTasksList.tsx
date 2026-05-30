'use client'

import { useEffect, useMemo, useState } from 'react'
import { createApiClient, listScheduledTasks, useAuthStore, useWorkspaceStore } from '@cubebox/core'
import type { ScheduledTaskOut } from '@cubebox/core'
import { ScheduledTaskCard } from './ScheduledTaskCard'
import { ScheduledTaskRunsPanel } from './ScheduledTaskRunsPanel'

interface ScheduledTasksListProps {
  wsId: string
  onEdit: (task: ScheduledTaskOut) => void
  refreshKey: number
}

export function ScheduledTasksList({
  wsId,
  onEdit,
  refreshKey,
}: ScheduledTasksListProps): React.ReactElement {
  const [tasks, setTasks] = useState<ScheduledTaskOut[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)

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
        if (!cancelled)
          setError(err instanceof Error ? err.message : 'Failed to load scheduled tasks')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [client, refreshKey])

  function handleUpdate(updated: ScheduledTaskOut): void {
    setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)))
  }

  function handleDelete(id: string): void {
    setTasks((prev) => prev.filter((t) => t.id !== id))
    if (selectedId === id) setSelectedId(null)
  }

  const selectedTask = tasks.find((t) => t.id === selectedId) ?? null

  if (loading) {
    return (
      <div className="flex flex-col gap-3">
        {[...Array(3)].map((_, i) => (
          <div key={i} className="h-28 rounded-xl border border-border bg-muted/30 animate-pulse" />
        ))}
      </div>
    )
  }

  if (error) {
    return (
      <div className="rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
        {error}
      </div>
    )
  }

  if (tasks.length === 0) {
    return (
      <div
        className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border py-16 text-center"
        data-testid="empty-state"
      >
        <p className="text-sm font-medium text-muted-foreground">No scheduled tasks yet</p>
        <p className="text-xs text-muted-foreground/60">
          Create a task to run prompts on a schedule automatically.
        </p>
      </div>
    )
  }

  return (
    <div className="flex gap-4">
      {/* Task list */}
      <div className="flex flex-col gap-2 w-full max-w-lg">
        {tasks.map((task) => {
          const canMutate = isAdmin || task.owner_user_id === currentUserId
          return (
            <ScheduledTaskCard
              key={task.id}
              wsId={wsId}
              task={task}
              isSelected={selectedId === task.id}
              canMutate={canMutate}
              onSelect={() => setSelectedId(task.id === selectedId ? null : task.id)}
              onUpdate={handleUpdate}
              onDelete={handleDelete}
              onEdit={onEdit}
            />
          )
        })}
      </div>

      {/* Runs panel */}
      {selectedTask && (
        <div className="flex-1 min-w-0 rounded-xl border border-border bg-card px-4 py-4">
          <ScheduledTaskRunsPanel wsId={wsId} taskId={selectedTask.id} />
        </div>
      )}
    </div>
  )
}
