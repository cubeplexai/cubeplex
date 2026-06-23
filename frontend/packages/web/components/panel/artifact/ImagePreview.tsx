'use client'

import type { Artifact } from '@cubebox/core'
import { buildPreviewUrl } from './previewUtils'
import { ImageViewer } from '@/components/shared/previews'

interface ImagePreviewProps {
  artifact: Artifact
  version: number | null
  workspaceId: string
}

export function ImagePreview({ artifact, version, workspaceId }: ImagePreviewProps) {
  const filename = artifact.path.split('/').pop() || 'image'
  const previewUrl = buildPreviewUrl(artifact, filename, version, workspaceId)

  return <ImageViewer url={previewUrl} alt={artifact.name} />
}
