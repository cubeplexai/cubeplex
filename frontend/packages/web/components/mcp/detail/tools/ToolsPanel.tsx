'use client'

import type { ApiClient, MCPToolEntry } from '@cubebox/core'
import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'

import { ToolDetail, type ToolDetailView } from './ToolDetail'
import { ToolList } from './ToolList'

export interface ToolsPanelProps {
  tools: MCPToolEntry[]
  installId: string
  client: ApiClient
  surface: 'admin' | 'ws'
  wsId: string | null
  adminWorkspaceOptions?: Array<{ id: string; name: string }>
  scopedAdminWorkspaceId?: string | null
  onScopedWorkspaceChange?: (wsId: string) => void
  requiresWorkspacePicker?: boolean
  adminAuthMethod?: 'oauth' | 'static' | 'none'
}

export function ToolsPanel({
  tools,
  installId,
  client,
  surface,
  wsId,
  adminWorkspaceOptions,
  scopedAdminWorkspaceId,
  onScopedWorkspaceChange,
  requiresWorkspacePicker,
  adminAuthMethod,
}: ToolsPanelProps) {
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
    selectedName && filtered.some((tool: MCPToolEntry) => tool.name === selectedName)
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
            installId={installId}
            client={client}
            surface={surface}
            wsId={wsId}
            adminWorkspaceOptions={adminWorkspaceOptions}
            scopedAdminWorkspaceId={scopedAdminWorkspaceId}
            onScopedWorkspaceChange={onScopedWorkspaceChange}
            requiresWorkspacePicker={requiresWorkspacePicker}
            adminAuthMethod={adminAuthMethod}
          />
        ) : null}
      </section>
    </div>
  )
}
