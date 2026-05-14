'use client'

import type { MCPToolEntry } from '@cubebox/core'
import { Search } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'
import { getProperties, getRequired, type SchemaNode } from '@/lib/jsonSchemaTypes'

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
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          placeholder={t('filterPlaceholder')}
          aria-label={t('filterPlaceholder')}
          className="h-9 pl-7 text-sm"
        />
      </div>
      <p className="px-1 text-xs text-muted-foreground">
        {trimmed
          ? t('countMatch', { matched: filtered.length, total: tools.length })
          : t('countAll', { count: tools.length })}
      </p>
      <ScrollArea className="min-h-0 flex-1">
        <ul
          aria-label={t('countAll', { count: tools.length })}
          className="flex flex-col gap-0.5 pr-1"
        >
          {filtered.length === 0 ? (
            <li className="px-3 py-6 text-center text-xs text-muted-foreground">
              {trimmed ? t('emptyMatch', { query: trimmed }) : t('empty')}
            </li>
          ) : (
            filtered.map((tool) => {
              const { args, required } = countArgs(tool.input_schema)
              const selected = tool.name === selectedName
              return (
                <li key={tool.name}>
                  <button
                    type="button"
                    aria-pressed={selected}
                    onClick={() => onSelect(tool.name)}
                    className={cn(
                      'flex w-full flex-col gap-1 rounded-md border border-transparent px-3 py-2 text-left transition',
                      selected ? 'border-l-2 border-l-primary bg-primary/5' : 'hover:bg-muted/60',
                    )}
                  >
                    <span className="truncate font-mono text-sm font-semibold">{tool.name}</span>
                    {tool.description ? (
                      <span className="truncate text-xs text-muted-foreground">
                        {tool.description}
                      </span>
                    ) : null}
                    <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                      {t('argsSummary', { count: args })}
                      {required > 0 ? ` · ${t('requiredSummary', { count: required })}` : ''}
                    </span>
                  </button>
                </li>
              )
            })
          )}
        </ul>
      </ScrollArea>
    </div>
  )
}
