import type { Artifact } from '@cubebox/core'

export function buildPreviewUrl(
  artifact: Artifact,
  filePath: string,
  version: number | null,
  workspaceId: string,
): string {
  // Version goes in the path (not a query) so relative URLs inside the
  // served HTML — e.g. `<iframe src="slides/01.html">` — automatically
  // pick up the same version prefix when the browser resolves them.
  // Query strings are dropped during relative-URL resolution.
  const v = version ?? artifact.version
  return (
    `/api/v1/ws/${workspaceId}/conversations/${artifact.conversation_id}` +
    `/artifacts/${artifact.id}/preview/v${v}/${filePath}`
  )
}

export function buildDownloadUrl(
  artifact: Artifact,
  workspaceId: string,
  version?: number | null,
): string {
  const base =
    `/api/v1/ws/${workspaceId}/conversations/${artifact.conversation_id}` +
    `/artifacts/${artifact.id}/download`
  const v = version ?? artifact.version
  return `${base}?version=${v}`
}
