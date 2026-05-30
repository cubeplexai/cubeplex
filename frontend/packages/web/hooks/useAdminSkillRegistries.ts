'use client'

import { useState } from 'react'
import useSWR from 'swr'
import { csrfHeaders, jsonHeaders, readApiError } from '@/lib/csrf'

export interface SkillRegistryEntry {
  id: string
  name: string
  kind: string
  base_url: string
  repo: string | null
  trust_tier: string
  enabled: boolean
}

export interface CreateRegistryBody {
  name: string
  kind: 'remote' | 'skills-sh'
  base_url?: string
  repo?: string | null
  trust_tier: string
}

export interface PatchRegistryBody {
  enabled?: boolean
  trust_tier?: string
}

async function fetcher(url: string): Promise<SkillRegistryEntry[]> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`skill registries fetch failed: ${res.status}`)
  return res.json() as Promise<SkillRegistryEntry[]>
}

export function useAdminSkillRegistries() {
  const [mutating, setMutating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { data, isLoading, mutate } = useSWR<SkillRegistryEntry[]>(
    '/api/v1/admin/skill-registries',
    fetcher,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  async function create(body: CreateRegistryBody): Promise<SkillRegistryEntry | null> {
    setMutating(true)
    setError(null)
    try {
      const res = await fetch('/api/v1/admin/skill-registries', {
        method: 'POST',
        credentials: 'include',
        headers: jsonHeaders(),
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        setError(await readApiError(res))
        return null
      }
      const created = (await res.json()) as SkillRegistryEntry
      await mutate()
      return created
    } finally {
      setMutating(false)
    }
  }

  async function patch(id: string, body: PatchRegistryBody): Promise<boolean> {
    setMutating(true)
    setError(null)
    try {
      const res = await fetch(`/api/v1/admin/skill-registries/${id}`, {
        method: 'PATCH',
        credentials: 'include',
        headers: jsonHeaders(),
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        setError(await readApiError(res))
        return false
      }
      await mutate()
      return true
    } finally {
      setMutating(false)
    }
  }

  async function remove(id: string): Promise<boolean> {
    setMutating(true)
    setError(null)
    try {
      const res = await fetch(`/api/v1/admin/skill-registries/${id}`, {
        method: 'DELETE',
        credentials: 'include',
        headers: csrfHeaders(),
      })
      if (!res.ok) {
        setError(await readApiError(res))
        return false
      }
      await mutate()
      return true
    } finally {
      setMutating(false)
    }
  }

  return {
    registries: data ?? [],
    loading: isLoading,
    mutating,
    error,
    create,
    patch,
    remove,
    refresh: mutate,
  }
}
