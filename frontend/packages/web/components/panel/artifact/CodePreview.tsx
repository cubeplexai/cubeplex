'use client'

import { useState, useEffect } from 'react'
import type { Artifact } from '@cubebox/core'

interface CodePreviewProps {
  artifact: Artifact
}

export function CodePreview({ artifact }: CodePreviewProps) {
  const [code, setCode] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const filename = artifact.entry_file || artifact.path.split('/').pop() || 'file'
  const previewUrl =
    `/api/v1/conversations/${artifact.conversation_id}/artifacts/${artifact.id}/preview/${filename}`

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
    return (
      <div className="p-4 text-sm text-muted-foreground animate-pulse">
        Loading...
      </div>
    )
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
