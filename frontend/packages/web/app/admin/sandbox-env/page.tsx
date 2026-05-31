// frontend/packages/web/app/admin/sandbox-env/page.tsx
'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  createApiClient,
  createAdminEnv,
  deleteAdminEnv,
  listAdminEnv,
  rotateAdminEnv,
  type CreateEnvIn,
  type EnvEntryOut,
} from '@cubebox/core'
import { EnvTable } from '@/components/sandbox-env/EnvTable'
import { EnvModal, type ModalMode } from '@/components/sandbox-env/EnvModal'

export default function AdminSandboxEnvPage() {
  const client = useMemo(() => createApiClient(''), [])
  const [entries, setEntries] = useState<EnvEntryOut[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [modal, setModal] = useState<ModalMode | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const data = await listAdminEnv(client)
      setEntries(data.entries.slice().sort((a, b) => a.env_name.localeCompare(b.env_name)))
    } catch (err: unknown) {
      setLoadError(err instanceof Error ? err.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [client])

  useEffect(() => {
    load()
  }, [load])

  async function handleSubmit(
    body: CreateEnvIn | { secret_value: string },
    entryId?: string,
    _scope?: 'workspace' | 'user',
  ) {
    if (entryId) {
      await rotateAdminEnv(client, entryId, body as { secret_value: string })
    } else {
      await createAdminEnv(client, body as CreateEnvIn)
    }
    await load()
  }

  async function handleDelete(entry: EnvEntryOut) {
    if (!confirm(`Delete ${entry.env_name}?`)) return
    try {
      await deleteAdminEnv(client, entry.id)
      await load()
    } catch (err: unknown) {
      setLoadError(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border/70 px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold tracking-tight">Sandbox environment variables</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Org-wide secrets and plain values injected into every workspace sandbox.
            </p>
          </div>
          <button
            onClick={() => setModal({ kind: 'add-org' })}
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border/70 bg-background px-3 text-xs font-medium shadow-sm transition-colors hover:bg-accent"
          >
            + Add secret
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-3xl">
          <EnvTable
            mode="org"
            entries={entries}
            loading={loading}
            error={loadError}
            onRotate={(entry) => setModal({ kind: 'rotate', entry })}
            onDelete={handleDelete}
          />
        </div>
      </div>

      {modal && <EnvModal mode={modal} onSubmit={handleSubmit} onClose={() => setModal(null)} />}
    </div>
  )
}
