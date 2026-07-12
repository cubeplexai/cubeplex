'use client'

import { Pause, Pencil, Play, Trash2 } from 'lucide-react'
import { useTranslations } from 'next-intl'
import type { ScheduledTaskOut } from '@cubeplex/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { DetailPanel } from '@/components/shared/DetailPanel'
import { ScheduledTaskRunsPanel } from './ScheduledTaskRunsPanel'
import { formatNextFire, formatScheduleSummary } from '../lib/format'
import { DestinationCell } from './DestinationCell'

interface ScheduledTaskDetailProps {
  wsId: string
  task: ScheduledTaskOut
  canMutate: boolean
  pending?: boolean
  backLabel?: string
  onPause: () => void
  onResume: () => void
  onDelete: () => void
  onEdit: (task: ScheduledTaskOut) => void
  onBack: () => void
}

export function ScheduledTaskDetail({
  wsId,
  task,
  canMutate,
  pending,
  backLabel,
  onPause,
  onResume,
  onDelete,
  onEdit,
  onBack,
}: ScheduledTaskDetailProps): React.ReactElement {
  const t = useTranslations('scheduledTasks')
  return (
    <DetailPanel
      onBack={onBack}
      backLabel={backLabel}
      title={task.name}
      badge={
        <Badge
          variant={task.status === 'active' ? 'default' : 'secondary'}
          className={task.status === 'active' ? 'bg-success-surface text-success-fg' : undefined}
        >
          {task.status === 'active' ? 'Active' : 'Paused'}
        </Badge>
      }
      actions={
        canMutate ? (
          <>
            {task.status === 'active' ? (
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5"
                disabled={pending}
                onClick={onPause}
              >
                <Pause className="size-3.5" />
                Pause
              </Button>
            ) : (
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5"
                disabled={pending}
                onClick={onResume}
              >
                <Play className="size-3.5" />
                Resume
              </Button>
            )}
            <Button variant="outline" size="sm" className="gap-1.5" onClick={() => onEdit(task)}>
              <Pencil className="size-3.5" />
              Edit
            </Button>
            <Button
              variant="destructive"
              size="sm"
              className="gap-1.5"
              disabled={pending}
              onClick={onDelete}
            >
              <Trash2 className="size-3.5" />
              Delete
            </Button>
          </>
        ) : undefined
      }
    >
      <div className="flex flex-col gap-5">
        <div className="rounded-xl border border-border/70 bg-card/40 p-4 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
            <div className="flex flex-col gap-0.5">
              <span className="text-xs text-muted-foreground">{t('detailSchedule')}</span>
              <span className="text-sm font-medium">{formatScheduleSummary(task)}</span>
            </div>
            <div className="flex flex-col gap-0.5">
              <span className="text-xs text-muted-foreground">{t('detailDestination')}</span>
              <span className="text-sm font-medium">
                <DestinationCell wsId={wsId} task={task} />
              </span>
            </div>
            <div className="flex flex-col gap-0.5">
              <span className="text-xs text-muted-foreground">{t('detailNextRun')}</span>
              <span className="text-sm font-medium">{formatNextFire(task)}</span>
            </div>
          </div>
        </div>
        <ScheduledTaskRunsPanel wsId={wsId} taskId={task.id} />
      </div>
    </DetailPanel>
  )
}
