'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Plus } from 'lucide-react'
import { createApiClient, useTriggerStore, type CreateTriggerBody } from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { PANE_CONTENT_WIDTH, SectionHeader } from '@/components/shared/SectionHeader'
import { ListDetailLayout } from '@/components/shared/ListDetailLayout'
import { TriggersList } from './TriggersList'
import { TriggerDetailPanel } from './TriggerDetailPanel'
import { TriggerForm } from './TriggerForm'

interface TriggersPanelProps {
  wsId: string
}

export function TriggersPanel({ wsId }: TriggersPanelProps) {
  const t = useTranslations('triggers')
  const client = useMemo(() => createApiClient(''), [])

  const { triggers, loading, load, create, update, remove } = useTriggerStore()

  const [createOpen, setCreateOpen] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [selectedTriggerId, setSelectedTriggerId] = useState<string | null>(null)

  useEffect(() => {
    void load(client, wsId)
  }, [client, wsId, load])

  const handleCreate = useCallback(
    async (body: CreateTriggerBody) => {
      return create(client, wsId, body)
    },
    [client, wsId, create],
  )

  const handleCreated = useCallback((triggerId: string) => {
    setSelectedTriggerId(triggerId)
  }, [])

  const handleToggleEnabled = useCallback(
    async (id: string, enabled: boolean) => {
      await update(client, wsId, id, { enabled })
    },
    [client, wsId, update],
  )

  const handleDeleteConfirm = useCallback(
    async (id: string) => {
      await remove(client, wsId, id)
      if (selectedTriggerId === id) setSelectedTriggerId(null)
      setDeletingId(null)
    },
    [client, wsId, remove, selectedTriggerId],
  )

  const sorted = [...triggers].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )

  return (
    <div className="flex h-full flex-col">
      <SectionHeader
        title={t('title')}
        description={t('subtitle')}
        action={
          <Button
            size="sm"
            className="gap-1.5"
            onClick={() => setCreateOpen(true)}
            data-testid="create-trigger-btn"
          >
            <Plus className="size-3.5" />
            {t('createTrigger')}
          </Button>
        }
      />

      {sorted.length === 0 ? (
        <div className="flex-1 overflow-y-auto px-6 py-6">
          <div className={PANE_CONTENT_WIDTH}>
            <TriggersList
              wsId={wsId}
              triggers={sorted}
              loading={loading}
              onToggleEnabled={handleToggleEnabled}
              onDelete={setDeletingId}
              onCreate={() => setCreateOpen(true)}
            />
          </div>
        </div>
      ) : (
        <ListDetailLayout
          selected={selectedTriggerId !== null}
          list={
            <TriggersList
              wsId={wsId}
              triggers={sorted}
              // Rail only renders when triggers exist; never collapse it to a
              // loading state during the detail panel's background refetch.
              loading={false}
              onToggleEnabled={handleToggleEnabled}
              onDelete={setDeletingId}
              onCreate={() => setCreateOpen(true)}
              selectedId={selectedTriggerId}
              onSelect={setSelectedTriggerId}
            />
          }
          detail={
            selectedTriggerId ? (
              <TriggerDetailPanel
                wsId={wsId}
                triggerId={selectedTriggerId}
                onClose={() => setSelectedTriggerId(null)}
              />
            ) : null
          }
          placeholder={t('selectHint')}
        />
      )}

      {deletingId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
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
  )
}
