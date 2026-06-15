'use client'

import { use, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Plus } from 'lucide-react'
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
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-center justify-between border-b border-border/70 px-6 py-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">{t('pageTitle')}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">{t('pageSubtitle')}</p>
        </div>
        <Button size="sm" className="gap-1.5" onClick={openCreate} data-testid="new-task-button">
          <Plus className="size-3.5" />
          {t('newTask')}
        </Button>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-4xl">
          <ScheduledTasksList
            wsId={wsId}
            onEdit={openEdit}
            onCreate={openCreate}
            refreshKey={refreshKey}
          />
        </div>
      </div>

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
