'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  adminDeleteInstall,
  adminListConnectors,
  adminListTemplates,
  createApiClient,
  useWorkspaceStore,
  type AdminOrgConnector,
  type MCPConnectorFilter,
  type MCPConnectorTemplate,
} from '@cubebox/core'
import { MCPToolbar } from '@/components/mcp/MCPToolbar'
import { MCPConnectorList } from '@/components/mcp/MCPConnectorList'
import { MCPAdminDetailPanel } from '@/components/mcp/MCPAdminDetailPanel'
import { ListDetailLayout } from '@/components/shared/ListDetailLayout'

export default function AdminMcpPage() {
  const t = useTranslations('mcpAdmin')
  const client = useMemo(() => createApiClient(''), [])

  const [connectors, setConnectors] = useState<AdminOrgConnector[]>([])
  const [templates, setTemplates] = useState<MCPConnectorTemplate[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<MCPConnectorFilter>('all')
  const [mode, setMode] = useState<'detail' | 'install_template' | 'custom_install' | null>(null)
  const [installTemplate, setInstallTemplate] = useState<MCPConnectorTemplate | null>(null)

  // The admin layout doesn't populate the workspace store; the detail
  // panel's Try It workspace picker (for workspace/user policy installs)
  // reads from useWorkspaceStore, so without this fetch the picker is
  // empty and Run stays disabled. Page no longer uses workspace ids for
  // its own list (no lens), but downstream consumers still do.
  const workspaces = useWorkspaceStore((s) => s.workspaces)
  const fetchWorkspaceList = useWorkspaceStore((s) => s.fetchList)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [conn, tpl] = await Promise.all([
        adminListConnectors(client),
        adminListTemplates(client),
      ])
      setConnectors(conn.items)
      setTemplates(tpl.items)
    } finally {
      setLoading(false)
    }
  }, [client])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
    if (workspaces.length === 0) void fetchWorkspaceList(client)
  }, [load, client, workspaces.length, fetchWorkspaceList])

  const selected = useMemo(
    () => connectors.find((c) => c.install.connector_id === selectedId) ?? null,
    [connectors, selectedId],
  )

  function handleSelect(id: string): void {
    setSelectedId(id)
    setInstallTemplate(null)
    setMode('detail')
  }

  async function handleRefresh(): Promise<void> {
    await load()
  }

  async function handleDelete(connectorId: string): Promise<void> {
    await adminDeleteInstall(client, connectorId)
    await load()
    setSelectedId(null)
    setMode(null)
  }

  function handleInstalled(connectorId: string): void {
    setInstallTemplate(null)
    setSelectedId(connectorId)
    setMode('detail')
    void load()
  }

  const availableTemplates = useMemo(() => {
    return templates
  }, [templates])

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
      />

      <ListDetailLayout
        selected={selectedId !== null || mode === 'custom_install' || mode === 'install_template'}
        onBack={() => {
          setSelectedId(null)
          setMode(null)
          setInstallTemplate(null)
        }}
        backLabel={t('back')}
        placeholder={null}
        railClassName="bg-card/20 px-0 py-0"
        list={
          <div aria-label="connector-list">
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
            <div className="border-t border-border/60">
              <div className="px-4 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                {t('templates')}
              </div>
              <div className="flex flex-col gap-1.5 p-3">
                <button
                  type="button"
                  onClick={() => {
                    setSelectedId(null)
                    setInstallTemplate(null)
                    setMode('custom_install')
                  }}
                  data-testid="mcp-add-custom-connector"
                  className="flex w-full items-center gap-2 rounded-lg border border-dashed border-border/70 bg-card/40 p-3 text-left text-sm font-medium hover:border-border hover:bg-accent/40"
                >
                  <span aria-hidden>+</span>
                  {t('addCustomConnector')}
                </button>
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
          </div>
        }
        detail={
          <MCPAdminDetailPanel
            connector={selected}
            mode={mode}
            installTemplate={installTemplate}
            client={client}
            onRefresh={handleRefresh}
            onDelete={handleDelete}
            onInstalled={handleInstalled}
          />
        }
      />
    </div>
  )
}
