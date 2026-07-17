'use client'

import { useState } from 'react'
import useSWR from 'swr'
import { useTranslations } from 'next-intl'
import { Check, X } from 'lucide-react'
import { createApiClient, listWorkspaces, type Workspace } from '@cubeplex/core'
import type { SkillSummary, WorkspaceBindingState } from '@cubeplex/core'
import { csrfHeaders, jsonHeaders, readApiError } from '@/lib/csrf'

interface WorkspaceBindingsTableProps {
  skillId: string
  installed: boolean
  autoBind: boolean
}

interface WsBindingEntry {
  ws: Workspace
  state: WorkspaceBindingState
  pending: boolean
  error: string | null
  confirmDisable: boolean
}

const workspacesFetcher = async (): Promise<Workspace[]> => {
  const client = createApiClient('')
  return listWorkspaces(client)
}

const wsSkillsFetcher = async (url: string): Promise<SkillSummary[]> => {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`workspace skills fetch failed: ${res.status}`)
  return res.json() as Promise<SkillSummary[]>
}

export function WorkspaceBindingsTable({
  skillId,
  installed,
  autoBind,
}: WorkspaceBindingsTableProps) {
  const t = useTranslations('adminSkills')
  const tExtra = useTranslations('adminSkillsExtra')
  const {
    data: workspaces,
    isLoading: wsLoading,
    error: wsError,
  } = useSWR<Workspace[]>(installed ? '__workspaces__' : null, workspacesFetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })

  const [entries, setEntries] = useState<Record<string, WsBindingEntry>>({})

  const wsIds = workspaces?.map((w) => w.id).join(',') ?? ''

  // Fetch binding state for all workspaces at once per workspace.
  useSWR<void>(
    installed && workspaces && workspaces.length > 0 ? `ws-bindings:${skillId}:${wsIds}` : null,
    async () => {
      if (!workspaces) return
      const next: Record<string, WsBindingEntry> = {}
      await Promise.all(
        workspaces.map(async (ws) => {
          try {
            const list = await wsSkillsFetcher(`/api/v1/admin/workspaces/${ws.id}/skills`)
            const match = list.find((s) => s.id === skillId)
            const state: WorkspaceBindingState = match?.workspace_binding_state ?? 'disabled'
            next[ws.id] = { ws, state, pending: false, error: null, confirmDisable: false }
          } catch (err) {
            next[ws.id] = {
              ws,
              state: 'disabled',
              pending: false,
              error: (err as Error).message,
              confirmDisable: false,
            }
          }
        }),
      )
      setEntries(next)
    },
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  function setEntry(wsId: string, patch: Partial<WsBindingEntry>): void {
    setEntries((prev) => ({
      ...prev,
      [wsId]: { ...prev[wsId], ...patch },
    }))
  }

  async function enableWs(wsId: string): Promise<void> {
    setEntry(wsId, { pending: true, error: null, confirmDisable: false })
    try {
      const res = await fetch(`/api/v1/admin/workspaces/${wsId}/skills`, {
        method: 'POST',
        credentials: 'include',
        headers: jsonHeaders(),
        body: JSON.stringify({ skill_ids: [skillId] }),
      })
      if (!res.ok) throw new Error(await readApiError(res))
      setEntry(wsId, { state: 'enabled', pending: false })
    } catch (err) {
      setEntry(wsId, { pending: false, error: (err as Error).message })
    }
  }

  async function disableWs(wsId: string): Promise<void> {
    setEntry(wsId, { pending: true, error: null, confirmDisable: false })
    try {
      const res = await fetch(`/api/v1/admin/workspaces/${wsId}/skills/${skillId}`, {
        method: 'DELETE',
        credentials: 'include',
        headers: csrfHeaders(),
      })
      if (!res.ok && res.status !== 204) throw new Error(await readApiError(res))
      setEntry(wsId, { state: autoBind ? 'auto' : 'disabled', pending: false })
    } catch (err) {
      setEntry(wsId, { pending: false, error: (err as Error).message })
    }
  }

  if (!installed) {
    return (
      <div className="rounded-md border border-dashed border-border/70 bg-muted/20 px-3 py-4 text-xs text-muted-foreground">
        {t('installFirst')}
      </div>
    )
  }

  if (wsLoading) {
    return <div className="text-xs text-muted-foreground">{t('loadingWorkspaces')}</div>
  }
  if (wsError) {
    return (
      <div className="text-xs text-destructive">
        {t('wsLoadFailed', { message: wsError.message })}
      </div>
    )
  }
  if (!workspaces || workspaces.length === 0) {
    return <div className="text-xs text-muted-foreground">{t('noWorkspaces')}</div>
  }

  const sortedEntries = Object.values(entries).sort((a, b) => a.ws.name.localeCompare(b.ws.name))

  if (sortedEntries.length === 0) {
    return <div className="text-xs text-muted-foreground">{t('loadingBindings')}</div>
  }

  return (
    <ul className="flex flex-col divide-y divide-border/70 rounded-md border border-border/70">
      {sortedEntries.map(({ ws, state, pending, error, confirmDisable }) => {
        const effective = state === 'enabled' || state === 'auto'
        return (
          <li
            key={ws.id}
            className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
            data-testid={`ws-binding-row-${ws.name}`}
          >
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium">{ws.name}</div>
              {state === 'auto' && (
                <div className="text-[11px] text-muted-foreground">{t('autoLinked')}</div>
              )}
              {error && <div className="mt-0.5 text-[11px] text-destructive">{error}</div>}
            </div>

            <div className="flex shrink-0 items-center gap-1.5">
              {pending ? (
                <span className="text-xs text-muted-foreground">{t('saving')}</span>
              ) : confirmDisable ? (
                <>
                  <span className="text-xs text-destructive">{t('confirmDisable')}</span>
                  <button
                    type="button"
                    className="cursor-pointer rounded p-0.5 text-destructive hover:bg-destructive/10"
                    onClick={() => void disableWs(ws.id)}
                    aria-label={tExtra('confirmDisable')}
                  >
                    <Check className="size-3.5" />
                  </button>
                  <button
                    type="button"
                    className="cursor-pointer rounded p-0.5 text-muted-foreground hover:bg-muted"
                    onClick={() => setEntry(ws.id, { confirmDisable: false })}
                    aria-label="cancel"
                  >
                    <X className="size-3.5" />
                  </button>
                </>
              ) : (
                <label className="inline-flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={effective}
                    disabled={pending}
                    onChange={(e) => {
                      if (e.target.checked) {
                        void enableWs(ws.id)
                      } else {
                        setEntry(ws.id, { confirmDisable: true })
                      }
                    }}
                    className="size-4 cursor-pointer rounded border-border accent-primary"
                    aria-label={`enable ${ws.name}`}
                    data-testid={`ws-binding-checkbox-${ws.name}`}
                  />
                  {effective
                    ? state === 'auto'
                      ? t('autoEnabled')
                      : t('manualEnabled')
                    : t('notEnabled')}
                </label>
              )}
            </div>
          </li>
        )
      })}
    </ul>
  )
}
