'use client'

import { useState, useEffect } from 'react'
import type { Artifact } from '@cubebox/core'
import { PreviewLoading } from './PreviewLoading'
import { buildPreviewUrl } from './previewUtils'

interface CodePreviewProps {
  artifact: Artifact
  version: number | null
}

export function CodePreview({ artifact, version }: CodePreviewProps) {
  const [code, setCode] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const filename = artifact.entry_file || artifact.path.split('/').pop() || 'file'
  const previewUrl = buildPreviewUrl(artifact, filename, version)

  useEffect(() => {
    fetch(previewUrl)
      .then(res => {
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
        return res.text()
      })
      .then(setCode)
      .catch(e => setError(e.message))
  }, [previewUrl])

  if (error) {
    return (
      <div className="p-4 text-sm text-destructive">
        Failed to load file: {error}
      </div>
    )
  }

  if (code === null) {
    return <PreviewLoading />
  }

  return (
    <div className="h-full overflow-auto">
      <pre className="p-4 text-xs leading-relaxed font-mono text-foreground whitespace-pre-wrap
        break-words">
        {code}
      </pre>
    </div>
  )
}
