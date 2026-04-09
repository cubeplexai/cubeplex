'use client'

import { useState } from 'react'
import type { Artifact } from '@cubebox/core'
import { PreviewLoading } from './PreviewLoading'
import { buildPreviewUrl } from './previewUtils'

interface HtmlPreviewProps {
  artifact: Artifact
  version: number | null
}

export function HtmlPreview({ artifact, version }: HtmlPreviewProps) {
  const [loading, setLoading] = useState(true)
  const entryFile = artifact.entry_file || 'index.html'
  const previewUrl = buildPreviewUrl(artifact, entryFile, version)

  return (
    <div className="relative w-full h-full">
      {loading && (
        <div className="absolute inset-0 bg-background">
          <PreviewLoading />
        </div>
      )}
      <iframe
        key={`${artifact.id}-${version}`}
        src={previewUrl}
        className="w-full h-full border-0"
        sandbox="allow-scripts allow-same-origin"
        title={artifact.name}
        onLoad={() => setLoading(false)}
      />
    </div>
  )
}
