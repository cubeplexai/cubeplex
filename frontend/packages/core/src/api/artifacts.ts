import { toApiError, type ApiClient } from './client'
import type { Artifact } from '../types'

export interface ListWorkspaceArtifactsParams {
  type?: string
  q?: string
  limit?: number
  offset?: number
}

export async function listWorkspaceArtifacts(
  client: ApiClient,
  params: ListWorkspaceArtifactsParams = {},
): Promise<{ artifacts: Artifact[]; total: number }> {
  const qs = new URLSearchParams()
  if (params.type) qs.set('type', params.type)
  if (params.q) qs.set('q', params.q)
  if (params.limit != null) qs.set('limit', String(params.limit))
  if (params.offset != null) qs.set('offset', String(params.offset))
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  const res = await client.get(`/api/v1/artifacts${suffix}`)
  if (!res.ok) throw await toApiError(res)
  const data = (await res.json()) as { artifacts?: Artifact[]; total?: number }
  return { artifacts: data.artifacts ?? [], total: data.total ?? 0 }
}

export async function deleteArtifact(client: ApiClient, artifactId: string): Promise<void> {
  const res = await client.del(`/api/v1/artifacts/${artifactId}`)
  if (!res.ok) throw await toApiError(res)
}
