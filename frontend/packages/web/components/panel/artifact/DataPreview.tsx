'use client'

import { useState, useEffect, useMemo } from 'react'
import type { Artifact } from '@cubebox/core'
import { PreviewLoading } from './PreviewLoading'

interface DataPreviewProps {
  artifact: Artifact
}

function parseCsv(text: string): { headers: string[]; rows: string[][] } {
  const lines = text.trim().split('\n')
  if (lines.length === 0) return { headers: [], rows: [] }
  const headers = lines[0].split(',').map(h => h.trim().replace(/^"|"$/g, ''))
  const rows = lines.slice(1).map(line =>
    line.split(',').map(cell => cell.trim().replace(/^"|"$/g, ''))
  )
  return { headers, rows }
}

function JsonTable({ data }: { data: unknown }) {
  if (Array.isArray(data) && data.length > 0 && typeof data[0] === 'object') {
    const headers = Object.keys(data[0] as Record<string, unknown>)
    return (
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="border-b border-border bg-muted/50">
            {headers.map(h => (
              <th key={h} className="text-left p-2 font-medium text-muted-foreground">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((row, i) => (
            <tr key={i} className="border-b border-border/50 hover:bg-muted/30">
              {headers.map(h => (
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

export function DataPreview({ artifact }: DataPreviewProps) {
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

  const isCsv = /\.csv$/i.test(filename)

  const parsed = useMemo(() => {
    if (!content) return null
    if (isCsv) return { type: 'csv' as const, data: parseCsv(content) }
    try {
      return { type: 'json' as const, data: JSON.parse(content) }
    } catch {
      return { type: 'text' as const, data: content }
    }
  }, [content, isCsv])

  if (error) {
    return <div className="p-4 text-sm text-destructive">Failed to load data: {error}</div>
  }

  if (!parsed) {
    return <PreviewLoading />
  }

  if (parsed.type === 'csv') {
    const { headers, rows } = parsed.data
    return (
      <div className="overflow-auto h-full">
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr className="border-b border-border bg-muted/50 sticky top-0">
              {headers.map(h => (
                <th key={h} className="text-left p-2 font-medium text-muted-foreground">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-b border-border/50 hover:bg-muted/30">
                {row.map((cell, j) => (
                  <td key={j} className="p-2 text-foreground">{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
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
