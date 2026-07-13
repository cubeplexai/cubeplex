'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  AlertTriangle,
  ArrowUpCircle,
  Check,
  CheckCircle2,
  Loader2,
  PauseCircle,
  Pencil,
  Plus,
  Search,
  Trash2,
  Wrench,
  X,
} from 'lucide-react'
import {
  ApiError,
  createApiClient,
  useWorkspaceStore,
  wsCreateTemplate,
  wsDeleteTemplate,
  wsListCatalog,
  wsPromoteTemplate,
  wsRefreshDiscovery,
  wsSetTemplateState,
  wsUpdateTemplate,
  type ApiClient,
  type MCPAuthMethod,
  type MCPConnector,
  type MCPConnectorTemplate,
  type MCPCredentialScope,
  type MCPEffectiveConnector,
  type MCPTemplate,
  type MCPTemplateScope,
  type UpdateTemplateBody,
  type WorkspaceCatalogRow,
} from '@cubeplex/core'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ListDetailLayout } from '@/components/shared/ListDetailLayout'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'
import { ConnectorLogo } from '@/components/mcp/ConnectorLogo'
import { MCPScopeBadge } from '@/components/mcp/MCPScopeBadge'
import { ServerErrorBanner } from '@/components/mcp/detail/ServerErrorBanner'
import { WsToolsPanel } from '@/components/mcp/detail/tools/WsToolsPanel'
import { WsAuthBand } from '@/components/mcp/WsAuthBand'
import {
  MCPTemplateCreateForm,
  type CreateTemplateBody,
} from '@/components/mcp/MCPTemplateCreateForm'

interface McpPanelProps {
  wsId: string
}

type WsFilter = 'enabled' | 'disabled' | 'all'
type RowStatus = 'ready' | 'needsCredential' | 'pendingOAuth' | 'workspaceDisabled' | 'notInstalled'

function statusOf(row: WorkspaceCatalogRow): RowStatus {
  if (!row.connector) return 'notInstalled'
  if (!row.enabled) return 'workspaceDisabled'
  if (row.reason === 'pending_oauth') return 'pendingOAuth'
  const anyCred = Object.values(row.credential_availability_by_scope).some(Boolean)
  const policy = row.connector.default_credential_policy
  if (policy !== 'none' && !anyCred) return 'needsCredential'
  return 'ready'
}

function StatusPill({ status }: { status: RowStatus }) {
  const t = useTranslations('mcpAdmin')
  if (status === 'ready') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-success-solid/10 px-1.5 py-0.5 text-[10px] font-medium text-success-fg">
        <CheckCircle2 className="size-3" />
        {t('ready')}
      </span>
    )
  }
  if (status === 'needsCredential') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-warning-solid/10 px-1.5 py-0.5 text-[10px] font-medium text-warning-fg">
        <AlertTriangle className="size-3" />
        {t('needsCredential')}
      </span>
    )
  }
  if (status === 'pendingOAuth') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-warning-solid/10 px-1.5 py-0.5 text-[10px] font-medium text-warning-fg">
        <AlertTriangle className="size-3" />
        {t('statusPendingOAuth')}
      </span>
    )
  }
  if (status === 'workspaceDisabled') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
        <PauseCircle className="size-3" />
        {t('statusWorkspaceDisabled')}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
      <PauseCircle className="size-3" />
      {t('statusUninstalled')}
    </span>
  )
}

type PolicyLabelKey = 'policyOrg' | 'policyWorkspace' | 'policyUser' | 'policyNone'
type PolicyDescriptionKey =
  | 'policyOrgDescription'
  | 'policyWorkspaceDescription'
  | 'policyUserDescription'
  | 'policyNoneDescription'

function policyLabelKey(policy: MCPCredentialScope): PolicyLabelKey {
  if (policy === 'org') return 'policyOrg'
  if (policy === 'workspace') return 'policyWorkspace'
  if (policy === 'user') return 'policyUser'
  return 'policyNone'
}

function policyDescriptionKey(policy: MCPCredentialScope): PolicyDescriptionKey {
  if (policy === 'org') return 'policyOrgDescription'
  if (policy === 'workspace') return 'policyWorkspaceDescription'
  if (policy === 'user') return 'policyUserDescription'
  return 'policyNoneDescription'
}

// Synthesize an MCPEffectiveConnector from a WorkspaceCatalogRow for WsAuthBand.
function toEffectiveConnector(
  row: WorkspaceCatalogRow,
  wsId: string,
): MCPEffectiveConnector | null {
  const { connector, template } = row
  if (!connector) return null

  // Prefer the workspace-level override; fall back to the connector's default
  // when the workspace has never enabled this template.
  const policy = (row.credential_policy ??
    connector.default_credential_policy ??
    'none') as MCPCredentialScope
  // When a grant exists, use the method the grant was minted with. When no
  // grant exists yet, fall back to the template's single supported method
  // (the multi-method case is handled upstream by WsNoGrantMultiMethod).
  // Without this fallback, a workspace enabling e.g. an OAuth-only template
  // before any org grant is minted would see the static-token form because
  // org_grant_auth_method is null.
  const singleSupportedMethod = (template.supported_auth_methods as MCPAuthMethod[]).find(
    (m) => m !== 'none',
  )
  const authMethod: MCPAuthMethod =
    (connector.org_grant_auth_method as MCPAuthMethod | null) ?? singleSupportedMethod ?? 'none'

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
    server_icons: connector.server_icons,
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
    icon: template.icon ?? null,
  }

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

function WsCatalogRow({
  row,
  active,
  onClick,
}: {
  row: WorkspaceCatalogRow
  active: boolean
  onClick: () => void
}) {
  const { template } = row
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? 'true' : undefined}
      data-testid={`ws-catalog-row-${template.template_id}`}
      className={cn(
        'group flex w-full flex-col gap-1.5 rounded-lg border p-3 text-left transition-all',
        active
          ? 'border-primary/40 bg-primary/5 shadow-sm'
          : 'border-border/70 bg-card/40 hover:border-border hover:bg-accent/40',
      )}
    >
      <div className="flex min-w-0 items-center gap-2">
        <ConnectorLogo
          name={template.name}
          icon={template.icon ?? null}
          serverIcons={row.connector?.server_icons ?? null}
          size="sm"
        />
        <span className="truncate text-sm font-semibold">{template.name}</span>
        {template.provider && template.provider.toLowerCase() !== template.name.toLowerCase() ? (
          <Badge variant="outline" className="shrink-0 text-[10px]">
            {template.provider}
          </Badge>
        ) : null}
        <span className="ml-auto shrink-0">
          <StatusPill status={statusOf(row)} />
        </span>
      </div>
      {template.description ? (
        <p className="line-clamp-1 text-xs text-muted-foreground">{template.description}</p>
      ) : null}
      <div className="flex flex-wrap items-center gap-1 pt-0.5">
        <MCPScopeBadge scope={template.scope as MCPTemplateScope} />
        {row.connector ? (
          <Badge variant="outline" className="px-1.5 text-[10px]">
            {row.connector.default_credential_policy}
          </Badge>
        ) : null}
      </div>
    </button>
  )
}

function WsCatalogDetail({
  row,
  wsId,
  isAdmin,
  client,
  onChanged,
}: {
  row: WorkspaceCatalogRow
  wsId: string
  isAdmin: boolean
  client: ApiClient
  onChanged: () => Promise<void>
}) {
  const t = useTranslations('mcpAdmin')
  const tMcp = useTranslations('mcp')
  const [saving, setSaving] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [promoting, setPromoting] = useState(false)
  const [editing, setEditing] = useState(false)
  const [savingEdit, setSavingEdit] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { template, connector, enabled } = row
  const effective = useMemo(() => toEffectiveConnector(row, wsId), [row, wsId])
  const isWsOwned = template.scope === 'workspace' && template.workspace_id === wsId && isAdmin
  const canPromote = isWsOwned
  const hasConnector = connector !== null

  async function handleToggle(): Promise<void> {
    setSaving(true)
    setError(null)
    try {
      await wsSetTemplateState(client, wsId, template.template_id, { enabled: !enabled })
      await onChanged()
    } catch (err) {
      if (err instanceof ApiError && err.code === 'template_disabled_in_org') {
        toast.error(tMcp('toastDisabledInOrg'))
      } else if (
        err instanceof ApiError &&
        (err.code === 'template_not_visible' || err.status === 404)
      ) {
        toast.error(tMcp('toastNotVisible'))
        await onChanged()
      } else if (err instanceof ApiError && err.code === 'server_url_taken_in_org') {
        const detail = (err.detail ?? {}) as {
          colliding_template_name?: string | null
        }
        const other = detail.colliding_template_name ?? tMcp('toastUrlTakenFallbackName')
        toast.error(tMcp('toastUrlTaken', { other }))
      } else {
        setError((err as Error).message)
      }
    } finally {
      setSaving(false)
    }
  }

  async function handlePolicyChange(next: 'org' | 'workspace' | 'user'): Promise<void> {
    setSaving(true)
    setError(null)
    try {
      await wsSetTemplateState(client, wsId, template.template_id, {
        enabled,
        credential_policy: next,
      })
      await onChanged()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  async function handleRefresh(): Promise<void> {
    if (!connector) return
    setRefreshing(true)
    setError(null)
    try {
      await wsRefreshDiscovery(client, wsId, connector.connector_id)
      await onChanged()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setRefreshing(false)
    }
  }

  async function handlePromote(): Promise<void> {
    setPromoting(true)
    setError(null)
    try {
      await wsPromoteTemplate(client, wsId, template.template_id)
      await onChanged()
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setPromoting(false)
    }
  }

  async function handleEditSubmit(body: UpdateTemplateBody): Promise<void> {
    setSavingEdit(true)
    setError(null)
    try {
      await wsUpdateTemplate(client, wsId, template.template_id, body)
      await onChanged()
      setEditing(false)
    } catch (err) {
      if (err instanceof ApiError && err.code === 'template_in_use') {
        toast.error(tMcp('toastTemplateInUseEdit'))
      } else {
        setError((err as Error).message)
      }
      throw err
    } finally {
      setSavingEdit(false)
    }
  }

  async function handleDelete(): Promise<void> {
    setDeleting(true)
    setError(null)
    try {
      await wsDeleteTemplate(client, wsId, template.template_id)
      await onChanged()
    } catch (err) {
      if (err instanceof ApiError && err.code === 'template_in_use') {
        toast.error(tMcp('toastTemplateInUseDelete'))
      } else {
        toast.error((err as Error).message)
      }
    } finally {
      setDeleting(false)
      setConfirmDelete(false)
    }
  }

  const discoveryError = connector?.discovery_status === 'error'
  const busy = saving || refreshing || promoting || savingEdit || deleting
  const orgCredentialAvailable = row.credential_availability_by_scope.org
  // Source of truth for the "selected" pill is the workspace state override;
  // fall back to the connector default only when the workspace has no state row.
  const activePolicy = (row.credential_policy ??
    connector?.default_credential_policy ??
    'none') as MCPCredentialScope

  if (editing) {
    const initialAuth = (template.supported_auth_methods?.[0] ?? 'none') as MCPAuthMethod
    return (
      <MCPTemplateCreateForm
        client={client}
        variant="workspace"
        initial={{
          name: template.name,
          server_url: template.server_url,
          transport: template.transport as CreateTemplateBody['transport'],
          auth_method: initialAuth,
          lockConnectivity: hasConnector,
        }}
        onSubmit={async (formBody) => {
          const diff: UpdateTemplateBody = {}
          if (formBody.name !== template.name) diff.name = formBody.name
          if (formBody.server_url !== template.server_url) diff.server_url = formBody.server_url
          if (formBody.transport !== template.transport) diff.transport = formBody.transport
          if (Object.keys(diff).length === 0) {
            setEditing(false)
            return
          }
          await handleEditSubmit(diff)
        }}
        onCancel={() => setEditing(false)}
      />
    )
  }

  return (
    <div className="flex w-full flex-col gap-4 p-6">
      {discoveryError && connector ? (
        <ServerErrorBanner
          error={connector.last_error ?? 'Discovery failed.'}
          onRetry={() => void handleRefresh()}
          retrying={refreshing}
        />
      ) : null}

      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-3">
          <ConnectorLogo
            name={template.name}
            icon={template.icon ?? null}
            serverIcons={connector?.server_icons ?? null}
            size="lg"
          />
          <div className="flex min-w-0 flex-col">
            <h3 className="truncate text-xl font-semibold tracking-tight">{template.name}</h3>
            <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
              <MCPScopeBadge scope={template.scope as MCPTemplateScope} />
              <StatusPill status={statusOf(row)} />
            </div>
          </div>
          <div className="ml-auto flex flex-wrap items-center gap-1.5">
            {isWsOwned ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => setEditing(true)}
                data-testid={`ws-catalog-edit-${template.template_id}`}
              >
                <Pencil className="mr-1.5 size-3.5" />
                {t('editTemplateAction')}
              </Button>
            ) : null}
            {isWsOwned ? (
              !confirmDelete ? (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                  disabled={busy}
                  onClick={() => setConfirmDelete(true)}
                  data-testid={`ws-catalog-delete-${template.template_id}`}
                >
                  <Trash2 className="mr-1.5 size-3.5" />
                  {t('deleteTemplateAction')}
                </Button>
              ) : (
                <div className="flex items-center gap-1.5 rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-1.5">
                  <span className="text-xs text-destructive">{t('confirmDeleteText')}</span>
                  <button
                    type="button"
                    className="cursor-pointer rounded p-0.5 text-destructive hover:bg-destructive/20"
                    disabled={deleting}
                    onClick={() => void handleDelete()}
                    data-testid={`ws-catalog-delete-confirm-${template.template_id}`}
                  >
                    <Check className="size-3.5" />
                  </button>
                  <button
                    type="button"
                    className="cursor-pointer rounded p-0.5 text-muted-foreground hover:bg-muted"
                    onClick={() => setConfirmDelete(false)}
                  >
                    <X className="size-3.5" />
                  </button>
                </div>
              )
            ) : null}
            {canPromote ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => void handlePromote()}
                data-testid={`ws-catalog-promote-${template.template_id}`}
              >
                {promoting ? (
                  <Loader2 className="mr-1.5 size-3.5 animate-spin" />
                ) : (
                  <ArrowUpCircle className="mr-1.5 size-3.5" />
                )}
                {t('promoteToOrg')}
              </Button>
            ) : null}
            {connector ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => void handleRefresh()}
              >
                {refreshing ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : null}
                {t('refreshTools')}
              </Button>
            ) : null}
          </div>
        </div>
        {template.description ? (
          <p className="text-sm text-muted-foreground">{template.description}</p>
        ) : null}
      </header>

      {effective ? (
        <WsAuthBand
          connector={effective}
          client={client}
          wsId={wsId}
          callerRole={isAdmin ? 'admin' : 'member'}
          onChanged={onChanged}
        />
      ) : null}

      <Tabs defaultValue="overview" className="flex-1 flex-col">
        <TabsList variant="line" className="w-full justify-start border-b border-border/60 pb-0">
          <TabsTrigger value="overview">{t('tabOverview')}</TabsTrigger>
          <TabsTrigger value="tools">
            <Wrench className="size-3.5" />
            {t('tabTools', { count: connector?.tool_count ?? 0 })}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="mt-4 flex flex-col gap-4">
          <div className="rounded-lg border border-border/70 bg-card/40 p-4">
            <h4 className="mb-3 text-sm font-semibold">{t('workspaceState')}</h4>
            <div className="flex items-center justify-between gap-3 text-sm">
              <span>{enabled ? t('wsEnabled') : t('wsDisabled')}</span>
              <Button
                size="sm"
                variant={enabled ? 'outline' : 'default'}
                disabled={saving}
                onClick={() => void handleToggle()}
                data-testid={`ws-catalog-toggle-${template.template_id}`}
              >
                {saving ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : null}
                {enabled ? tMcp('disableAction') : tMcp('enableAction')}
              </Button>
            </div>
            {error ? <p className="mt-2 text-xs text-destructive">{error}</p> : null}
          </div>

          {connector ? (
            <div className="rounded-lg border border-border/70 bg-card/40 p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <h4 className="text-sm font-semibold">{t('credentialPolicy')}</h4>
                {orgCredentialAvailable ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-success-solid/10 px-2 py-0.5 text-xs font-medium text-success-fg">
                    <CheckCircle2 className="size-3" />
                    {t('orgCredentialAvailable')}
                  </span>
                ) : null}
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                {(['org', 'workspace', 'user'] as const).map((p) => {
                  const selected = activePolicy === p
                  const credentialAvailable = row.credential_availability_by_scope[p]
                  return (
                    <Button
                      key={p}
                      type="button"
                      variant={selected ? 'default' : 'outline'}
                      disabled={saving}
                      className="h-auto min-h-16 justify-start px-3 py-2 text-left"
                      onClick={() => void handlePolicyChange(p)}
                    >
                      <span className="flex min-w-0 flex-col gap-1">
                        <span className="flex items-center gap-1.5 text-sm font-medium">
                          {selected ? <CheckCircle2 className="size-3.5 shrink-0" /> : null}
                          {t(policyLabelKey(p))}
                          {credentialAvailable ? (
                            <CheckCircle2
                              aria-label={t('policyCredentialAvailableLabel', {
                                scope: t(policyLabelKey(p)),
                              })}
                              className="size-3.5 shrink-0 text-success-fg"
                            />
                          ) : null}
                        </span>
                        <span
                          className={cn(
                            'text-xs leading-snug',
                            selected ? 'text-primary-foreground/80' : 'text-muted-foreground',
                          )}
                        >
                          {t(policyDescriptionKey(p))}
                        </span>
                      </span>
                    </Button>
                  )
                })}
              </div>
            </div>
          ) : null}
        </TabsContent>

        <TabsContent value="tools" className="mt-4">
          {connector ? (
            <WsToolsPanel
              tools={connector.tools}
              connectorId={connector.connector_id}
              client={client}
              wsId={wsId}
            />
          ) : (
            <div className="rounded-lg border border-dashed border-border px-6 py-12 text-center text-sm text-muted-foreground">
              {tMcp('reasonTemplateDisabled')}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}

export function McpPanel({ wsId }: McpPanelProps) {
  const t = useTranslations('mcp')
  const tAdmin = useTranslations('mcpAdmin')
  const client = useMemo(() => createApiClient(''), [])
  const wsRole = useWorkspaceStore((s) => s.workspaces.find((w) => w.id === wsId)?.role)
  const isAdmin = wsRole === 'admin'

  const [rows, setRows] = useState<WorkspaceCatalogRow[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<WsFilter>('all')
  const [source, setSource] = useState<MCPTemplateScope | 'all'>('all')
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(null)
  const [mode, setMode] = useState<'detail' | 'custom_create' | null>(null)

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

  const selectedRow = useMemo(
    () => rows.find((r) => r.template.template_id === selectedTemplateId) ?? null,
    [rows, selectedTemplateId],
  )

  function handleSelect(templateId: string): void {
    setSelectedTemplateId(templateId)
    setMode('detail')
  }

  function handleCreated(template: MCPTemplate): void {
    void load()
    setMode('detail')
    setSelectedTemplateId(template.template_id)
    toast.success(t('toastAdded', { name: template.name }))
  }

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border/70 px-6 py-4">
        <h2 className="text-lg font-semibold tracking-tight">{t('wsTitle')}</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{t('wsSubtitle')}</p>
      </header>

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
            name="mcp-connector-search"
            autoComplete="off"
          />
        </div>
        <FilterPills filter={filter} onFilterChange={setFilter} t={t} />
        <SourcePills source={source} onSourceChange={setSource} t={t} />
        {isAdmin ? (
          <Button
            size="sm"
            variant={mode === 'custom_create' ? 'default' : 'outline'}
            onClick={() => {
              setSelectedTemplateId(null)
              setMode('custom_create')
            }}
            data-testid="ws-add-custom-connector"
            className="ml-auto"
          >
            <Plus className="size-3.5" />
            {t('registerCustomTemplate')}
          </Button>
        ) : null}
      </div>

      <ListDetailLayout
        selected={selectedTemplateId !== null || mode === 'custom_create'}
        onBack={() => {
          setSelectedTemplateId(null)
          setMode(null)
        }}
        backLabel={tAdmin('back')}
        placeholder={tAdmin('selectConnector')}
        railClassName="w-[360px] bg-card/20 px-0 py-0"
        list={
          <div aria-label="MCP workspace catalog">
            {loading ? (
              <p className="px-4 py-6 text-center text-xs text-muted-foreground">{t('loading')}</p>
            ) : visible.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-1 px-4 py-10 text-center">
                <p className="text-sm font-medium">{t('noConnectors')}</p>
                <p className="text-xs text-muted-foreground">{t('noConnectorsHint')}</p>
              </div>
            ) : (
              <div className="flex flex-col gap-1.5 p-3">
                {visible.map((row) => (
                  <WsCatalogRow
                    key={row.template.template_id}
                    row={row}
                    active={row.template.template_id === selectedTemplateId}
                    onClick={() => handleSelect(row.template.template_id)}
                  />
                ))}
              </div>
            )}
          </div>
        }
        detail={
          mode === 'custom_create' ? (
            <WsCreateForm wsId={wsId} onCreated={handleCreated} />
          ) : selectedRow ? (
            <WsCatalogDetail
              key={selectedRow.template.template_id}
              row={selectedRow}
              wsId={wsId}
              isAdmin={isAdmin}
              client={client}
              onChanged={load}
            />
          ) : null
        }
      />
    </div>
  )
}

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
