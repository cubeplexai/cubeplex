'use client'

import useSWR from 'swr'

export function backgroundTaskOutputUrl(
  workspaceId: string,
  conversationId: string,
  taskId: string,
): string {
  return (
    `/api/v1/ws/${encodeURIComponent(workspaceId)}/conversations/` +
    `${encodeURIComponent(conversationId)}/background-tasks/${encodeURIComponent(taskId)}/output`
  )
}

async function fetchOutput(url: string): Promise<{ content: string }> {
  const response = await fetch(url, { credentials: 'include' })
  if (response.status === 413) throw new Error('FILE_TOO_LARGE')
  if (response.status === 410) {
    const body = (await response.json()) as { detail?: unknown }
    const error = new Error(typeof body.detail === 'string' ? body.detail : 'Output unavailable')
    error.name = 'OUTPUT_UNAVAILABLE'
    throw error
  }
  if (!response.ok) throw new Error(`Task output fetch failed: ${response.status}`)
  return response.json() as Promise<{ content: string }>
}

export function useBackgroundTaskOutput(
  workspaceId: string | null,
  conversationId: string,
  taskId: string | null,
  refreshInterval: number,
) {
  const key =
    workspaceId && taskId ? backgroundTaskOutputUrl(workspaceId, conversationId, taskId) : null
  const { data, error, isLoading, mutate } = useSWR(key, fetchOutput, {
    revalidateOnFocus: false,
    revalidateOnMount: true,
    shouldRetryOnError: false,
    refreshInterval,
  })
  return { content: data?.content ?? null, error, loading: isLoading, refresh: mutate }
}
