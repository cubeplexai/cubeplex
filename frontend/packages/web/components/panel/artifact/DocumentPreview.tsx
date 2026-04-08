'use client'

import { useState, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Artifact } from '@cubebox/core'
import { proseClasses } from '@/lib/utils'
import { PreviewLoading } from './PreviewLoading'

interface DocumentPreviewProps {
  artifact: Artifact
}

export function DocumentPreview({ artifact }: DocumentPreviewProps) {
  const [content, setContent] = useState<string | null>(null)
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
      .then(setContent)
      .catch(e => setError(e.message))
  }, [previewUrl])

  if (error) {
    return (
      <div className="p-4 text-sm text-destructive">
        Failed to load document: {error}
      </div>
    )
  }

  if (content === null) {
    return <PreviewLoading />
  }

  const isMarkdown = /\.(md|markdown|mdx)$/i.test(filename)

  if (isMarkdown) {
    return (
      <div className={`p-4 ${proseClasses}`}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
      </div>
    )
  }

  return (
    <div className="p-4">
      <pre className="text-sm leading-relaxed whitespace-pre-wrap break-words text-foreground">
        {content}
      </pre>
    </div>
  )
}
