'use client'

import { useCallback, useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  createApiClient,
  listWorkspaces,
  wsListEffectiveConnectors,
  wsPatchConnectorState,
  type ApiClient,
  type MCPEffectiveConnector,
  type Workspace,
} from '@cubebox/core'

interface MCPWorkspacesTabProps {
  connectorId: string
  // Retained for prop-compatibility from callers that still pass an admin
  // client; the per-workspace four-layer state endpoints are workspace-scoped
  // so this is effectively informational here.
  client: ApiClient
}

interface WsRow {
  ws: Workspace
  enabled: boolean
  saving: boolean
  error: string | null
}

export function MCPWorkspacesTab({ connectorId, client: _client }: MCPWorkspacesTabProps) {
  const t = useTranslations('mcpAdmin')

  const [rows, setRows] = useState<WsRow[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const orgClient = createApiClient('')
      const workspaces = await listWorkspaces(orgClient)
      // For each workspace, fetch its effective connector state for this install
      const results = await Promise.all(
        workspaces.map(async (ws) => {
          try {
            const wsClient = createApiClient('')
            wsClient.setWorkspaceId(ws.id)
            const list = await wsListEffectiveConnectors(wsClient, ws.id)
            const match = list.items.find(
              (c: MCPEffectiveConnector) => c.install.connector_id === connectorId,
            )
            return {
              ws,
              enabled: match?.workspace_state?.enabled ?? false,
              saving: false,
              error: null,
            } satisfies WsRow
          } catch (err) {
            return {
              ws,
              enabled: false,
              saving: false,
              error: (err as Error).message,
            } satisfies WsRow
          }
        }),
      )
      results.sort((a, b) => a.ws.name.localeCompare(b.ws.name))
      setRows(results)
    } catch (err) {
      setLoadError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }, [connectorId])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])

  function patchRow(wsId: string, patch: Partial<WsRow>): void {
    setRows((prev) => prev.map((r) => (r.ws.id === wsId ? { ...r, ...patch } : r)))
  }

  async function setEnabled(wsId: string, enabled: boolean): Promise<void> {
    patchRow(wsId, { saving: true, error: null })
    try {
      const wsClient = createApiClient('')
      wsClient.setWorkspaceId(wsId)
      const updated = await wsPatchConnectorState(wsClient, wsId, connectorId, { enabled })
      patchRow(wsId, { enabled: updated.enabled, saving: false, error: null })
    } catch (err) {
      patchRow(wsId, { saving: false, error: (err as Error).message })
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

  if (rows.length === 0) {
    return <div className="text-xs text-muted-foreground">{t('wsEmpty')}</div>
  }

  const enabledCount = rows.filter((r) => r.enabled).length

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-muted-foreground">
        {t('workspaceStateSummary', { enabled: enabledCount, total: rows.length })}
      </p>

      <ul className="flex flex-col divide-y divide-border/70 rounded-md border border-border/70">
        {rows.map(({ ws, enabled, saving, error }) => (
          <li
            key={ws.id}
            className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
            data-testid={`ws-state-row-${ws.name}`}
          >
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium">{ws.name}</div>
              {error && <div className="mt-0.5 text-[11px] text-destructive">{error}</div>}
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              {saving ? (
                <span className="text-xs text-muted-foreground">{t('wsSaving')}</span>
              ) : (
                <label className="inline-flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={enabled}
                    disabled={saving}
                    onChange={(e) => void setEnabled(ws.id, e.target.checked)}
                    className="size-4 cursor-pointer rounded border-border accent-primary"
                    aria-label={`enable ${ws.name}`}
                    data-testid={`ws-state-checkbox-${ws.name}`}
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
