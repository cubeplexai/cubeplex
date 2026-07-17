'use client'

import { useState, useEffect } from 'react'
import type { Artifact } from '@cubeplex/core'
import { PreviewLoading } from './PreviewLoading'
import { buildPreviewUrl } from './previewUtils'
import { CodeHighlight } from '@/components/shared/previews'

interface CodePreviewProps {
  artifact: Artifact
  version: number | null
  workspaceId: string
}

export function CodePreview({ artifact, version, workspaceId }: CodePreviewProps) {
  const [code, setCode] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const filename = artifact.entry_file || artifact.path.split('/').pop() || 'file'
  const previewUrl = buildPreviewUrl(artifact, filename, version, workspaceId)

  useEffect(() => {
    fetch(previewUrl)
      .then((res) => {
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
        return res.text()
      })
      .then(setCode)
      .catch((e) => setError(e.message))
  }, [previewUrl])

  if (error) {
    return <div className="p-4 text-sm text-destructive">Failed to load file: {error}</div>
  }

  if (code === null) {
    return <PreviewLoading />
  }

  return <CodeHighlight code={code} filename={filename} />
}
