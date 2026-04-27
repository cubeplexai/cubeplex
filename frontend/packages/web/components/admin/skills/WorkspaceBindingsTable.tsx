'use client'

import { useEffect, useMemo, useState } from 'react'
import useSWR from 'swr'
import { createApiClient, listWorkspaces, type Workspace } from '@cubebox/core'
import { csrfHeaders, jsonHeaders, readApiError } from '@/lib/csrf'

interface WorkspaceBindingsTableProps {
  skillId: string
  installed: boolean
}

interface BindingState {
  enabled: boolean
  pending: boolean
  error: string | null
}

const workspacesFetcher = async (): Promise<Workspace[]> => {
  const client = createApiClient('')
  return listWorkspaces(client)
}

const wsSkillsFetcher = async (url: string): Promise<{ id: string }[]> => {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`workspace skills fetch failed: ${res.status}`)
  return res.json() as Promise<{ id: string }[]>
}

export function WorkspaceBindingsTable({ skillId, installed }: WorkspaceBindingsTableProps) {
  const {
    data: workspaces,
    isLoading: wsLoading,
    error: wsError,
  } = useSWR<Workspace[]>(installed ? '__workspaces__' : null, workspacesFetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })

  const [state, setState] = useState<Record<string, BindingState>>({})

  // Fetch initial enabled state per workspace.
  useEffect(() => {
    if (!installed || !workspaces || workspaces.length === 0) return
    let cancelled = false
    ;(async () => {
      const next: Record<string, BindingState> = {}
      await Promise.all(
        workspaces.map(async (ws) => {
          try {
            const list = await wsSkillsFetcher(`/api/v1/admin/workspaces/${ws.id}/skills`)
            next[ws.id] = {
              enabled: list.some((s) => s.id === skillId),
              pending: false,
              error: null,
            }
          } catch (err) {
            next[ws.id] = { enabled: false, pending: false, error: (err as Error).message }
          }
        }),
      )
      if (!cancelled) setState(next)
    })()
    return () => {
      cancelled = true
    }
  }, [installed, workspaces, skillId])

  async function toggle(wsId: string, nextEnabled: boolean): Promise<void> {
    setState((s) => ({
      ...s,
      [wsId]: { ...(s[wsId] ?? { enabled: false }), pending: true, error: null },
    }))
    try {
      if (nextEnabled) {
        const res = await fetch(`/api/v1/admin/workspaces/${wsId}/skills`, {
          method: 'POST',
          credentials: 'include',
          headers: jsonHeaders(),
          body: JSON.stringify({ skill_ids: [skillId] }),
        })
        if (!res.ok) throw new Error(await readApiError(res))
      } else {
        const res = await fetch(`/api/v1/admin/workspaces/${wsId}/skills/${skillId}`, {
          method: 'DELETE',
          credentials: 'include',
          headers: csrfHeaders(),
        })
        if (!res.ok && res.status !== 204) throw new Error(await readApiError(res))
      }
      setState((s) => ({
        ...s,
        [wsId]: { enabled: nextEnabled, pending: false, error: null },
      }))
    } catch (err) {
      setState((s) => ({
        ...s,
        [wsId]: {
          enabled: s[wsId]?.enabled ?? false,
          pending: false,
          error: (err as Error).message,
        },
      }))
    }
  }

  const sortedWorkspaces = useMemo(
    () => (workspaces ?? []).slice().sort((a, b) => a.name.localeCompare(b.name)),
    [workspaces],
  )

  if (!installed) {
    return (
      <div className="rounded-md border border-dashed border-border/70 bg-muted/20 px-3 py-4 text-xs text-muted-foreground">
        先在组织安装该 skill，才能在 workspace 启用。
      </div>
    )
  }

  if (wsLoading) {
    return <div className="text-xs text-muted-foreground">加载 workspace 列表…</div>
  }
  if (wsError) {
    return (
      <div className="text-xs text-destructive">无法加载 workspace 列表：{wsError.message}</div>
    )
  }
  if (sortedWorkspaces.length === 0) {
    return <div className="text-xs text-muted-foreground">尚无 workspace。</div>
  }

  return (
    <ul className="flex flex-col divide-y divide-border/70 rounded-md border border-border/70">
      {sortedWorkspaces.map((ws) => {
        const cur = state[ws.id]
        const enabled = cur?.enabled ?? false
        const pending = cur?.pending ?? false
        const err = cur?.error ?? null
        return (
          <li
            key={ws.id}
            className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
            data-testid={`ws-binding-row-${ws.name}`}
          >
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium">{ws.name}</div>
              {err && <div className="mt-0.5 text-[11px] text-destructive">{err}</div>}
            </div>
            <label className="inline-flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={enabled}
                disabled={pending}
                onChange={(e) => void toggle(ws.id, e.target.checked)}
                className="size-4 rounded border-border accent-primary"
                aria-label={`enable ${ws.name}`}
                data-testid={`ws-binding-checkbox-${ws.name}`}
              />
              {pending ? '保存中…' : enabled ? '已启用' : '未启用'}
            </label>
          </li>
        )
      })}
    </ul>
  )
}
