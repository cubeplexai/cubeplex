'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { Plus } from 'lucide-react'
import { createApiClient, useTriggerStore, type CreateTriggerBody } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { TriggersList } from './TriggersList'
import { TriggerForm } from './TriggerForm'

interface TriggersPanelProps {
  wsId: string
}

export function TriggersPanel({ wsId }: TriggersPanelProps) {
  const t = useTranslations('triggers')
  const router = useRouter()
  const client = useMemo(() => createApiClient(''), [])

  const { triggers, loading, load, create, update, remove } = useTriggerStore()

  const [createOpen, setCreateOpen] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  useEffect(() => {
    void load(client, wsId)
  }, [client, wsId, load])

  const handleCreate = useCallback(
    async (body: CreateTriggerBody) => {
      return create(client, wsId, body)
    },
    [client, wsId, create],
  )

  const handleCreated = useCallback(
    (triggerId: string) => {
      router.push(`/w/${wsId}/triggers/${triggerId}`)
    },
    [router, wsId],
  )

  const handleToggleEnabled = useCallback(
    async (id: string, enabled: boolean) => {
      await update(client, wsId, id, { enabled })
    },
    [client, wsId, update],
  )

  const handleDeleteConfirm = useCallback(
    async (id: string) => {
      await remove(client, wsId, id)
      setDeletingId(null)
    },
    [client, wsId, remove],
  )

  const sorted = [...triggers].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )

  return (
    <div className="flex h-full flex-col overflow-y-auto px-6 py-6">
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-6">
        <header className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold tracking-tight">{t('title')}</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">{t('subtitle')}</p>
          </div>
          <Button
            size="sm"
            className="gap-1.5"
            onClick={() => setCreateOpen(true)}
            data-testid="create-trigger-btn"
          >
            <Plus className="size-3.5" />
            {t('createTrigger')}
          </Button>
        </header>

        <TriggersList
          wsId={wsId}
          triggers={sorted}
          loading={loading}
          onToggleEnabled={handleToggleEnabled}
          onDelete={setDeletingId}
          onCreate={() => setCreateOpen(true)}
        />

        {deletingId && (
          <div
            className={
              'fixed inset-0 z-50 flex items-center justify-center ' +
              'bg-black/40 backdrop-blur-sm'
            }
          >
            <div className="w-[min(400px,calc(100vw-32px))] rounded-xl border border-border bg-popover p-5 shadow-2xl">
              <h3 className="text-base font-semibold">{t('deleteTitle')}</h3>
              <p className="mt-2 text-sm text-muted-foreground">{t('deleteConfirm')}</p>
              <div className="mt-4 flex items-center justify-end gap-2">
                <Button variant="ghost" size="sm" onClick={() => setDeletingId(null)}>
                  {t('cancel')}
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => void handleDeleteConfirm(deletingId)}
                  data-testid="confirm-delete-btn"
                >
                  {t('delete')}
                </Button>
              </div>
            </div>
          </div>
        )}

        <TriggerForm
          wsId={wsId}
          open={createOpen}
          onOpenChange={setCreateOpen}
          onSubmit={handleCreate}
          onCreated={handleCreated}
        />
      </div>
    </div>
  )
}
