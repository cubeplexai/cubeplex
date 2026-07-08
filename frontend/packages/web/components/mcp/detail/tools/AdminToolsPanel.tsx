'use client'

import type { ApiClient, MCPToolEntry } from '@cubebox/core'
import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'

import { AdminTryItView } from './AdminTryItView'
import { ToolDetail, type ToolDetailView } from './ToolDetail'
import { ToolList } from './ToolList'

export interface AdminToolsPanelProps {
  tools: MCPToolEntry[]
  connectorId: string
  client: ApiClient
  /** Panel lens workspace id; null when the install has no state row
   * in the current lens. */
  wsId: string | null
  adminWorkspaceOptions?: Array<{ id: string; name: string }>
  scopedAdminWorkspaceId?: string | null
  onScopedWorkspaceChange?: (wsId: string) => void
  requiresWorkspacePicker?: boolean
  adminAuthMethod?: 'oauth' | 'static' | 'none'
}

export function AdminToolsPanel({
  tools,
  connectorId,
  client,
  wsId,
  adminWorkspaceOptions,
  scopedAdminWorkspaceId,
  onScopedWorkspaceChange,
  requiresWorkspacePicker,
  adminAuthMethod,
}: AdminToolsPanelProps) {
  const t = useTranslations('mcp.tools')
  const [selectedName, setSelectedName] = useState<string | null>(
    tools.length > 0 ? tools[0].name : null,
  )
  const [view, setView] = useState<ToolDetailView>('schema')
  const [query, setQuery] = useState('')

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return tools
    return tools.filter(
      (tool) =>
        tool.name.toLowerCase().includes(q) || (tool.description ?? '').toLowerCase().includes(q),
    )
  }, [tools, query])

  const effectiveSelected: string | null =
    selectedName && filtered.some((tool) => tool.name === selectedName)
      ? selectedName
      : filtered.length > 0
        ? filtered[0].name
        : null

  const selected = tools.find((tool) => tool.name === effectiveSelected) ?? null

  if (tools.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border px-6 py-12 text-center text-sm text-muted-foreground">
        {t('empty')}
      </div>
    )
  }

  return (
    <div className="grid min-h-[420px] grid-cols-[280px_minmax(0,1fr)] gap-6">
      <aside className="min-h-0 border-r border-border/60 pr-4">
        <ToolList
          tools={tools}
          filtered={filtered}
          query={query}
          onQueryChange={setQuery}
          selectedName={effectiveSelected}
          onSelect={setSelectedName}
        />
      </aside>
      <section className="min-h-0">
        {selected ? (
          <ToolDetail
            tool={selected}
            view={view}
            onViewChange={setView}
            tryItSlot={
              <AdminTryItView
                key={selected.name}
                connectorId={connectorId}
                toolName={selected.name}
                inputSchema={(selected.input_schema as Record<string, unknown> | null) ?? null}
                client={client}
                wsId={wsId}
                adminWorkspaceOptions={adminWorkspaceOptions}
                scopedAdminWorkspaceId={scopedAdminWorkspaceId}
                onScopedWorkspaceChange={onScopedWorkspaceChange}
                requiresWorkspacePicker={requiresWorkspacePicker}
                adminAuthMethod={adminAuthMethod}
              />
            }
          />
        ) : null}
      </section>
    </div>
  )
}
