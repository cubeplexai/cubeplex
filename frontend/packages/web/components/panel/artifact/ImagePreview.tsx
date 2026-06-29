'use client'

import { useState, useEffect } from 'react'
import type { Artifact } from '@cubebox/core'
import { buildPreviewUrl, hasImageExt } from './previewUtils'
import { ImageViewer } from '@/components/shared/previews'
import { PreviewLoading } from './PreviewLoading'
import { FallbackPreview } from './FallbackPreview'
import { ImageCarousel } from './ImageCarousel'

interface ImagePreviewProps {
  artifact: Artifact
  version: number | null
  workspaceId: string
}

interface FilesResponse {
  version: number
  files: string[]
}

export function ImagePreview({
  artifact,
  version,
  workspaceId,
}: ImagePreviewProps): React.ReactElement {
  const filename = artifact.entry_file || artifact.path.split('/').pop() || ''

  const v = version ?? artifact.version
  // Tag the cached file list with the version it was fetched for, so a
  // version switch shows loading instead of the old version's images while
  // the new /files request is in flight.
  const [data, setData] = useState<{ files: string[]; version: number } | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    if (hasImageExt(filename)) return
    let cancelled = false
    const url =
      `/api/v1/ws/${workspaceId}/conversations/${artifact.conversation_id}` +
      `/artifacts/${artifact.id}/files?filter=image&version=${v}`
    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`${res.status}`)
        return res.json() as Promise<FilesResponse>
      })
      .then((body) => {
        if (!cancelled) setData({ files: body.files, version: v })
      })
      .catch(() => {
        if (!cancelled) setError(true)
      })
    return () => {
      cancelled = true
    }
  }, [artifact.id, artifact.conversation_id, v, workspaceId, filename])

  // Heuristic: a path that already points at an image file → single image,
  // no list call. Otherwise (directory like /workspace/charts) fetch the
  // file list and render a carousel.
  if (hasImageExt(filename)) {
    const url = buildPreviewUrl(artifact, filename, version, workspaceId)
    return <ImageViewer url={url} alt={artifact.name} />
  }

  if (error) {
    return <FallbackPreview artifact={artifact} version={version} workspaceId={workspaceId} />
  }
  // No data yet, or data is from a different version than the one selected.
  if (!data || data.version !== v) {
    return <PreviewLoading />
  }
  const files = data.files
  if (files.length === 0) {
    return <FallbackPreview artifact={artifact} version={version} workspaceId={workspaceId} />
  }
  if (files.length === 1) {
    const url = buildPreviewUrl(artifact, files[0], version, workspaceId)
    return <ImageViewer url={url} alt={artifact.name} />
  }
  return (
    <ImageCarousel
      artifact={artifact}
      imageFiles={files}
      version={version}
      workspaceId={workspaceId}
    />
  )
}
