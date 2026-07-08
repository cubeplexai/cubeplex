'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  adminUpsertToolCitation,
  type ApiClient,
  type CitationConfigJSON,
  type MCPConnector,
  type MCPToolEntry,
} from '@cubebox/core'

import { MasterDetailList } from './detail/MasterDetailList'
import { MCPCitationEditor } from './MCPCitationEditor'
import { getProperties, type SchemaNode } from '@/lib/jsonSchemaTypes'

export interface MCPCitationsTabProps {
  install: MCPConnector
  client: ApiClient
  onUpdated: (install: MCPConnector) => void
}

function extractOutputFieldCandidates(tool: MCPToolEntry): string[] {
  // Top-level keys of the tool's declared output_schema. MCP servers may
  // omit outputSchema entirely (it's optional in the spec); in that case
  // the editor falls back to a free-text input with no datalist hints.
  // Do NOT fall back to input_schema — input args and output fields are
  // unrelated, so suggesting input keys would mislead the operator.
  if (!tool.output_schema || typeof tool.output_schema !== 'object') return []
  return Object.keys(getProperties(tool.output_schema as SchemaNode))
}

export function MCPCitationsTab({ install, client, onUpdated }: MCPCitationsTabProps) {
  const t = useTranslations('mcp.citations')
  const tools = install.tools
  const citations = install.tool_citations ?? {}

  const [selected, setSelected] = useState<string | null>(tools[0]?.name ?? null)
  const [query, setQuery] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const selectedTool = useMemo(
    () => tools.find((tool) => tool.name === selected) ?? null,
    [tools, selected],
  )

  async function handleChange(next: CitationConfigJSON | null): Promise<void> {
    if (!selectedTool) return
    setSaving(true)
    setError(null)
    try {
      const updated = await adminUpsertToolCitation(
        client,
        install.connector_id,
        selectedTool.name,
        next as Record<string, unknown> | null,
      )
      onUpdated(updated)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  if (tools.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border px-6 py-12 text-center text-sm text-muted-foreground">
        {t('emptyNoTools')}
      </div>
    )
  }

  return (
    <div className="grid min-h-[420px] grid-cols-[280px_minmax(0,1fr)] gap-6">
      <aside className="min-h-0 border-r border-border/60 pr-4">
        <MasterDetailList<MCPToolEntry>
          items={tools}
          getKey={(tool) => tool.name}
          filter={(tool, q) =>
            tool.name.toLowerCase().includes(q.toLowerCase()) ||
            (tool.description ?? '').toLowerCase().includes(q.toLowerCase())
          }
          selectedKey={selected}
          onSelect={setSelected}
          searchPlaceholder={t('filterPlaceholder')}
          countLabel={(matched, total, q) =>
            q ? t('countMatch', { matched, total }) : t('countAll', { count: total })
          }
          emptyState={t('emptyNoTools')}
          emptyMatchState={(q) => t('emptyMatch', { query: q })}
          query={query}
          onQueryChange={setQuery}
          renderItem={(tool) => (
            <>
              <span className="truncate font-mono text-sm font-semibold">{tool.name}</span>
              {tool.description ? (
                <span className="truncate text-xs text-muted-foreground">{tool.description}</span>
              ) : null}
              <span
                className={
                  citations[tool.name]
                    ? 'text-[10px] font-medium uppercase tracking-wide text-success-fg'
                    : 'text-[10px] uppercase tracking-wide text-muted-foreground'
                }
              >
                {citations[tool.name] ? t('rowMapped') : t('rowUnmapped')}
              </span>
            </>
          )}
        />
      </aside>
      <section className="min-h-0">
        {selectedTool ? (
          <>
            <MCPCitationEditor
              toolName={selectedTool.name}
              outputFieldCandidates={extractOutputFieldCandidates(selectedTool)}
              value={citations[selectedTool.name] ?? null}
              onChange={(next) => void handleChange(next)}
              readOnly={saving}
            />
            {error ? <p className="mt-2 text-xs text-destructive">{error}</p> : null}
          </>
        ) : null}
      </section>
    </div>
  )
}
