'use client'

import useSWR from 'swr'
import type { SkillFilters, SkillSummary } from '@cubeplex/core'

async function fetcher(url: string): Promise<SkillSummary[]> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`admin skills fetch failed: ${res.status}`)
  return res.json() as Promise<SkillSummary[]>
}

function buildKey(filters?: SkillFilters): string {
  const params = new URLSearchParams()
  if (filters?.source) params.set('source', filters.source)
  if (filters?.installed !== undefined) params.set('installed', String(filters.installed))
  if (filters?.q) params.set('q', filters.q)
  if (filters?.tag) params.set('tag', filters.tag)
  const qs = params.toString()
  return qs ? `/api/v1/admin/skills?${qs}` : '/api/v1/admin/skills'
}

export function useAdminSkills(filters?: SkillFilters) {
  const key = buildKey(filters)
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
