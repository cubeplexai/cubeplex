import type { Artifact } from '@cubebox/core'

export function buildPreviewUrl(
  artifact: Artifact,
  filePath: string,
  version: number | null,
): string {
  const base =
    `/api/v1/conversations/${artifact.conversation_id}/artifacts/${artifact.id}/preview/${filePath}`
  return version != null ? `${base}?version=${version}` : base
}
