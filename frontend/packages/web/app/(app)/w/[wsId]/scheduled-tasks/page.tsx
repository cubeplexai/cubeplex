'use client'

import { use, useState } from 'react'
import { useTranslations } from 'next-intl'
import { CalendarClock, Plus } from 'lucide-react'
import type { ScheduledTaskOut } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { ScheduledTasksList } from './components/ScheduledTasksList'
import { ScheduledTaskFormDialog } from './components/ScheduledTaskFormDialog'

interface ScheduledTasksPageProps {
  params: Promise<{ wsId: string }>
}

export default function ScheduledTasksPage({
  params,
}: ScheduledTasksPageProps): React.ReactElement {
  const { wsId } = use(params)
  const t = useTranslations('scheduledTasks')
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingTask, setEditingTask] = useState<ScheduledTaskOut | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

  function openCreate(): void {
    setEditingTask(null)
    setDialogOpen(true)
  }

  function openEdit(task: ScheduledTaskOut): void {
    setEditingTask(task)
    setDialogOpen(true)
  }

  function handleSuccess(): void {
    setRefreshKey((k) => k + 1)
  }

  return (
    <div className="flex flex-col gap-6 px-6 py-6 max-w-5xl">
      {/* Page header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <CalendarClock className="size-4.5" />
          </div>
          <div>
            <h1 className="text-xl font-semibold leading-tight">{t('pageTitle')}</h1>
            <p className="text-sm text-muted-foreground">{t('pageSubtitle')}</p>
          </div>
        </div>

        <Button
          size="sm"
          className="gap-1.5 shrink-0"
          onClick={openCreate}
          data-testid="new-task-button"
        >
          <Plus className="size-3.5" />
          {t('newTask')}
        </Button>
      </div>

      {/* Task list */}
      <ScheduledTasksList
        wsId={wsId}
        onEdit={openEdit}
        onCreate={openCreate}
        refreshKey={refreshKey}
      />

      {/* Create / edit dialog */}
      <ScheduledTaskFormDialog
        wsId={wsId}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        task={editingTask}
        onSuccess={handleSuccess}
      />
    </div>
  )
}
