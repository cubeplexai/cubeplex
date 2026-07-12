'use client'

import useSWR from 'swr'
import type { SkillSummary } from '@cubeplex/core'

async function fetcher(url: string): Promise<SkillSummary[]> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`workspace skills fetch failed: ${res.status}`)
  return res.json() as Promise<SkillSummary[]>
}

export function useWorkspaceSkills(workspaceId: string | null) {
  const key = workspaceId ? `/api/v1/admin/workspaces/${workspaceId}/skills` : null
  const { data, error, isLoading, mutate } = useSWR<SkillSummary[]>(key, fetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })
  return {
    skills: data ?? [],
    loading: isLoading,
    error: error as Error | undefined,
    refresh: mutate,
  }
}
