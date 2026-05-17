'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  adminDeleteInstall,
  adminListInstalls,
  createApiClient,
  useWorkspaceStore,
  wsListEffectiveConnectors,
  wsListTemplates,
  type MCPConnectorFilter,
  type MCPConnectorInstall,
  type MCPConnectorTemplate,
  type MCPEffectiveConnector,
} from '@cubebox/core'
import { MCPToolbar } from '@/components/mcp/MCPToolbar'
import { MCPConnectorList } from '@/components/mcp/MCPConnectorList'
import { MCPAdminDetailPanel } from '@/components/mcp/MCPAdminDetailPanel'

function synthesizeStubEffective(
  install: MCPConnectorInstall,
  workspaceId: string,
): MCPEffectiveConnector {
  // Mirror the backend "no workspace_state row" semantics: the install
  // exists, but has not been enabled in the lens workspace. The admin
  // page still wants to render it so org-scope installs (especially
  // ``auto_enable.mode='none'`` ones) remain visible / manageable.
  const credentialAvailability = install.auth_method === 'none' ? 'not_required' : 'missing'
  return {
    template: null,
    install,
    // Keep workspace_state = null for synthesized rows. A non-null
    // value tells MCPAdminDetailPanel "this install has a real
    // workspace state row in this lens" — which Try It then uses
    // to send the wsId lens to the admin invoke route. The backend
    // workspace-effective service filters org installs without a
    // real state row, so the synthesized lens would 400 with
    // connector_not_usable. We only synthesize this entry so the
    // admin list shows the install at all; the panel must NOT
    // treat it as workspace-enabled.
    workspace_state: null,
    credential_policy: install.default_credential_policy,
    credential_availability: credentialAvailability,
    credential_source: null,
    usable: false,
    reason: 'not_enabled_in_workspace',
  }
}

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
  const [mode, setMode] = useState<'detail' | 'install_template' | 'custom_install' | null>(null)
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
      // Admin install list (``/admin/mcp/installs`` → ``list_org_installs``)
      // is the source of truth for org-scope rows in the left rail —
      // wsListEffectiveConnectors only surfaces org installs with a
      // workspace_state row, so org installs with auto_enable.mode='none'
      // or scoped to a sibling workspace would otherwise be hidden from
      // the admin. The effective list still feeds per-workspace state
      // (enabled flag, credential availability) for the lens workspace.
      //
      // However ``list_org_installs`` omits workspace-scope installs
      // entirely, so a connector freshly created via
      // ``MCPTemplateInstallPanel`` (which posts ``install_scope:'workspace'``)
      // would vanish from the admin page right after creation. To cover
      // those, also append any workspace-scope effective rows that the
      // admin endpoint omits.
      const [adminInstalls, eff, tpl] = await Promise.all([
        adminListInstalls(client),
        wsListEffectiveConnectors(wsClient, lensWsId),
        wsListTemplates(wsClient, lensWsId),
      ])
      const effByInstallId = new Map(eff.items.map((c) => [c.install.install_id, c]))
      const merged: MCPEffectiveConnector[] = []
      const seen = new Set<string>()
      for (const install of adminInstalls.items) {
        if (seen.has(install.install_id)) continue
        seen.add(install.install_id)
        const existing = effByInstallId.get(install.install_id)
        if (existing) {
          merged.push(existing)
        } else {
          // Synthesize a stub effective row so the admin can still see
          // and manage org installs that have no workspace_state row in
          // the lens workspace. Enabled=false reflects the fact that the
          // install is not active in this workspace.
          merged.push(synthesizeStubEffective(install, lensWsId))
        }
      }
      for (const effRow of eff.items) {
        if (seen.has(effRow.install.install_id)) continue
        if (effRow.install.install_scope !== 'workspace') continue
        seen.add(effRow.install.install_id)
        merged.push(effRow)
      }
      setConnectors(merged)
      setTemplates(tpl.items)
    } finally {
      setLoading(false)
    }
  }, [client, lensWsId])

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

  async function handleRefresh(): Promise<void> {
    await load()
  }

  async function handleDelete(installId: string): Promise<void> {
    await adminDeleteInstall(client, installId)
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

  const availableTemplates = useMemo(() => {
    // Prefer ``install.template_id`` because synthesized stubs (admin
    // installs without a workspace effective row) don't carry the
    // hydrated ``template`` payload but still know the template id.
    //
    // Only ACTIVE installs mask their template from the install dialog —
    // tombstoned (uninstalled) rows are kept around so a reinstall can
    // re-attach the same shape (see ``MCPConnectorInstallService.uninstall``),
    // but they must NOT block the admin from re-launching the install
    // flow for the same template. Without this filter, "uninstall" is a
    // one-shot operation per template.
    const installedTemplateIds = new Set(
      connectors
        .filter((c) => c.install.install_state === 'active')
        .map((c) => c.template?.template_id ?? c.install.template_id)
        .filter((v): v is string => Boolean(v)),
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
          />
        </section>
      </div>
    </div>
  )
}
