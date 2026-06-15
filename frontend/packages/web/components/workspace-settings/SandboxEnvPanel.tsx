'use client'

import { useEffect, useMemo, useState } from 'react'
import {
  createApiClient,
  createWsEnvMe,
  createWsEnvWorkspace,
  deleteWsEnvMe,
  deleteWsEnvWorkspace,
  listWsEnvMe,
  listWsEnvWorkspace,
  updateWsEnvMe,
  updateWsEnvWorkspace,
  useWorkspaceStore,
  type CreateEnvIn,
  type EnvEntryOut,
  type UpdateEntryIn,
} from '@cubebox/core'
import { useTranslations } from 'next-intl'
import { Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EnvTable } from '@/components/sandbox-env/EnvTable'
import { EnvModal, type ModalMode } from '@/components/sandbox-env/EnvModal'

interface SandboxEnvPanelProps {
  wsId: string
}

export function SandboxEnvPanel({ wsId }: SandboxEnvPanelProps): React.ReactElement {
  const t = useTranslations('wsSettings.sandboxEnv')
  const client = useMemo(() => createApiClient(''), [])
  const wsRole = useWorkspaceStore((s) => s.workspaces.find((w) => w.id === wsId)?.role)
  const isAdmin = wsRole === 'admin'

  const [entries, setEntries] = useState<EnvEntryOut[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [modal, setModal] = useState<ModalMode | null>(null)

  const load = async () => {
    try {
      const fetches = isAdmin
        ? await Promise.all([listWsEnvWorkspace(client, wsId), listWsEnvMe(client, wsId)])
        : [await listWsEnvMe(client, wsId)]
      const merged = fetches
        .flatMap((r) => r.entries)
        .sort((a, b) => a.env_name.localeCompare(b.env_name))
      setEntries(merged)
      setLoading(false)
    } catch (err: unknown) {
      setLoadError(err instanceof Error ? err.message : 'Failed to load')
      setLoading(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    const fetcher = async () => {
      try {
        const fetches = isAdmin
          ? await Promise.all([listWsEnvWorkspace(client, wsId), listWsEnvMe(client, wsId)])
          : [await listWsEnvMe(client, wsId)]
        if (cancelled) return
        const merged = fetches
          .flatMap((r) => r.entries)
          .sort((a, b) => a.env_name.localeCompare(b.env_name))
        setEntries(merged)
        setLoading(false)
        setLoadError(null)
      } catch (err: unknown) {
        if (cancelled) return
        setLoadError(err instanceof Error ? err.message : 'Failed to load')
        setLoading(false)
      }
    }
    void fetcher()
    return () => {
      cancelled = true
    }
  }, [client, wsId, isAdmin])

  async function handleSubmit(
    body: CreateEnvIn | UpdateEntryIn,
    entryId?: string,
    scope?: 'workspace' | 'user',
  ) {
    if (entryId) {
      const entry = entries.find((e) => e.id === entryId)
      if (entry?.scope === 'workspace') {
        await updateWsEnvWorkspace(client, wsId, entryId, body as UpdateEntryIn)
      } else {
        await updateWsEnvMe(client, wsId, entryId, body as UpdateEntryIn)
      }
    } else {
      const createBody = body as CreateEnvIn
      if (scope === 'workspace') {
        await createWsEnvWorkspace(client, wsId, createBody)
      } else {
        await createWsEnvMe(client, wsId, createBody)
      }
    }
    await load()
  }

  async function handleDelete(entry: EnvEntryOut) {
    if (!confirm(t('deleteConfirm', { name: entry.env_name }))) return
    try {
      if (entry.scope === 'workspace') {
        await deleteWsEnvWorkspace(client, wsId, entry.id)
      } else {
        await deleteWsEnvMe(client, wsId, entry.id)
      }
      await load()
    } catch (err: unknown) {
      setLoadError(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  const tableMode = isAdmin ? 'workspace-admin' : 'workspace-member'

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border/70 px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold tracking-tight">{t('title')}</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">{t('description')}</p>
          </div>
          <div className="flex gap-2">
            {isAdmin && (
              <Button
                size="sm"
                variant="outline"
                className="gap-1.5"
                onClick={() => setModal({ kind: 'add-workspace', defaultScope: 'workspace' })}
              >
                <Plus className="size-3.5" />
                {t('addWorkspaceVar')}
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              className="gap-1.5"
              onClick={() => setModal({ kind: 'add-workspace', defaultScope: 'user' })}
            >
              <Plus className="size-3.5" />
              {t('addPersonalVar')}
            </Button>
          </div>
        </div>
      </header>
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-3xl">
          <EnvTable
            mode={tableMode}
            entries={entries}
            loading={loading}
            error={loadError}
            onEdit={(entry) => setModal({ kind: 'edit', entry })}
            onDelete={handleDelete}
          />
        </div>
      </div>
      {modal && <EnvModal mode={modal} onSubmit={handleSubmit} onClose={() => setModal(null)} />}
    </div>
  )
}
