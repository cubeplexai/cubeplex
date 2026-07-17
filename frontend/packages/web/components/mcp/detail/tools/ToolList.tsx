'use client'

import type { MCPToolEntry } from '@cubeplex/core'
import { useTranslations } from 'next-intl'

import { getProperties, getRequired, type SchemaNode } from '@/lib/jsonSchemaTypes'
import { MasterDetailList } from '../MasterDetailList'

export interface ToolListProps {
  tools: MCPToolEntry[]
  filtered: MCPToolEntry[]
  query: string
  onQueryChange: (query: string) => void
  selectedName: string | null
  onSelect: (name: string) => void
}

function countArgs(schema: unknown): { args: number; required: number } {
  if (!schema || typeof schema !== 'object') return { args: 0, required: 0 }
  const node = schema as SchemaNode
  return { args: Object.keys(getProperties(node)).length, required: getRequired(node).length }
}

export function ToolList({
  tools,
  filtered,
  query,
  onQueryChange,
  selectedName,
  onSelect,
}: ToolListProps) {
  const t = useTranslations('mcp.tools')
  const trimmed = query.trim()

  return (
    <MasterDetailList<MCPToolEntry>
      items={tools}
      getKey={(tool) => tool.name}
      filter={(tool, q) => {
        const lq = q.toLowerCase()
        return (
          tool.name.toLowerCase().includes(lq) ||
          (tool.description ?? '').toLowerCase().includes(lq)
        )
      }}
      selectedKey={selectedName}
      onSelect={onSelect}
      searchPlaceholder={t('filterPlaceholder')}
      countLabel={(_matched, _total, q) =>
        q
          ? t('countMatch', { matched: filtered.length, total: tools.length })
          : t('countAll', { count: tools.length })
      }
      emptyState={trimmed ? t('emptyMatch', { query: trimmed }) : t('empty')}
      emptyMatchState={(q) => t('emptyMatch', { query: q })}
      query={query}
      onQueryChange={onQueryChange}
      renderItem={(tool) => {
        const { args, required } = countArgs(tool.input_schema)
        return (
          <>
            <span className="truncate font-mono text-sm font-semibold">{tool.name}</span>
            {tool.description ? (
              <span className="truncate text-xs text-muted-foreground">{tool.description}</span>
            ) : null}
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
              {t('argsSummary', { count: args })}
              {required > 0 ? ` · ${t('requiredSummary', { count: required })}` : ''}
            </span>
          </>
        )
      }}
    />
  )
}
