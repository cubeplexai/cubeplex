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
} from '@cubeplex/core'
import { useTranslations } from 'next-intl'
import { Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { SETTINGS_CONTENT_WIDTH, SectionHeader } from '@/components/shared/SectionHeader'
import { EnvTable } from '@/components/sandbox-env/EnvTable'
import { EnvModal, type ModalMode } from '@/components/sandbox-env/EnvModal'

interface SandboxEnvPanelProps {
  wsId: string
}

export function SandboxEnvPanel({ wsId }: SandboxEnvPanelProps): React.ReactElement {
  const t = useTranslations('wsSettings.sandboxEnv')
  const client = useMemo(() => createApiClient(''), [])
  const workspace = useWorkspaceStore((s) => s.workspaces.find((w) => w.id === wsId))
  const wsListError = useWorkspaceStore((s) => s.error)
  const wsRole = workspace?.role
  const sandboxMode = workspace?.sandbox_mode ?? 'per_user'
  const isAdmin = wsRole === 'admin'
  const isSharedSandbox = sandboxMode === 'workspace'
  const isWorkspaceLoaded = workspace !== undefined

  const [entries, setEntries] = useState<EnvEntryOut[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [modal, setModal] = useState<ModalMode | null>(null)

  const load = async () => {
    try {
      const fetches = isSharedSandbox
        ? [await listWsEnvWorkspace(client, wsId)]
        : isAdmin
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
    if (!isWorkspaceLoaded) return
    let cancelled = false
    const fetcher = async () => {
      setLoading(true)
      try {
        const fetches = isSharedSandbox
          ? [await listWsEnvWorkspace(client, wsId)]
          : isAdmin
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
  }, [client, wsId, isWorkspaceLoaded, isAdmin, isSharedSandbox])

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

  const tableMode = isSharedSandbox
    ? isAdmin
      ? 'workspace-admin'
      : 'workspace-readonly'
    : isAdmin
      ? 'workspace-admin'
      : 'workspace-member'

  const headerDescription = isSharedSandbox
    ? isAdmin
      ? t('sharedWorkspaceAdminDescription')
      : t('sharedWorkspaceMemberDescription')
    : t('description')

  const showActionButtons = isWorkspaceLoaded && (isAdmin || !isSharedSandbox)

  const effectiveLoading = isWorkspaceLoaded ? loading : !wsListError
  const effectiveError = isWorkspaceLoaded ? loadError : wsListError

  return (
    <div className="flex h-full flex-col">
      <SectionHeader
        title={t('title')}
        description={headerDescription}
        contained={SETTINGS_CONTENT_WIDTH}
        action={
          showActionButtons ? (
            <div className="flex gap-2">
              {isAdmin && (
                <Button
                  size="sm"
                  variant="outline"
                  className="gap-1.5"
                  onClick={() =>
                    setModal({
                      kind: 'add-workspace',
                      defaultScope: 'workspace',
                      allowPersonalScope: !isSharedSandbox,
                    })
                  }
                >
                  <Plus className="size-3.5" />
                  {t('addWorkspaceVar')}
                </Button>
              )}
              {!isSharedSandbox && (
                <Button
                  size="sm"
                  variant="outline"
                  className="gap-1.5"
                  onClick={() =>
                    setModal({
                      kind: 'add-workspace',
                      defaultScope: 'user',
                      allowPersonalScope: true,
                    })
                  }
                >
                  <Plus className="size-3.5" />
                  {t('addPersonalVar')}
                </Button>
              )}
            </div>
          ) : undefined
        }
      />
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className={SETTINGS_CONTENT_WIDTH}>
          <EnvTable
            mode={tableMode}
            entries={entries}
            loading={effectiveLoading}
            error={effectiveError}
            onEdit={(entry) => setModal({ kind: 'edit', entry })}
            onDelete={handleDelete}
          />
        </div>
      </div>
      {modal && <EnvModal mode={modal} onSubmit={handleSubmit} onClose={() => setModal(null)} />}
    </div>
  )
}
