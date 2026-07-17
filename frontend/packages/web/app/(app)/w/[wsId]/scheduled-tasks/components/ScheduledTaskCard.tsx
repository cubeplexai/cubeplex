'use client'

import { MoreHorizontal, Pause, Play, Pencil, Trash2 } from 'lucide-react'
import type { ScheduledTaskOut } from '@cubeplex/core'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Badge } from '@/components/ui/badge'
import { RailCard } from '@/components/shared/RailCard'
import { formatNextFire, formatScheduleSummary } from '../lib/format'
import { DestinationCell } from './DestinationCell'

interface ScheduledTaskCardProps {
  wsId: string
  task: ScheduledTaskOut
  isSelected: boolean
  canMutate: boolean
  pending?: boolean
  onSelect: () => void
  onPause: () => void
  onResume: () => void
  onDelete: () => void
  onEdit: (task: ScheduledTaskOut) => void
}

export function ScheduledTaskCard({
  wsId,
  task,
  isSelected,
  canMutate,
  pending,
  onSelect,
  onPause,
  onResume,
  onDelete,
  onEdit,
}: ScheduledTaskCardProps): React.ReactElement {
  return (
    <RailCard
      data-testid={`task-card-${task.id}`}
      selected={isSelected}
      onSelect={onSelect}
      title={task.name}
      badge={
        <Badge
          variant={task.status === 'active' ? 'default' : 'secondary'}
          className={
            task.status === 'active' ? 'text-xs bg-success-surface text-success-fg' : 'text-xs'
          }
          data-testid={`status-badge-${task.id}`}
        >
          {task.status === 'active' ? 'Active' : 'Paused'}
        </Badge>
      }
      secondary={
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span>{formatScheduleSummary(task)}</span>
          <span className="text-muted-foreground/60">·</span>
          <DestinationCell wsId={wsId} task={task} />
        </span>
      }
      meta={formatNextFire(task)}
      actions={
        canMutate ? (
          <DropdownMenu>
            <DropdownMenuTrigger
              aria-label="Task actions"
              disabled={pending}
              className="grid size-6 place-items-center rounded-md p-0 text-muted-foreground opacity-0 transition-opacity hover:bg-accent hover:text-foreground group-hover:opacity-100 data-[popup-open]:opacity-100"
            >
              <MoreHorizontal className="size-3.5" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" side="bottom" sideOffset={4} className="w-36">
              {task.status === 'active' ? (
                <DropdownMenuItem onClick={onPause}>
                  <Pause className="size-3.5" />
                  Pause
                </DropdownMenuItem>
              ) : (
                <DropdownMenuItem onClick={onResume}>
                  <Play className="size-3.5" />
                  Resume
                </DropdownMenuItem>
              )}
              <DropdownMenuItem onClick={() => onEdit(task)}>
                <Pencil className="size-3.5" />
                Edit
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onClick={onDelete}>
                <Trash2 className="size-3.5" />
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : undefined
      }
    />
  )
}
