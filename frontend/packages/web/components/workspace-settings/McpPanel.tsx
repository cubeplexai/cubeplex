'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { ChevronDown, ChevronUp, Loader2, Search } from 'lucide-react'
import {
  ApiError,
  createApiClient,
  useWorkspaceStore,
  wsCreateTemplate,
  wsListCatalog,
  wsPromoteTemplate,
  wsSetTemplateState,
  type ApiClient,
  type MCPAuthMethod,
  type MCPConnector,
  type MCPConnectorTemplate,
  type MCPCredentialScope,
  type MCPEffectiveConnector,
  type MCPTemplate,
  type MCPTemplateScope,
  type WorkspaceCatalogRow,
} from '@cubeplex/core'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { MCPScopeBadge } from '@/components/mcp/MCPScopeBadge'
import { WsAuthBand } from '@/components/mcp/WsAuthBand'
import {
  MCPTemplateCreateForm,
  type CreateTemplateBody,
} from '@/components/mcp/MCPTemplateCreateForm'

interface McpPanelProps {
  wsId: string
}

type WsFilter = 'enabled' | 'disabled' | 'all'

// Synthesize an MCPEffectiveConnector from a WorkspaceCatalogRow for WsAuthBand.
// WsAuthBand's computeAuthBandState needs usable, reason, credential_availability_by_scope, etc.
function toEffectiveConnector(
  row: WorkspaceCatalogRow,
  wsId: string,
): MCPEffectiveConnector | null {
  const { connector, template } = row
  if (!connector) return null

  const policy = (connector.default_credential_policy ?? 'none') as MCPCredentialScope
  // Use the auth method recorded on the org grant when one exists.
  // When no grant exists yet, leave 'none' and let WsAuthBand decide from supported_auth_methods.
  const authMethod = (connector.org_grant_auth_method ?? 'none') as MCPAuthMethod

  // Build a synthetic MCPConnector (install shape) for the band.
  const syntheticInstall: MCPConnector = {
    connector_id: connector.connector_id,
    template_id: template.template_id,
    install_scope: 'org',
    workspace_id: wsId,
    name: template.name,
    server_url: template.server_url,
    transport: template.transport as MCPConnector['transport'],
    auth_method: authMethod,
    default_credential_policy: policy,
    auth_status: row.usable ? 'authorized' : 'pending',
    discovery_status: connector.discovery_status,
    install_state: 'active',
    tool_count: connector.tool_count,
    tools: connector.tools,
    tool_citations: connector.tool_citations as unknown as MCPConnector['tool_citations'],
    last_error: connector.last_error,
    auto_enroll_new_workspaces: connector.auto_enroll_new_workspaces,
  }

  const syntheticTemplate: MCPConnectorTemplate = {
    template_id: template.template_id,
    slug: template.slug,
    name: template.name,
    provider: template.provider,
    description: template.description,
    server_url: template.server_url,
    transport: template.transport as MCPConnectorTemplate['transport'],
    supported_auth_methods: template.supported_auth_methods as MCPAuthMethod[],
    default_credential_policy: policy,
    static_form_schema: null,
    status: 'active',
  }

  // Determine credential source from availability.
  const avail = row.credential_availability_by_scope
  const credSource: MCPEffectiveConnector['credential_source'] = avail.org
    ? 'org'
    : avail.workspace
      ? 'workspace'
      : avail.user
        ? 'user'
        : null

  const credAvailability: MCPEffectiveConnector['credential_availability'] =
    policy === 'none' ? 'not_required' : credSource !== null ? 'available' : 'missing'

  return {
    template: syntheticTemplate,
    install: syntheticInstall,
    workspace_state: {
      workspace_id: wsId,
      connector_id: connector.connector_id,
      enabled: row.enabled,
      credential_policy: policy,
    },
    credential_policy: policy,
    required_grant_scope: policy === 'none' ? null : policy,
    credential_availability: credAvailability,
    credential_source: credSource,
    credential_availability_by_scope: row.credential_availability_by_scope,
    usable: row.usable ?? false,
    reason: row.reason ?? '',
  }
}

// Individual row in the workspace catalog list.
function WsCatalogRow({
  row,
  wsId,
  isAdmin,
  client,
  onRefresh,
}: {
  row: WorkspaceCatalogRow
  wsId: string
  isAdmin: boolean
  client: ApiClient
  onRefresh: () => Promise<void>
}) {
  const t = useTranslations('mcp')
  const [toggling, setToggling] = useState(false)
  const [promoting, setPromoting] = useState(false)
  const [expanded, setExpanded] = useState(false)

  const { template, connector, enabled } = row
  const isOwnWorkspaceTemplate =
    template.scope === 'workspace' && template.workspace_id === wsId && isAdmin
  const effectiveConnector = useMemo(() => toEffectiveConnector(row, wsId), [row, wsId])

  async function handleToggle(): Promise<void> {
    setToggling(true)
    try {
      await wsSetTemplateState(client, wsId, template.template_id, { enabled: !enabled })
      await onRefresh()
    } catch (err) {
      if (err instanceof ApiError && err.code === 'template_disabled_in_org') {
        toast.error(t('toastDisabledInOrg'))
      } else if (
        err instanceof ApiError &&
        (err.code === 'template_not_visible' || err.status === 404)
      ) {
        toast.error(t('toastNotVisible'))
        await onRefresh()
      } else {
        toast.error((err as Error).message)
      }
    } finally {
      setToggling(false)
    }
  }

  async function handlePromote(): Promise<void> {
    setPromoting(true)
    try {
      await wsPromoteTemplate(client, wsId, template.template_id)
      await onRefresh()
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setPromoting(false)
    }
  }

  return (
    <div
      className={cn(
        'rounded-lg border transition-all',
        enabled ? 'border-border/70 bg-card/60' : 'border-border/40 bg-muted/20 opacity-70',
      )}
      data-testid={`ws-catalog-row-${template.template_id}`}
    >
      <div className="flex items-center gap-3 px-4 py-3">
        {/* Enable toggle */}
        <label className="relative inline-flex shrink-0 cursor-pointer items-center">
          <input
            type="checkbox"
            className="sr-only"
            checked={enabled}
            disabled={toggling}
            onChange={() => void handleToggle()}
            aria-label={`${enabled ? t('filterDisabled') : t('filterEnabled')} ${template.name}`}
            data-testid={`ws-catalog-toggle-${template.template_id}`}
          />
          <div
            className={cn(
              'h-5 w-9 rounded-full transition-colors',
              enabled ? 'bg-primary' : 'bg-muted-foreground/30',
              toggling && 'cursor-not-allowed opacity-50',
            )}
          >
            <div
              className={cn(
                'mt-0.5 ml-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform',
                enabled ? 'translate-x-4' : 'translate-x-0',
              )}
            />
          </div>
          {toggling ? (
            <Loader2 className="absolute inset-0 m-auto size-3 animate-spin text-primary" />
          ) : null}
        </label>

        {/* Name + badges */}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="truncate text-sm font-semibold">{template.name}</span>
            <MCPScopeBadge scope={template.scope as MCPTemplateScope} />
          </div>
          {template.description ? (
            <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
              {template.description}
            </p>
          ) : null}
        </div>

        {/* Actions */}
        <div className="flex shrink-0 items-center gap-1">
          {isOwnWorkspaceTemplate ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={promoting}
              onClick={() => void handlePromote()}
              data-testid={`ws-catalog-promote-${template.template_id}`}
            >
              {promoting ? <Loader2 data-icon="inline-start" className="animate-spin" /> : null}
              {t('promoteTemplateAction')}
            </Button>
          ) : null}

          {/* Expand/collapse credential band */}
          {connector && enabled ? (
            <button
              type="button"
              aria-label={expanded ? 'collapse' : 'expand'}
              onClick={() => setExpanded((v) => !v)}
              className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              {expanded ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
            </button>
          ) : null}
        </div>
      </div>

      {/* Credential band — only shown when enabled and expanded */}
      {connector && enabled && expanded && effectiveConnector ? (
        <div className="border-t border-border/50 px-4 py-3">
          <WsAuthBand
            connector={effectiveConnector}
            client={client}
            wsId={wsId}
            callerRole={isAdmin ? 'admin' : 'member'}
            onChanged={onRefresh}
          />
        </div>
      ) : null}
    </div>
  )
}

export function McpPanel({ wsId }: McpPanelProps) {
  const t = useTranslations('mcp')
  const client = useMemo(() => createApiClient(''), [])
  const wsRole = useWorkspaceStore((s) => s.workspaces.find((w) => w.id === wsId)?.role)
  const isAdmin = wsRole === 'admin'

  const [rows, setRows] = useState<WorkspaceCatalogRow[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<WsFilter>('all')
  const [source, setSource] = useState<MCPTemplateScope | 'all'>('all')
  const [showCustomForm, setShowCustomForm] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const catalog = await wsListCatalog(client, wsId)
      setRows(catalog.items)
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setLoading(false)
    }
  }, [client, wsId])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])

  const visible = useMemo(
    () =>
      rows.filter((r) => {
        if (search && !r.template.name.toLowerCase().includes(search.toLowerCase())) return false
        if (source !== 'all' && r.template.scope !== source) return false
        switch (filter) {
          case 'enabled':
            return r.enabled
          case 'disabled':
            return !r.enabled
          default:
            return true
        }
      }),
    [rows, search, filter, source],
  )

  function handleCreated(template: MCPTemplate): void {
    void load()
    setShowCustomForm(false)
    toast.success(t('toastAdded', { name: template.name }))
  }

  if (showCustomForm) {
    return (
      <div className="flex h-full flex-col">
        <header className="flex items-center gap-3 border-b border-border/70 px-6 py-4">
          <button
            type="button"
            onClick={() => setShowCustomForm(false)}
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            ← {t('wsTitle')}
          </button>
        </header>
        <div className="flex-1 overflow-y-auto">
          <WsCreateForm wsId={wsId} onCreated={handleCreated} />
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border/70 px-6 py-4">
        <h2 className="text-lg font-semibold tracking-tight">{t('wsTitle')}</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{t('wsSubtitle')}</p>
      </header>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border/70 px-4 py-3">
        <div className="relative min-w-[180px] flex-1">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/70" />
          <Input
            type="search"
            placeholder={t('searchPlaceholder')}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-7"
            aria-label={t('searchAriaLabel')}
          />
        </div>

        <FilterPills filter={filter} onFilterChange={setFilter} t={t} />
        <SourcePills source={source} onSourceChange={setSource} t={t} />
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto px-4 py-3">
        {loading ? (
          <div className="flex items-center justify-center py-12 text-sm text-muted-foreground">
            {t('loading')}
          </div>
        ) : visible.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-1 py-12 text-center">
            <p className="text-sm font-medium text-foreground">{t('noConnectors')}</p>
            <p className="text-xs text-muted-foreground">{t('noConnectorsHint')}</p>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {visible.map((row) => (
              <WsCatalogRow
                key={row.template.template_id}
                row={row}
                wsId={wsId}
                isAdmin={isAdmin}
                client={client}
                onRefresh={load}
              />
            ))}
          </div>
        )}
      </div>

      {/* Footer action — add custom connector (admins only) */}
      {isAdmin ? (
        <div className="border-t border-border/70 px-4 py-3">
          <button
            type="button"
            onClick={() => setShowCustomForm(true)}
            data-testid="ws-add-custom-connector"
            className="flex w-full items-center gap-2 rounded-lg border border-dashed border-border/70 bg-card/40 p-2.5 text-left text-sm font-medium hover:border-border hover:bg-accent/40"
          >
            <span aria-hidden>+</span>
            {t('registerCustomTemplate')}
          </button>
        </div>
      ) : null}
    </div>
  )
}

// Filter pills for enabled/disabled/all.
function FilterPills({
  filter,
  onFilterChange,
  t,
}: {
  filter: WsFilter
  onFilterChange: (v: WsFilter) => void
  t: ReturnType<typeof useTranslations<'mcp'>>
}) {
  const options: { value: WsFilter; label: string }[] = [
    { value: 'enabled', label: t('filterEnabled') },
    { value: 'disabled', label: t('filterDisabled') },
    { value: 'all', label: t('filterAll') },
  ]
  return (
    <div
      role="group"
      aria-label={t('filterByStatus')}
      className="inline-flex items-center gap-0.5 rounded-lg border border-border bg-muted/30 p-0.5"
    >
      {options.map((opt) => {
        const active = opt.value === filter
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onFilterChange(opt.value)}
            className={cn(
              'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
              active
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}

// Source filter pills.
function SourcePills({
  source,
  onSourceChange,
  t,
}: {
  source: MCPTemplateScope | 'all'
  onSourceChange: (v: MCPTemplateScope | 'all') => void
  t: ReturnType<typeof useTranslations<'mcp'>>
}) {
  const options: { value: MCPTemplateScope | 'all'; label: string }[] = [
    { value: 'all', label: t('sourceAll') },
    { value: 'global', label: t('sourceGlobal') },
    { value: 'org', label: t('sourceOrg') },
    { value: 'workspace', label: t('sourceWorkspace') },
  ]
  return (
    <div
      role="group"
      aria-label={t('filterBySource')}
      className="inline-flex items-center gap-0.5 rounded-lg border border-border bg-muted/30 p-0.5"
    >
      {options.map((opt) => {
        const active = opt.value === source
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onSourceChange(opt.value)}
            className={cn(
              'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
              active
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}

// Workspace-scoped template create form — wraps MCPTemplateCreateForm with wsCreateTemplate.
function WsCreateForm({ wsId, onCreated }: { wsId: string; onCreated: (t: MCPTemplate) => void }) {
  const client = useMemo(() => createApiClient(''), [])

  const handleCreateTemplate = useCallback(
    async (body: CreateTemplateBody): Promise<MCPTemplate> => {
      return wsCreateTemplate(client, wsId, body)
    },
    [client, wsId],
  )

  return (
    <MCPTemplateCreateForm
      client={client}
      onCreated={onCreated}
      variant="workspace"
      onSubmit={handleCreateTemplate}
    />
  )
}
