'use client'

import { useTranslations } from 'next-intl'
import type { SpanNode } from './types'
import { AgentCard } from './cards/AgentCard'
import { JsonBlock } from './cards/JsonBlock'
import { LlmCard } from './cards/LlmCard'
import { Section } from './cards/Section'
import { ToolCard } from './cards/ToolCard'
import { TurnCard } from './cards/TurnCard'
import { KIND_BADGE } from './kindStyles'

interface Props {
  node: SpanNode
}

// Distinct chat models used anywhere under this agent span - an agent run
// can call more than one model across turns, so this isn't a single value.
function collectModels(node: SpanNode): string[] {
  const models = new Set<string>()
  const walk = (n: SpanNode) => {
    if (n.llm?.model) models.add(n.llm.model)
    for (const c of n.children) walk(c)
  }
  walk(node)
  return Array.from(models)
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
      {node.turn && <TurnCard turn={node.turn} />}
      {node.agent && <AgentCard agent={node.agent} models={collectModels(node)} />}
      <Section title={t('rawAttributes')} defaultOpen={node.kind === 'other'}>
        <JsonBlock value={JSON.stringify(node.raw_attributes, null, 2)} />
      </Section>
    </div>
  )
}
