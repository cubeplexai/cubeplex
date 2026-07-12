'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  adminListCatalog,
  createApiClient,
  useWorkspaceStore,
  type AdminCatalogFilter,
  type AdminCatalogRow,
  type MCPTemplateScope,
} from '@cubeplex/core'
import { MCPToolbar } from '@/components/mcp/MCPToolbar'
import { MCPCatalogList } from '@/components/mcp/MCPCatalogList'
import { MCPAdminDetailPanel } from '@/components/mcp/MCPAdminDetailPanel'
import { MCPTemplateCreateForm } from '@/components/mcp/MCPTemplateCreateForm'
import { ListDetailLayout } from '@/components/shared/ListDetailLayout'

export default function AdminMcpPage() {
  const t = useTranslations('mcpAdmin')
  const client = useMemo(() => createApiClient(''), [])

  const [rows, setRows] = useState<AdminCatalogRow[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<AdminCatalogFilter>('in_use')
  const [source, setSource] = useState<MCPTemplateScope | 'all'>('all')
  const [mode, setMode] = useState<'detail' | 'custom_create' | null>(null)

  const workspaces = useWorkspaceStore((s) => s.workspaces)
  const fetchWorkspaceList = useWorkspaceStore((s) => s.fetchList)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const catalog = await adminListCatalog(client)
      setRows(catalog.items)
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
    () => rows.find((r) => r.template.template_id === selectedTemplateId) ?? null,
    [rows, selectedTemplateId],
  )

  // Client-side filter as per spec.
  const visible = useMemo(
    () =>
      rows.filter((r) => {
        if (search && !r.template.name.toLowerCase().includes(search.toLowerCase())) return false
        if (source !== 'all' && r.template.scope !== source) return false
        switch (filter) {
          case 'in_use':
            return r.in_use
          case 'needs_attention':
            return r.needs_attention
          case 'org_credential':
            return r.org_grant_status !== null
          case 'unused':
            return !r.in_use
          default:
            return true
        }
      }),
    [rows, search, filter, source],
  )

  function handleSelect(templateId: string): void {
    setSelectedTemplateId(templateId)
    setMode('detail')
  }

  async function handleRefresh(): Promise<void> {
    await load()
  }

  function handleDeleted(): void {
    setSelectedTemplateId(null)
    setMode(null)
    void load()
  }

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
        source={source}
        onSourceChange={setSource}
      />

      <ListDetailLayout
        selected={selectedTemplateId !== null || mode === 'custom_create'}
        onBack={() => {
          setSelectedTemplateId(null)
          setMode(null)
        }}
        backLabel={t('back')}
        placeholder={null}
        railClassName="bg-card/20 px-0 py-0"
        list={
          <div aria-label="catalog-list">
            <div className="border-b border-border/60 px-4 py-2">
              <button
                type="button"
                onClick={() => {
                  setSelectedTemplateId(null)
                  setMode('custom_create')
                }}
                data-testid="mcp-add-custom-connector"
                className="flex w-full items-center gap-2 rounded-lg border border-dashed border-border/70 bg-card/40 p-2.5 text-left text-sm font-medium hover:border-border hover:bg-accent/40"
              >
                <span aria-hidden>+</span>
                {t('addCustomConnector')}
              </button>
            </div>
            <MCPCatalogList
              rows={visible}
              loading={loading}
              selectedTemplateId={selectedTemplateId}
              onSelect={handleSelect}
            />
          </div>
        }
        detail={
          mode === 'custom_create' ? (
            <MCPTemplateCreateForm
              client={client}
              onCreated={(template) => {
                void load()
                setSelectedTemplateId(template.template_id)
                setMode('detail')
              }}
            />
          ) : (
            <MCPAdminDetailPanel
              row={selected}
              mode={mode}
              client={client}
              onRefresh={handleRefresh}
              onDeleted={handleDeleted}
            />
          )
        }
      />
    </div>
  )
}
