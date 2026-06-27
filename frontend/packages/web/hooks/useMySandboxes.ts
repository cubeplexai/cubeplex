'use client'

import useSWR from 'swr'
import { createApiClient, type MySandboxOut } from '@cubebox/core'

/**
 * List the calling user's own sandbox entities in a workspace.
 * Returns live rows regardless of runtime status — a terminated sandbox
 * (container off, row alive) still shows up so the user can restart or
 * delete it. Pass `null` to suspend fetching (e.g. before the wsId is known).
 */
export function useMySandboxes(wsId: string | null) {
  const key = wsId ? ['my-sandboxes', wsId] : null
  const { data, error, isLoading, mutate } = useSWR<MySandboxOut[]>(
    key,
    async () => {
      const client = createApiClient('')
      const res = await client.get(`/api/v1/ws/${wsId}/sandboxes`)
      if (!res.ok) {
        throw new Error(`my-sandboxes fetch failed: ${res.status}`)
      }
      return (await res.json()) as MySandboxOut[]
    },
    { revalidateOnFocus: false },
  )
  return { data, error, isLoading, mutate }
}

/** Soft restart: kill the container, keep the row + PVC. */
export async function restartMySandbox(wsId: string, sandboxId: string): Promise<void> {
  const client = createApiClient('')
  const res = await client.post(`/api/v1/ws/${wsId}/sandboxes/${sandboxId}/restart`, {})
  if (!res.ok) {
    throw new Error(`restart sandbox failed: ${res.status}`)
  }
}

/** Hard delete: soft-delete the row + kill the container. PVC is left as an
 * orphan for operator cleanup. */
export async function deleteMySandbox(wsId: string, sandboxId: string): Promise<void> {
  const client = createApiClient('')
  const res = await client.del(`/api/v1/ws/${wsId}/sandboxes/${sandboxId}`)
  if (!res.ok) {
    throw new Error(`delete sandbox failed: ${res.status}`)
  }
}
