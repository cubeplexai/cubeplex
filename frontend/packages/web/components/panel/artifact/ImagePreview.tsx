'use client'

import { useState } from 'react'
import type { Artifact } from '@cubebox/core'
import { PreviewLoading } from './PreviewLoading'
import { buildPreviewUrl } from './previewUtils'

interface ImagePreviewProps {
  artifact: Artifact
  version: number | null
  workspaceId: string
}

export function ImagePreview({ artifact, version, workspaceId }: ImagePreviewProps) {
  const [loading, setLoading] = useState(true)
  const filename = artifact.path.split('/').pop() || 'image'
  const previewUrl = buildPreviewUrl(artifact, filename, version, workspaceId)

  return (
    <div className="flex items-center justify-center h-full p-4 bg-muted/20">
      {loading && (
        <div className="absolute inset-0">
          <PreviewLoading />
        </div>
      )}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        key={`${artifact.id}-${version}`}
        src={previewUrl}
        alt={artifact.name}
        className="max-w-full max-h-full object-contain rounded-md"
        onLoad={() => setLoading(false)}
      />
    </div>
  )
}
