'use client'

import { useCallback, useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Check, X } from 'lucide-react'
import {
  adminGetOverrides,
  adminPutOverride,
  createApiClient,
  listWorkspaces,
  type ApiClient,
  type Workspace,
  type WorkspaceOverride,
} from '@cubebox/core'

interface MCPWorkspacesTabProps {
  serverId: string
  client: ApiClient
}

interface WsEntry {
  ws: Workspace
  enabled: boolean
  saving: boolean
  error: string | null
  confirmDisable: boolean
}

export function MCPWorkspacesTab({ serverId, client }: MCPWorkspacesTabProps) {
  const t = useTranslations('mcpAdmin')

  const [entries, setEntries] = useState<WsEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const wsClient = createApiClient('')
      const [workspaces, overrides] = await Promise.all([
        listWorkspaces(wsClient),
        adminGetOverrides(client, serverId),
      ])
      const overrideMap = new Map<string, boolean>(
        overrides.map((o: WorkspaceOverride) => [o.workspace_id, o.enabled]),
      )
      setEntries(
        workspaces
          .slice()
          .sort((a, b) => a.name.localeCompare(b.name))
          .map((ws) => ({
            ws,
            enabled: overrideMap.get(ws.id) ?? true,
            saving: false,
            error: null,
            confirmDisable: false,
          })),
      )
    } catch (err) {
      setLoadError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }, [client, serverId])

  useEffect(() => {
    void load()
  }, [load])

  function patchEntry(wsId: string, patch: Partial<WsEntry>): void {
    setEntries((prev) => prev.map((e) => (e.ws.id === wsId ? { ...e, ...patch } : e)))
  }

  async function setEnabled(wsId: string, enabled: boolean): Promise<void> {
    patchEntry(wsId, { saving: true, error: null, confirmDisable: false })
    try {
      const updated = await adminPutOverride(client, serverId, {
        workspace_id: wsId,
        enabled,
      })
      const overrideMap = new Map<string, boolean>(updated.map((o) => [o.workspace_id, o.enabled]))
      setEntries((prev) =>
        prev.map((e) => ({
          ...e,
          enabled: overrideMap.get(e.ws.id) ?? true,
          saving: e.ws.id === wsId ? false : e.saving,
        })),
      )
    } catch (err) {
      patchEntry(wsId, { saving: false, error: (err as Error).message })
    }
  }

  if (loading) {
    return <div className="text-xs text-muted-foreground">{t('wsLoading')}</div>
  }

  if (loadError) {
    return (
      <div className="text-xs text-destructive">{t('wsLoadError', { message: loadError })}</div>
    )
  }

  if (entries.length === 0) {
    return <div className="text-xs text-muted-foreground">{t('wsEmpty')}</div>
  }

  const enabledCount = entries.filter((e) => e.enabled).length

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-muted-foreground">
        {t('wsSummary', { enabled: enabledCount, total: entries.length })}
      </p>

      <ul className="flex flex-col divide-y divide-border/70 rounded-md border border-border/70">
        {entries.map(({ ws, enabled, saving, error, confirmDisable }) => (
          <li
            key={ws.id}
            className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
            data-testid={`ws-override-row-${ws.name}`}
          >
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium">{ws.name}</div>
              {error && <div className="mt-0.5 text-[11px] text-destructive">{error}</div>}
            </div>

            <div className="flex shrink-0 items-center gap-1.5">
              {saving ? (
                <span className="text-xs text-muted-foreground">{t('wsSaving')}</span>
              ) : confirmDisable ? (
                <>
                  <span className="text-xs text-destructive">{t('wsConfirmDisable')}</span>
                  <button
                    type="button"
                    className={
                      'cursor-pointer rounded p-0.5 text-destructive ' + 'hover:bg-destructive/10'
                    }
                    onClick={() => void setEnabled(ws.id, false)}
                    aria-label="confirm disable"
                  >
                    <Check className="size-3.5" />
                  </button>
                  <button
                    type="button"
                    className={
                      'cursor-pointer rounded p-0.5 text-muted-foreground ' + 'hover:bg-muted'
                    }
                    onClick={() => patchEntry(ws.id, { confirmDisable: false })}
                    aria-label="cancel"
                  >
                    <X className="size-3.5" />
                  </button>
                </>
              ) : (
                <label
                  className="inline-flex cursor-pointer items-center gap-2 text-xs
                    text-muted-foreground"
                >
                  <input
                    type="checkbox"
                    checked={enabled}
                    disabled={saving}
                    onChange={(e) => {
                      if (e.target.checked) {
                        void setEnabled(ws.id, true)
                      } else {
                        patchEntry(ws.id, { confirmDisable: true })
                      }
                    }}
                    className="size-4 cursor-pointer rounded border-border accent-primary"
                    aria-label={`enable ${ws.name}`}
                    data-testid={`ws-override-checkbox-${ws.name}`}
                  />
                  {enabled ? t('wsEnabled') : t('wsDisabled')}
                </label>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
