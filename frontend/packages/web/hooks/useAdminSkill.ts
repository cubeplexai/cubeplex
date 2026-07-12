'use client'

import useSWR from 'swr'
import type { SkillDetail } from '@cubeplex/core'

async function fetcher(url: string): Promise<SkillDetail> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`admin skill detail fetch failed: ${res.status}`)
  return res.json() as Promise<SkillDetail>
}

export function useAdminSkill(skillId: string | null) {
  const key = skillId ? `/api/v1/admin/skills/${skillId}` : null
  const { data, error, isLoading, mutate } = useSWR<SkillDetail>(key, fetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })
  return {
    skill: data,
    loading: isLoading,
    error: error as Error | undefined,
    refresh: mutate,
  }
}
