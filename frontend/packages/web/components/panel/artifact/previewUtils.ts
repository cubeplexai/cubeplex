import type { Artifact } from '@cubebox/core'

export function buildPreviewUrl(
  artifact: Artifact,
  filePath: string,
  version: number | null,
  workspaceId: string,
): string {
  const base =
    `/api/v1/ws/${workspaceId}/conversations/${artifact.conversation_id}` +
    `/artifacts/${artifact.id}/preview/${filePath}`
  return version != null ? `${base}?version=${version}` : base
}

export function buildDownloadUrl(
  artifact: Artifact,
  workspaceId: string,
  version?: number | null,
): string {
  const base =
    `/api/v1/ws/${workspaceId}/conversations/${artifact.conversation_id}` +
    `/artifacts/${artifact.id}/download`
  return version != null ? `${base}?version=${version}` : base
}
