'use client'

import useSWR from 'swr'

interface SandboxFileContent {
  content: string
  mime_type: string
}

async function fetcher(url: string): Promise<SandboxFileContent> {
  const res = await fetch(url, { credentials: 'include' })
  if (res.status === 413) throw new Error('FILE_TOO_LARGE')
  if (!res.ok) {
    throw new Error(`file content fetch failed: ${res.status}`)
  }
  return res.json() as Promise<SandboxFileContent>
}

export function useSandboxFileContent(workspaceId: string | null, path: string | null) {
  const key =
    workspaceId && path
      ? `/api/v1/ws/${workspaceId}/sandbox/files/content` + `?path=${encodeURIComponent(path)}`
      : null
  const { data, error, isLoading } = useSWR<SandboxFileContent>(key, fetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })
  return {
    content: data?.content ?? null,
    mimeType: data?.mime_type ?? null,
    error,
    loading: isLoading,
  }
}
