'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  createApiClient,
  useMcpStore,
  useWorkspaceStore,
  type MCPConnectorFilter,
} from '@cubebox/core'
import { MCPToolbar } from '@/components/mcp/MCPToolbar'
import { MCPConnectorList } from '@/components/mcp/MCPConnectorList'
import { MCPAdminDetailPanel } from '@/components/mcp/MCPAdminDetailPanel'

export default function AdminMcpPage() {
  const t = useTranslations('mcpAdmin')
  const client = useMemo(() => createApiClient(''), [])

  const connectors = useMcpStore((s) => s.connectors)
  const loading = useMcpStore((s) => s.loading)
  const selectedId = useMcpStore((s) => s.selectedId)
  const setSelectedId = useMcpStore((s) => s.setSelectedId)
  const fetchAll = useMcpStore((s) => s.fetchAll)
  const refreshTools = useMcpStore((s) => s.refreshTools)
  const deleteServer = useMcpStore((s) => s.deleteServer)

  const workspaces = useWorkspaceStore((s) => s.workspaces)
  const fetchWorkspaceList = useWorkspaceStore((s) => s.fetchList)

  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<MCPConnectorFilter>('all')
  const [mode, setMode] = useState<'detail' | 'add_custom' | null>(null)

  const lensWsId = workspaces[0]?.id ?? ''

  useEffect(() => {
    if (workspaces.length === 0) void fetchWorkspaceList(client)
  }, [client, fetchWorkspaceList, workspaces.length])

  useEffect(() => {
    if (lensWsId) void fetchAll(client, lensWsId)
  }, [client, fetchAll, lensWsId])

  const selected = useMemo(
    () => connectors.find((c) => c.id === selectedId) ?? null,
    [connectors, selectedId],
  )

  function handleSelect(id: string): void {
    setSelectedId(id)
    setMode('detail')
  }

  function handleAddCustom(): void {
    setSelectedId(null)
    setMode('add_custom')
  }

  async function handleRefresh(id: string): Promise<void> {
    await refreshTools(client, id)
  }

  async function handleDelete(id: string): Promise<void> {
    await deleteServer(client, id, lensWsId)
  }

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border/70 px-6 py-4">
        <h2 className="text-lg font-semibold tracking-tight">{t('pageTitle')}</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{t('pageSubtitle')}</p>
      </header>

      <MCPToolbar
        search={search}
        onSearchChange={setSearch}
        filter={filter}
        onFilterChange={setFilter}
        onAddCustom={handleAddCustom}
      />

      <div className="flex flex-1 overflow-hidden">
        <aside
          aria-label="connector-list"
          className="w-[360px] shrink-0 overflow-y-auto border-r border-border/70 bg-card/20"
        >
          <MCPConnectorList
            connectors={connectors}
            loading={loading}
            search={search}
            filter={filter}
            selectedId={selectedId}
            onSelect={handleSelect}
          />
        </aside>

        <section className="flex flex-1 overflow-y-auto">
          <MCPAdminDetailPanel
            connector={selected}
            mode={mode}
            client={client}
            onRefresh={handleRefresh}
            onDelete={handleDelete}
          />
        </section>
      </div>
    </div>
  )
}
