'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  adminCatalogDeleteInstall,
  createApiClient,
  useWorkspaceStore,
  wsListEffectiveConnectors,
  wsListTemplates,
  type MCPConnectorFilter,
  type MCPConnectorTemplate,
  type MCPEffectiveConnector,
} from '@cubebox/core'
import { MCPToolbar } from '@/components/mcp/MCPToolbar'
import { MCPConnectorList } from '@/components/mcp/MCPConnectorList'
import { MCPAdminDetailPanel } from '@/components/mcp/MCPAdminDetailPanel'

export default function AdminMcpPage() {
  const t = useTranslations('mcpAdmin')
  const client = useMemo(() => createApiClient(''), [])

  const workspaces = useWorkspaceStore((s) => s.workspaces)
  const fetchWorkspaceList = useWorkspaceStore((s) => s.fetchList)

  const [connectors, setConnectors] = useState<MCPEffectiveConnector[]>([])
  const [templates, setTemplates] = useState<MCPConnectorTemplate[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<MCPConnectorFilter>('all')
  const [mode, setMode] = useState<'detail' | 'add_custom' | 'install_template' | null>(null)
  const [installTemplate, setInstallTemplate] = useState<MCPConnectorTemplate | null>(null)

  const lensWsId = workspaces[0]?.id ?? ''

  useEffect(() => {
    if (workspaces.length === 0) void fetchWorkspaceList(client)
  }, [client, fetchWorkspaceList, workspaces.length])

  const load = useCallback(async () => {
    if (!lensWsId) return
    setLoading(true)
    try {
      const wsClient = createApiClient('')
      wsClient.setWorkspaceId(lensWsId)
      const [eff, tpl] = await Promise.all([
        wsListEffectiveConnectors(wsClient, lensWsId),
        wsListTemplates(wsClient, lensWsId),
      ])
      setConnectors(eff.items)
      setTemplates(tpl.items)
    } finally {
      setLoading(false)
    }
  }, [lensWsId])

  useEffect(() => {
    void load()
  }, [load])

  const selected = useMemo(
    () => connectors.find((c) => c.install.install_id === selectedId) ?? null,
    [connectors, selectedId],
  )

  function handleSelect(id: string): void {
    setSelectedId(id)
    setInstallTemplate(null)
    setMode('detail')
  }

  function handleAddCustom(): void {
    setSelectedId(null)
    setInstallTemplate(null)
    setMode('add_custom')
  }

  async function handleRefresh(): Promise<void> {
    await load()
  }

  async function handleDelete(installId: string): Promise<void> {
    await adminCatalogDeleteInstall(client, installId)
    await load()
    setSelectedId(null)
    setMode(null)
  }

  function handleInstalled(installId: string): void {
    setInstallTemplate(null)
    setSelectedId(installId)
    setMode('detail')
    void load()
  }

  function handleCreated(_serverId: string): void {
    // Legacy custom-create panel still talks to the legacy MCPServer API; just
    // reload everything and let the new four-layer surface re-show the result
    // if/when it appears as an install.
    void load()
    setMode(null)
  }

  const availableTemplates = useMemo(() => {
    const installedTemplateIds = new Set(
      connectors.map((c) => c.template?.template_id).filter((v): v is string => Boolean(v)),
    )
    return templates.filter((tpl) => !installedTemplateIds.has(tpl.template_id))
  }, [templates, connectors])

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border/70 px-6 py-4">
        <h2 className="text-lg font-semibold tracking-tight">{t('pageTitle')}</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{t('pageSubtitle')}</p>
        <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-muted-foreground">
          <span className="rounded-full bg-muted px-2 py-0.5">{t('orgGrant')}</span>
          <span className="rounded-full bg-muted px-2 py-0.5">{t('workspaceGrant')}</span>
          <span className="rounded-full bg-muted px-2 py-0.5">{t('myGrant')}</span>
        </div>
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
          <div className="border-b border-border/60 px-4 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            {t('installs')}
          </div>
          <MCPConnectorList
            connectors={connectors}
            loading={loading}
            search={search}
            filter={filter}
            selectedId={selectedId}
            onSelect={handleSelect}
          />
          {availableTemplates.length > 0 && (
            <div className="border-t border-border/60">
              <div className="px-4 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                {t('templates')}
              </div>
              <div className="flex flex-col gap-1.5 p-3">
                {availableTemplates.map((tpl) => (
                  <button
                    key={tpl.template_id}
                    type="button"
                    onClick={() => {
                      setSelectedId(null)
                      setInstallTemplate(tpl)
                      setMode('install_template')
                    }}
                    data-testid={`template-row-${tpl.slug}`}
                    className="flex w-full flex-col gap-0.5 rounded-lg border border-border/70 bg-card/40 p-3 text-left hover:border-border hover:bg-accent/40"
                  >
                    <span className="truncate text-sm font-semibold">{tpl.name}</span>
                    {tpl.description && (
                      <span className="line-clamp-1 text-xs text-muted-foreground">
                        {tpl.description}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            </div>
          )}
        </aside>

        <section className="flex flex-1 overflow-y-auto">
          <MCPAdminDetailPanel
            connector={selected}
            mode={mode}
            installTemplate={installTemplate}
            client={client}
            wsId={lensWsId}
            onRefresh={handleRefresh}
            onDelete={handleDelete}
            onInstalled={handleInstalled}
            onCreated={handleCreated}
          />
        </section>
      </div>
    </div>
  )
}
