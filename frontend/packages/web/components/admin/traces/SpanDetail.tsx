'use client'

import { useTranslations } from 'next-intl'
import type { SpanNode } from './types'
import { JsonBlock } from './cards/JsonBlock'
import { LlmCard } from './cards/LlmCard'
import { ToolCard } from './cards/ToolCard'

interface Props {
  node: SpanNode
}

export function SpanDetail({ node }: Props) {
  const t = useTranslations('adminTraces.sections')
  return (
    <div className="space-y-4 p-4">
      <div>
        <h2 className="text-base font-semibold">{node.name}</h2>
        <p className="text-xs text-muted-foreground">
          {node.kind} · {node.duration_ms} ms · {new Date(node.start_time).toLocaleString()}
        </p>
      </div>
      {node.llm && <LlmCard llm={node.llm} />}
      {node.tool && <ToolCard tool={node.tool} />}
      {node.turn && (
        <div className="rounded border border-border bg-card p-3 text-xs">
          <div className="font-semibold">Turn {node.turn.index}</div>
          <div className="text-muted-foreground">
            stop: {node.turn.stop_reason ?? '—'} · tool_calls: {node.turn.tool_calls_count}
          </div>
        </div>
      )}
      <details className="rounded border border-border bg-card" open={node.kind === 'other'}>
        <summary className="cursor-pointer px-3 py-2 text-sm font-medium select-none">
          {t('rawAttributes')}
        </summary>
        <div className="border-t border-border px-3 py-3">
          <JsonBlock value={JSON.stringify(node.raw_attributes, null, 2)} />
        </div>
      </details>
    </div>
  )
}
