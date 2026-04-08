'use client'

import { useState } from 'react'
import type { Artifact } from '@cubebox/core'
import { PreviewLoading } from './PreviewLoading'

interface HtmlPreviewProps {
  artifact: Artifact
}

export function HtmlPreview({ artifact }: HtmlPreviewProps) {
  const [loading, setLoading] = useState(true)
  const entryFile = artifact.entry_file || 'index.html'
  const previewUrl =
    `/api/v1/conversations/${artifact.conversation_id}/artifacts/${artifact.id}/preview/${entryFile}`

  return (
    <div className="relative w-full h-full">
      {loading && (
        <div className="absolute inset-0 bg-background">
          <PreviewLoading />
        </div>
      )}
      <iframe
        src={previewUrl}
        className="w-full h-full border-0"
        sandbox="allow-scripts allow-same-origin"
        title={artifact.name}
        onLoad={() => setLoading(false)}
      />
    </div>
  )
}
