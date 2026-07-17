'use client'

import { useMemo } from 'react'
import useSWR from 'swr'
import type { SkillSource, SkillSummary, WorkspaceSkills } from '@cubeplex/core'

/**
 * Workspace-aware skill state. Mirrors the four real states a skill can be in
 * for a member viewing the workspace's catalog.
 */
export type WorkspaceSkillState =
  | 'org-enabled' // org-installed, toggled on for this workspace
  | 'org-disabled' // org-installed, toggled off for this workspace
  | 'workspace-private' // workspace-private install (always enabled)
  | 'available' // org-visible but not installed in this workspace

export interface WorkspaceSkillEntry extends SkillSummary {
  workspaceState: WorkspaceSkillState
  installId: string | null
}

export interface WorkspaceSkillFilters {
  source?: SkillSource
  state?: 'all' | 'enabled' | 'disabled' | 'available'
  q?: string
  externalOnly?: boolean
}

async function fetcher<T>(url: string): Promise<T> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`fetch failed: ${res.status}`)
  return res.json() as Promise<T>
}

function buildCatalogKey(wsId: string, filters?: WorkspaceSkillFilters): string {
  const params = new URLSearchParams({ scope: 'catalog' })
  if (filters?.source) params.set('source', filters.source)
  if (filters?.q) params.set('q', filters.q)
  return `/api/v1/ws/${wsId}/skills?${params.toString()}`
}

/**
 * Merges `/ws/{wsId}/skills?scope=catalog` (all org-visible skills) with
 * `/ws/{wsId}/settings/skills` (this workspace's binding/private state) into
 * a single per-skill view. Search and source filter pass through to the
 * server; the state filter is applied client-side after the merge.
 */
export function useWorkspaceSkillsCatalog(wsId: string, filters?: WorkspaceSkillFilters) {
  const catalogKey = buildCatalogKey(wsId, filters)
  const settingsKey = `/api/v1/ws/${wsId}/settings/skills`

  const catalogQuery = useSWR<SkillSummary[]>(catalogKey, fetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })
  const settingsQuery = useSWR<WorkspaceSkills>(settingsKey, fetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })

  const merged = useMemo<WorkspaceSkillEntry[]>(() => {
    const catalog = catalogQuery.data ?? []
    const settings = settingsQuery.data
    if (!settings) {
      return catalog.map((s) => ({ ...s, workspaceState: 'available', installId: null }))
    }
    // Build lookup tables keyed by skill_id.
    const orgBySkill = new Map(settings.org_skills.map((s) => [s.skill_id, s]))
    const wsBySkill = new Map(settings.workspace_skills.map((s) => [s.skill_id, s]))

    return catalog.map((s) => {
      const wsPrivate = wsBySkill.get(s.id)
      if (wsPrivate) {
        return {
          ...s,
          installed_version: wsPrivate.installed_version,
          install_state: 'installed' as const,
          workspaceState: 'workspace-private' as const,
          installId: wsPrivate.install_id,
        }
      }
      const org = orgBySkill.get(s.id)
      if (org) {
        return {
          ...s,
          installed_version: org.installed_version,
          install_state: 'installed' as const,
          workspaceState: org.enabled ? ('org-enabled' as const) : ('org-disabled' as const),
          installId: org.install_id,
        }
      }
      return { ...s, workspaceState: 'available' as const, installId: null }
    })
  }, [catalogQuery.data, settingsQuery.data])

  // Client-side state filter — server only knows source/q.
  const stateFilter = filters?.state
  const filtered = useMemo<WorkspaceSkillEntry[]>(() => {
    if (!stateFilter || stateFilter === 'all') return merged
    return merged.filter((s) => {
      if (stateFilter === 'enabled') {
        return s.workspaceState === 'org-enabled' || s.workspaceState === 'workspace-private'
      }
      if (stateFilter === 'disabled') return s.workspaceState === 'org-disabled'
      if (stateFilter === 'available') return s.workspaceState === 'available'
      return true
    })
  }, [merged, stateFilter])

  return {
    skills: filtered,
    loading: catalogQuery.isLoading || settingsQuery.isLoading,
    error: (catalogQuery.error ?? settingsQuery.error) as Error | undefined,
    refresh: async () => {
      await Promise.all([catalogQuery.mutate(), settingsQuery.mutate()])
    },
  }
}
