'use client'

import { useMemo } from 'react'

interface CsvTableProps {
  content: string
}

function parseCsv(text: string): { headers: string[]; rows: string[][] } {
  const lines = text.trim().split('\n')
  if (lines.length === 0) return { headers: [], rows: [] }
  const parse = (line: string): string[] =>
    line.split(',').map((c) => c.trim().replace(/^"|"$/g, ''))
  return { headers: parse(lines[0]), rows: lines.slice(1).map(parse) }
}

export function CsvTable({ content }: CsvTableProps) {
  const { headers, rows } = useMemo(() => parseCsv(content), [content])

  if (headers.length === 0) {
    return <div className="p-4 text-sm text-muted-foreground">Empty CSV</div>
  }

  return (
    <div className="overflow-auto h-full">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="border-b border-border bg-muted/50 sticky top-0">
            {headers.map((h, i) => (
              <th key={i} className="text-left p-2 font-medium text-muted-foreground">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b border-border/50 hover:bg-muted/30">
              {row.map((cell, j) => (
                <td key={j} className="p-2 text-foreground whitespace-nowrap">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
