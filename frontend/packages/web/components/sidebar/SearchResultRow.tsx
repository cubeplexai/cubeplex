'use client'

import Link from 'next/link'
import { useMemo } from 'react'
import type { SearchResult } from '@cubebox/core'
import { cn } from '@/lib/utils'

interface Props {
  result: SearchResult
  wsId: string
  active: boolean
  onPick: () => void
}

export function SearchResultRow({ result, wsId, active, onPick }: Props): React.ReactElement {
  const href = `/w/${wsId}/conversations/${result.conversation_id}${
    result.matched_message_seq ? `#msg-${result.matched_message_seq}` : ''
  }`
  const segments = useMemo(
    () => splitSnippet(result.snippet, result.match_offsets),
    [result.snippet, result.match_offsets],
  )
  return (
    <li>
      <Link
        href={href}
        onClick={onPick}
        className={cn(
          'group flex flex-col gap-0.5 rounded px-3 py-2 text-xs transition-colors duration-fast',
          active
            ? 'bg-accent text-foreground'
            : 'text-muted-foreground hover:bg-accent hover:text-foreground',
        )}
      >
        <span className="truncate text-[12.5px] font-medium leading-tight">
          {result.title || 'Untitled'}
        </span>
        <span className="line-clamp-2 text-2xs leading-snug text-faint">
          {segments.map((s, i) =>
            s.match ? (
              <mark key={i} className="bg-primary/20 text-foreground rounded-sm">
                {s.text}
              </mark>
            ) : (
              <span key={i}>{s.text}</span>
            ),
          )}
        </span>
      </Link>
    </li>
  )
}

function splitSnippet(
  snippet: string,
  offsets: [number, number][],
): { text: string; match: boolean }[] {
  if (offsets.length === 0) return [{ text: snippet, match: false }]
  const sorted = [...offsets].sort((a, b) => a[0] - b[0])
  const out: { text: string; match: boolean }[] = []
  let cursor = 0
  for (const [start, end] of sorted) {
    if (start > cursor) out.push({ text: snippet.slice(cursor, start), match: false })
    out.push({ text: snippet.slice(start, end), match: true })
    cursor = end
  }
  if (cursor < snippet.length) out.push({ text: snippet.slice(cursor), match: false })
  return out
}
