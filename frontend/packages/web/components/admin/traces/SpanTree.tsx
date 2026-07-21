'use client'

import { useMemo } from 'react'
import type { SpanNode } from './types'
import { KIND_BADGE, KIND_BAR } from './kindStyles'

interface Props {
  root: SpanNode
  selectedSpanId: string
  onSelect: (id: string) => void
}

interface FlatRow {
  node: SpanNode
  depth: number
  offsetMs: number
  totalMs: number
}

function flatten(root: SpanNode): FlatRow[] {
  const rootStart = new Date(root.start_time).getTime()
  const totalMs = root.duration_ms
  const out: FlatRow[] = []
  const walk = (n: SpanNode, depth: number) => {
    const off = new Date(n.start_time).getTime() - rootStart
    out.push({ node: n, depth, offsetMs: off, totalMs })
    for (const c of n.children) walk(c, depth + 1)
  }
  walk(root, 0)
  return out
}

export function SpanTree({ root, selectedSpanId, onSelect }: Props) {
  const rows = useMemo(() => flatten(root), [root])
  return (
    <ul className="select-none">
      {rows.map(({ node, depth, offsetMs, totalMs }) => {
        const left = totalMs ? (offsetMs / totalMs) * 100 : 0
        const width = totalMs ? Math.max((node.duration_ms / totalMs) * 100, 0.5) : 0
        const selected = node.span_id === selectedSpanId
        return (
          <li
            key={node.span_id}
            role="button"
            tabIndex={0}
            onClick={() => onSelect(node.span_id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onSelect(node.span_id)
              }
            }}
            className={`flex cursor-pointer items-center gap-3 border-b border-border/30 px-3 py-1.5 text-xs ${
              selected ? 'bg-primary/10' : 'hover:bg-muted/30'
            }`}
          >
            <div className="flex-1 truncate" style={{ paddingLeft: depth * 16 }}>
              <span className="truncate font-medium">{node.name}</span>
              <span
                className={`ml-2 rounded px-1.5 py-0.5 text-[10px] ${
                  KIND_BADGE[node.kind] ?? KIND_BADGE.other
                }`}
              >
                {node.kind}
              </span>
            </div>
            <div className="relative h-4 w-[40%] rounded bg-muted/30">
              <div
                className={`absolute h-3 rounded ${KIND_BAR[node.kind] ?? KIND_BAR.other}`}
                style={{ left: `${left}%`, width: `${width}%`, top: 2 }}
              />
            </div>
            <span className="w-16 text-right font-mono text-muted-foreground">
              {node.duration_ms} ms
            </span>
          </li>
        )
      })}
    </ul>
  )
}
