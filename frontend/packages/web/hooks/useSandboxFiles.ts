'use client'

import useSWR from 'swr'

export interface SandboxFileEntry {
  path: string
  name: string
  is_dir: boolean
  size: number
  modified_at: string
}

async function fetcher(url: string): Promise<SandboxFileEntry[]> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) {
    throw new Error(`sandbox files fetch failed: ${res.status}`)
  }
  return res.json() as Promise<SandboxFileEntry[]>
}

export function useSandboxFiles(workspaceId: string | null, path: string) {
  const key = workspaceId
    ? `/api/v1/ws/${workspaceId}/sandbox/files` + `?path=${encodeURIComponent(path)}`
    : null
  const { data, error, isLoading, mutate } = useSWR<SandboxFileEntry[]>(key, fetcher, {
    revalidateOnFocus: false,
  })
  return {
    files: data ?? [],
    error,
    loading: isLoading,
    refresh: mutate,
  }
}
