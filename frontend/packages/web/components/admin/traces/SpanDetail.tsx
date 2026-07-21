'use client'

import { useTranslations } from 'next-intl'
import { RotateCw } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import type { SpanNode } from './types'
import { JsonBlock } from './cards/JsonBlock'
import { LlmCard } from './cards/LlmCard'
import { Section } from './cards/Section'
import { ToolCard } from './cards/ToolCard'
import { KIND_BADGE } from './kindStyles'

interface Props {
  node: SpanNode
}

// Matches the formatDuration shape already used in TraceListTable.tsx (repo
// convention: this small helper is duplicated per-file rather than shared).
function formatDuration(ms: number): string {
  if (ms < 0) return '0s'
  if (ms < 1000) return `${ms}ms`
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return s > 0 ? `${m}m${s}s` : `${m}m`
}

export function SpanDetail({ node }: Props) {
  const t = useTranslations('adminTraces.sections')
  return (
    <div className="space-y-4 p-4">
      <div>
        <div className="flex items-center gap-2">
          <h2 className="text-base font-semibold">{node.name}</h2>
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
              KIND_BADGE[node.kind] ?? KIND_BADGE.other
            }`}
          >
            {node.kind}
          </span>
        </div>
        <p className="text-xs text-muted-foreground" title={`${node.duration_ms} ms`}>
          {formatDuration(node.duration_ms)} · {new Date(node.start_time).toLocaleString()}
        </p>
      </div>
      {node.llm && <LlmCard llm={node.llm} />}
      {node.tool && <ToolCard tool={node.tool} />}
      {node.turn && (
        <Card className="flex-row items-center gap-3 p-4">
          <div className="rounded-md bg-warning-surface p-2 text-warning-fg">
            <RotateCw className="size-4" />
          </div>
          <div className="space-y-1 text-xs">
            <div className="text-sm font-semibold">Turn {node.turn.index}</div>
            <div className="flex items-center gap-2 text-muted-foreground">
              <span>stop:</span>
              <Badge variant="outline">{node.turn.stop_reason ?? '—'}</Badge>
              <span>tool calls: {node.turn.tool_calls_count}</span>
            </div>
          </div>
        </Card>
      )}
      <Section title={t('rawAttributes')} defaultOpen={node.kind === 'other'}>
        <JsonBlock value={JSON.stringify(node.raw_attributes, null, 2)} />
      </Section>
    </div>
  )
}
