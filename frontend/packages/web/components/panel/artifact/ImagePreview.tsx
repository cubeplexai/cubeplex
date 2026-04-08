'use client'

import { useState } from 'react'
import type { Artifact } from '@cubebox/core'
import { PreviewLoading } from './PreviewLoading'

interface ImagePreviewProps {
  artifact: Artifact
}

export function ImagePreview({ artifact }: ImagePreviewProps) {
  const [loading, setLoading] = useState(true)
  const filename = artifact.path.split('/').pop() || 'image'
  const previewUrl =
    `/api/v1/conversations/${artifact.conversation_id}/artifacts/${artifact.id}/preview/${filename}`

  return (
    <div className="flex items-center justify-center h-full p-4 bg-muted/20">
      {loading && (
        <div className="absolute inset-0">
          <PreviewLoading />
        </div>
      )}
      <img
        src={previewUrl}
        alt={artifact.name}
        className="max-w-full max-h-full object-contain rounded-md"
        onLoad={() => setLoading(false)}
      />
    </div>
  )
}
