'use client'

import { useState, useEffect, useMemo } from 'react'
import type { Artifact } from '@cubeplex/core'
import { PreviewLoading } from './PreviewLoading'
import { buildPreviewUrl } from './previewUtils'
import { CsvTable } from '@/components/shared/previews'

interface DataPreviewProps {
  artifact: Artifact
  version: number | null
  workspaceId: string
}

function JsonTable({ data }: { data: unknown }) {
  if (Array.isArray(data) && data.length > 0 && typeof data[0] === 'object') {
    const headers = Object.keys(data[0] as Record<string, unknown>)
    return (
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="border-b border-border bg-muted/50">
            {headers.map((h) => (
              <th key={h} className="text-left p-2 font-medium text-muted-foreground">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((row, i) => (
            <tr key={i} className="border-b border-border/50 hover:bg-muted/30">
              {headers.map((h) => (
                <td key={h} className="p-2 text-foreground">
                  {String((row as Record<string, unknown>)[h] ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    )
  }

  return (
    <pre className="p-4 text-xs font-mono text-foreground whitespace-pre-wrap">
      {JSON.stringify(data, null, 2)}
    </pre>
  )
}

export function DataPreview({ artifact, version, workspaceId }: DataPreviewProps) {
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

  const isCsv = /\.csv$/i.test(filename)

  const parsed = useMemo(() => {
    if (!content) return null
    if (isCsv) return { type: 'csv' as const }
    try {
      return { type: 'json' as const, data: JSON.parse(content) }
    } catch {
      return { type: 'text' as const, data: content }
    }
  }, [content, isCsv])

  if (error) {
    return <div className="p-4 text-sm text-destructive">Failed to load data: {error}</div>
  }

  if (!parsed || !content) {
    return <PreviewLoading />
  }

  if (parsed.type === 'csv') {
    return <CsvTable content={content} />
  }

  if (parsed.type === 'json') {
    return (
      <div className="overflow-auto h-full">
        <JsonTable data={parsed.data} />
      </div>
    )
  }

  return (
    <div className="p-4">
      <pre className="text-xs font-mono whitespace-pre-wrap text-foreground">{parsed.data}</pre>
    </div>
  )
}
