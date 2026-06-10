'use client'

import { useState } from 'react'
import { MoreHorizontal, Pause, Play, Pencil, Trash2 } from 'lucide-react'
import {
  createApiClient,
  pauseScheduledTask,
  resumeScheduledTask,
  deleteScheduledTask,
} from '@cubebox/core'
import type { ScheduledTaskOut } from '@cubebox/core'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'

interface ScheduledTaskCardProps {
  wsId: string
  task: ScheduledTaskOut
  isSelected: boolean
  canMutate: boolean
  onSelect: () => void
  onUpdate: (task: ScheduledTaskOut) => void
  onDelete: (id: string) => void
  onEdit: (task: ScheduledTaskOut) => void
}

function formatScheduleSummary(task: ScheduledTaskOut): string {
  if (task.schedule_kind === 'cron') {
    const expr = task.cron_expr ?? ''
    return task.timezone && task.timezone !== 'UTC' ? `${expr} (${task.timezone})` : expr
  }
  if (task.schedule_kind === 'interval' && task.interval_seconds != null) {
    const secs = task.interval_seconds
    if (secs < 60) return `Every ${secs}s`
    if (secs < 3600) return `Every ${Math.round(secs / 60)} min`
    if (secs < 86400) {
      const h = secs / 3600
      return h === 1 ? 'Every hour' : `Every ${h} hours`
    }
    const d = secs / 86400
    return d === 1 ? 'Every day' : `Every ${d} days`
  }
  if (task.schedule_kind === 'once' && task.run_at) {
    return `Once at ${new Intl.DateTimeFormat('en', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(new Date(task.run_at))}`
  }
  return '—'
}

function formatNextFire(task: ScheduledTaskOut): string {
  if (task.status === 'paused') return 'Paused'
  if (!task.next_fire_at) return 'No upcoming run'
  const now = Date.now()
  const diff = new Date(task.next_fire_at).getTime() - now
  const absDiff = Math.abs(diff)
  if (absDiff < 60_000) return 'In less than a minute'
  if (absDiff < 3_600_000) return `In ${Math.round(absDiff / 60_000)} min`
  if (absDiff < 86_400_000) return `In ${Math.round(absDiff / 3_600_000)}h`
  return `In ${Math.round(absDiff / 86_400_000)}d`
}

export function ScheduledTaskCard({
  wsId,
  task,
  isSelected,
  canMutate,
  onSelect,
  onUpdate,
  onDelete,
  onEdit,
}: ScheduledTaskCardProps): React.ReactElement {
  const [pending, setPending] = useState(false)

  async function handlePause(): Promise<void> {
    setPending(true)
    try {
      const client = createApiClient('')
      client.setWorkspaceId(wsId)
      const updated = await pauseScheduledTask(client, task.id)
      onUpdate(updated)
    } finally {
      setPending(false)
    }
  }

  async function handleResume(): Promise<void> {
    setPending(true)
    try {
      const client = createApiClient('')
      client.setWorkspaceId(wsId)
      const updated = await resumeScheduledTask(client, task.id)
      onUpdate(updated)
    } finally {
      setPending(false)
    }
  }

  async function handleDelete(): Promise<void> {
    if (!confirm(`Delete "${task.name}"? This cannot be undone.`)) return
    setPending(true)
    try {
      const client = createApiClient('')
      client.setWorkspaceId(wsId)
      await deleteScheduledTask(client, task.id)
      onDelete(task.id)
    } finally {
      setPending(false)
    }
  }

  return (
    <div
      className={cn(
        'group relative rounded-xl border bg-card px-4 py-3.5 transition-colors cursor-pointer',
        isSelected
          ? 'border-primary/40 bg-primary/5 shadow-sm'
          : 'border-border hover:border-border/80 hover:bg-muted/30',
      )}
      onClick={onSelect}
      data-testid={`task-card-${task.id}`}
    >
      {isSelected && (
        <div className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-10 bg-primary rounded-r-full" />
      )}

      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold truncate">{task.name}</h3>
            <span
              className={cn(
                'inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium shrink-0',
                task.status === 'active'
                  ? 'bg-success-surface text-success-fg'
                  : 'bg-muted text-muted-foreground',
              )}
              data-testid={`status-badge-${task.id}`}
            >
              {task.status === 'active' ? 'Active' : 'Paused'}
            </span>
          </div>

          <p className="mt-0.5 text-xs text-muted-foreground font-mono">
            {formatScheduleSummary(task)}
          </p>

          <p className="mt-1 text-[11px] text-muted-foreground/70">{formatNextFire(task)}</p>
        </div>

        {canMutate && (
          <DropdownMenu>
            <DropdownMenuTrigger
              onClick={(e) => {
                e.stopPropagation()
              }}
              className={cn(
                'p-1 rounded hover:bg-accent text-muted-foreground hover:text-foreground',
                'shrink-0 opacity-0 group-hover:opacity-100 data-[popup-open]:opacity-100 transition-opacity',
              )}
              aria-label="Task actions"
              disabled={pending}
            >
              <MoreHorizontal className="size-3.5" />
            </DropdownMenuTrigger>
            <DropdownMenuContent
              align="end"
              side="bottom"
              sideOffset={4}
              className="w-36"
              onClick={(e) => e.stopPropagation()}
            >
              {task.status === 'active' ? (
                <DropdownMenuItem onClick={() => void handlePause()}>
                  <Pause className="size-3.5" />
                  Pause
                </DropdownMenuItem>
              ) : (
                <DropdownMenuItem onClick={() => void handleResume()}>
                  <Play className="size-3.5" />
                  Resume
                </DropdownMenuItem>
              )}
              <DropdownMenuItem onClick={() => onEdit(task)}>
                <Pencil className="size-3.5" />
                Edit
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onClick={() => void handleDelete()}>
                <Trash2 className="size-3.5" />
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>

      <p className="mt-2 text-xs text-muted-foreground/80 line-clamp-2 leading-relaxed">
        {task.prompt}
      </p>
    </div>
  )
}
