'use client'

import { useState, useEffect } from 'react'
import type { Artifact } from '@cubeplex/core'
import { proseClasses } from '@/lib/utils'
import { MarkdownWithCitations } from '@/components/shared/MarkdownWithCitations'
import { PreviewLoading } from './PreviewLoading'
import { buildPreviewUrl } from './previewUtils'

interface DocumentPreviewProps {
  artifact: Artifact
  version: number | null
  workspaceId: string
}

export function DocumentPreview({ artifact, version, workspaceId }: DocumentPreviewProps) {
  const [content, setContent] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const filename = artifact.entry_file || artifact.path.split('/').pop() || 'file'
  const previewUrl = buildPreviewUrl(artifact, filename, version, workspaceId)

  useEffect(() => {
    fetch(previewUrl)
      .then((res) => {
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
        return res.text()
      })
      .then(setContent)
      .catch((e) => setError(e.message))
  }, [previewUrl])

  if (error) {
    return <div className="p-4 text-sm text-destructive">Failed to load document: {error}</div>
  }

  if (content === null) {
    return <PreviewLoading />
  }

  const isMarkdown = /\.(md|markdown|mdx)$/i.test(filename)

  if (isMarkdown) {
    return (
      <div className="h-full overflow-auto">
        <MarkdownWithCitations className={`p-4 ${proseClasses}`}>{content}</MarkdownWithCitations>
      </div>
    )
  }

  return (
    <div className="h-full overflow-auto p-4">
      <pre className="text-sm leading-relaxed whitespace-pre-wrap break-words text-foreground">
        {content}
      </pre>
    </div>
  )
}
