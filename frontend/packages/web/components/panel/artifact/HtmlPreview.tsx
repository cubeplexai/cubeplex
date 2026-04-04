'use client'

import type { Artifact } from '@cubebox/core'

interface HtmlPreviewProps {
  artifact: Artifact
}

export function HtmlPreview({ artifact }: HtmlPreviewProps) {
  const entryFile = artifact.entry_file || 'index.html'
  const previewUrl =
    `/api/v1/conversations/${artifact.conversation_id}/artifacts/${artifact.id}/preview/${entryFile}`

  return (
    <iframe
      src={previewUrl}
      className="w-full h-full border-0"
      sandbox="allow-scripts allow-same-origin"
      title={artifact.name}
    />
  )
}
