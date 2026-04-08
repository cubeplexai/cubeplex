'use client'

import { File, Download } from 'lucide-react'
import type { Artifact } from '@cubebox/core'

interface FallbackPreviewProps {
  artifact: Artifact
}

export function FallbackPreview({ artifact }: FallbackPreviewProps) {
  const downloadUrl =
    `/api/v1/conversations/${artifact.conversation_id}/artifacts/${artifact.id}/download`

  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 p-8 text-center">
      <div className="flex size-16 items-center justify-center rounded-xl bg-muted">
        <File className="size-8 text-muted-foreground" />
      </div>
      <div>
        <h3 className="text-sm font-medium text-foreground">{artifact.name}</h3>
        {artifact.description && (
          <p className="mt-1 text-xs text-muted-foreground">{artifact.description}</p>
        )}
        <p className="mt-1 text-xs text-muted-foreground/60">
          {artifact.mime_type || 'Unknown type'} &middot; v{artifact.version}
        </p>
        <p className="mt-2 text-xs text-muted-foreground">
          This file type does not support preview. Please download to view.
        </p>
      </div>
      <a
        href={downloadUrl}
        className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2
          text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
      >
        <Download className="size-4" />
        Download
      </a>
    </div>
  )
}
