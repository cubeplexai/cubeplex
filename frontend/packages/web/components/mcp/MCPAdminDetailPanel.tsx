'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  BookOpen,
  Check,
  FileText,
  Loader2,
  Network,
  Pencil,
  Trash2,
  Wrench,
  X,
  AlertTriangle,
  Power,
} from 'lucide-react'
import {
  adminDeleteTemplate,
  adminDistribute,
  adminPatchInstall,
  adminPurgeTemplate,
  adminRefreshDiscovery,
  adminSetTemplateDisabled,
  adminUpdateTemplate,
  useOrgAdminFlag,
  useWorkspaceStore,
  wsDeleteTemplate,
  wsUpdateTemplate,
  type AdminCatalogRow,
  type ApiClient,
  type MCPAuthMethod,
  type MCPConnector,
  type MCPTemplateScope,
  type UpdateTemplateBody,
} from '@cubeplex/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'

import { AdminAuthBand } from './AdminAuthBand'
import { ConnectorLogo } from './ConnectorLogo'
import { MCPCitationsTab } from './MCPCitationsTab'
import { MCPDistributeDialog } from './MCPDistributeDialog'
import { MCPScopeBadge } from './MCPScopeBadge'
import { MCPTemplateCreateForm, type CreateTemplateBody } from './MCPTemplateCreateForm'
import { MCPWorkspacesTab } from './MCPWorkspacesTab'
import { ServerErrorBanner } from './detail/ServerErrorBanner'
import { AdminToolsPanel } from './detail/tools/AdminToolsPanel'

interface MCPAdminDetailPanelProps {
  row: AdminCatalogRow | null
  mode: 'detail' | 'custom_create' | null
  client: ApiClient
  onRefresh: () => Promise<void>
  onDeleted: () => void
}

// Synthesize an MCPConnector from catalog row facts for components that
// still consume the legacy connector shape (citations tab, tools panel).
// When an org grant exists, auth_method is sourced from the grant (the method
// used when the grant was minted). When no grant, auth_method is 'none' —
// AdminToolsPanel.adminAuthMethod is optional and handles undefined/none safely.
function toMCPConnector(row: AdminCatalogRow): MCPConnector {
  const facts = row.connector!
  const hasGrant = row.org_grant_status !== null
  const authMethod: MCPAuthMethod = hasGrant
    ? ((facts.org_grant_auth_method ?? 'none') as MCPAuthMethod)
    : 'none'
  return {
    connector_id: facts.connector_id,
    template_id: row.template.template_id,
    install_scope: 'org',
    workspace_id: null,
    name: row.template.name,
    server_url: row.template.server_url,
    transport: row.template.transport as MCPConnector['transport'],
    auth_method: authMethod,
    default_credential_policy:
      facts.default_credential_policy as MCPConnector['default_credential_policy'],
    auth_status: row.org_grant_status === 'valid' ? 'authorized' : 'pending',
    discovery_status: facts.discovery_status,
    install_state: 'active',
    tool_count: facts.tool_count,
    tools: facts.tools,
    tool_citations: facts.tool_citations as unknown as MCPConnector['tool_citations'],
    last_error: facts.last_error,
    auto_enroll_new_workspaces: facts.auto_enroll_new_workspaces,
  }
}

export function MCPAdminDetailPanel({
  row,
  mode,
  client,
  onRefresh,
  onDeleted,
}: MCPAdminDetailPanelProps) {
  const t = useTranslations('mcpAdmin')

  const [confirmDelete, setConfirmDelete] = useState(false)
  const [confirmPurge, setConfirmPurge] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [purging, setPurging] = useState(false)
  const [toggling, setToggling] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [editing, setEditing] = useState(false)
  const [savingEdit, setSavingEdit] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [distributeOpen, setDistributeOpen] = useState(false)

  const currentOrgId = useWorkspaceStore((s) => s.workspaces[0]?.org_id ?? null)
  const isOrgAdmin = useOrgAdminFlag(currentOrgId)
  const orgWorkspaces = useWorkspaceStore((s) =>
    currentOrgId ? s.workspaces.filter((w) => w.org_id === currentOrgId) : [],
  )
  const [tryItScopedWsId, setTryItScopedWsId] = useState<string | null>(null)

  if (!row || mode === null) {
    return (
      <div className="flex flex-1 items-center justify-center p-8 text-sm text-muted-foreground">
        {t('selectConnector')}
      </div>
    )
  }

  const { template, connector, disabled } = row
  const templateId = template.template_id
  const isOrgOwned = template.scope === 'org'
  const isWorkspaceOwned = template.scope === 'workspace' && template.workspace_id !== null
  // Editable/deletable when the current org owns the template (org- or workspace-scoped).
  const isCustomOwned = isOrgOwned || isWorkspaceOwned
  const hasConnector = connector !== null
  const connectorId = connector?.connector_id ?? null
  const discoveryError = connector?.discovery_status === 'error'
  const policy = connector?.default_credential_policy ?? template.default_credential_policy
  // A grant row exists when org_grant_status is non-null (both 'valid' and 'expired').
  const hasGrant = row.org_grant_status !== null

  async function handleRefresh(): Promise<void> {
    if (!connectorId) return
    setRefreshing(true)
    setActionError(null)
    try {
      const lens = policy === 'org' || policy === 'none' ? null : tryItScopedWsId
      await adminRefreshDiscovery(client, connectorId, lens)
      await onRefresh()
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setRefreshing(false)
    }
  }

  async function handleToggleDisabled(): Promise<void> {
    setToggling(true)
    setActionError(null)
    try {
      await adminSetTemplateDisabled(client, templateId, !disabled)
      await onRefresh()
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setToggling(false)
    }
  }

  async function handleDelete(): Promise<void> {
    setDeleting(true)
    setActionError(null)
    try {
      if (isWorkspaceOwned && template.workspace_id) {
        await wsDeleteTemplate(client, template.workspace_id, templateId)
      } else {
        await adminDeleteTemplate(client, templateId)
      }
      onDeleted()
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setDeleting(false)
      setConfirmDelete(false)
    }
  }

  async function handleEditSubmit(body: UpdateTemplateBody): Promise<void> {
    setSavingEdit(true)
    setActionError(null)
    try {
      if (isWorkspaceOwned && template.workspace_id) {
        await wsUpdateTemplate(client, template.workspace_id, templateId, body)
      } else {
        await adminUpdateTemplate(client, templateId, body)
      }
      await onRefresh()
      setEditing(false)
    } catch (err) {
      setActionError((err as Error).message)
      throw err
    } finally {
      setSavingEdit(false)
    }
  }

  async function handlePurge(): Promise<void> {
    setPurging(true)
    setActionError(null)
    try {
      await adminPurgeTemplate(client, templateId)
      await onRefresh()
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setPurging(false)
      setConfirmPurge(false)
    }
  }

  async function handleDistribute(opts: {
    enable_existing: boolean
    auto_enroll: boolean
  }): Promise<void> {
    await adminDistribute(client, templateId, opts)
    await onRefresh()
  }

  const busy = refreshing || deleting || purging || toggling || savingEdit

  const syntheticInstall: MCPConnector | null = hasConnector ? toMCPConnector(row) : null

  if (editing) {
    const initialAuth = (syntheticInstall?.auth_method ??
      (template.supported_auth_methods?.[0] as MCPAuthMethod | undefined) ??
      'none') as MCPAuthMethod
    return (
      <MCPTemplateCreateForm
        client={client}
        variant={isWorkspaceOwned ? 'workspace' : 'admin'}
        initial={{
          name: template.name,
          server_url: template.server_url,
          transport: template.transport as CreateTemplateBody['transport'],
          auth_method: initialAuth,
          lockConnectivity: hasConnector,
        }}
        onSubmit={async (body) => {
          const diff: UpdateTemplateBody = {}
          if (body.name !== template.name) diff.name = body.name
          if (body.server_url !== template.server_url) diff.server_url = body.server_url
          if (body.transport !== template.transport) diff.transport = body.transport
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
    <div className="flex w-full flex-col gap-4 p-6" data-testid="mcp-admin-detail-panel">
      {discoveryError && connector ? (
        <ServerErrorBanner
          error={connector.last_error ?? 'Discovery failed.'}
          onRetry={() => void handleRefresh()}
          retrying={refreshing}
        />
      ) : null}

      <div className="flex flex-col gap-4 rounded-xl border border-border bg-card p-5 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <ConnectorLogo
              name={template.name}
              icon={template.icon ?? null}
              serverIcons={connector?.server_icons ?? null}
              size="lg"
            />
            {disabled ? (
              <Badge variant="destructive" className="text-[11px]">
                {t('disabledBadge')}
              </Badge>
            ) : null}
            {row.needs_attention && !disabled ? (
              <Badge variant="secondary" className="text-[11px] text-warning-fg">
                {t('needsAttentionBadge')}
              </Badge>
            ) : null}
            <h1 className="truncate text-2xl font-semibold">{template.name}</h1>
            <MCPScopeBadge scope={template.scope as MCPTemplateScope} />
            {hasConnector ? (
              <span
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium',
                  row.in_use
                    ? 'bg-success-surface text-success-fg'
                    : 'bg-muted text-muted-foreground',
                )}
              >
                <span
                  className={cn(
                    'h-1.5 w-1.5 rounded-full',
                    row.in_use ? 'bg-success-solid' : 'bg-muted-foreground',
                  )}
                />
                {row.enabled_workspace_count}/{row.eligible_workspace_count}
              </span>
            ) : null}
          </div>
          {template.description ? (
            <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
              {template.description}
            </p>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {hasConnector ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => void handleRefresh()}
            >
              {refreshing ? <Loader2 data-icon="inline-start" className="animate-spin" /> : null}
              {t('refreshTools')}
            </Button>
          ) : null}

          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => setDistributeOpen(true)}
          >
            {t('distributeAction')}
          </Button>

          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => void handleToggleDisabled()}
          >
            {toggling ? (
              <Loader2 data-icon="inline-start" className="animate-spin" />
            ) : (
              <Power data-icon="inline-start" />
            )}
            {disabled ? t('enableOrgAction') : t('disableOrgAction')}
          </Button>

          {hasConnector ? (
            !confirmPurge ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="text-warning-fg hover:bg-warning-surface hover:text-warning-fg"
                disabled={busy}
                onClick={() => setConfirmPurge(true)}
              >
                <AlertTriangle data-icon="inline-start" />
                {t('purgeTitle')}
              </Button>
            ) : (
              <div className="flex items-center gap-1.5 rounded-md border border-warning-solid/30 bg-warning-surface px-2.5 py-1.5">
                <span className="text-xs text-warning-fg">{t('purgeConfirmText')}</span>
                <button
                  type="button"
                  className="cursor-pointer rounded p-0.5 text-warning-fg hover:bg-warning-solid/20"
                  disabled={purging}
                  onClick={() => void handlePurge()}
                >
                  <Check className="size-3.5" />
                </button>
                <button
                  type="button"
                  className="cursor-pointer rounded p-0.5 text-muted-foreground hover:bg-muted"
                  onClick={() => setConfirmPurge(false)}
                >
                  <X className="size-3.5" />
                </button>
              </div>
            )
          ) : null}

          {isOrgAdmin && isCustomOwned ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => setEditing(true)}
            >
              <Pencil data-icon="inline-start" />
              {t('editTemplateAction')}
            </Button>
          ) : null}

          {isOrgAdmin && isCustomOwned ? (
            !confirmDelete ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                disabled={busy}
                onClick={() => setConfirmDelete(true)}
              >
                <Trash2 data-icon="inline-start" />
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
        </div>
      </div>

      {actionError ? <p className="text-xs text-destructive">{actionError}</p> : null}

      {hasConnector ? (
        <>
          {syntheticInstall &&
            connector &&
            template.supported_auth_methods.length >= 2 &&
            !hasGrant && (
              <AuthMethodSwitcher
                install={syntheticInstall}
                client={client}
                onChanged={onRefresh}
              />
            )}
          <AdminAuthBand row={row} client={client} onChanged={onRefresh} />
        </>
      ) : (
        <div className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
          {t('noConnectorInstalled')}
        </div>
      )}

      <Tabs defaultValue="overview" className="flex-1 flex-col">
        <TabsList variant="line" className="w-full justify-start border-b border-border/60 pb-0">
          <TabsTrigger value="overview">
            <FileText className="size-3.5" />
            {t('tabOverview')}
          </TabsTrigger>
          {hasConnector ? (
            <>
              <TabsTrigger value="tools">
                <Wrench className="size-3.5" />
                {t('tabTools', { count: connector!.tool_count })}
              </TabsTrigger>
              <TabsTrigger value="citations">
                <BookOpen className="size-3.5" />
                {t('tabCitations')}
              </TabsTrigger>
              <TabsTrigger value="workspaces">
                <Network className="size-3.5" />
                {t('tabWorkspaces')}
              </TabsTrigger>
            </>
          ) : null}
        </TabsList>

        <TabsContent value="overview" className="mt-4">
          <div className="flex flex-col gap-3 rounded-lg border border-border/70 bg-card/40 p-4 text-sm">
            <dl className="grid grid-cols-[180px_1fr] gap-y-2">
              <dt className="text-muted-foreground">Template ID</dt>
              <dd className="font-mono text-xs">{templateId}</dd>
              <dt className="text-muted-foreground">Slug</dt>
              <dd className="font-mono text-xs">{template.slug}</dd>
              <dt className="text-muted-foreground">{t('overviewSource')}</dt>
              <dd>{template.scope}</dd>
              {hasConnector ? (
                <>
                  <dt className="text-muted-foreground">Connector ID</dt>
                  <dd className="font-mono text-xs">{connectorId}</dd>
                  <dt className="text-muted-foreground">{t('credentialPolicy')}</dt>
                  <dd>{policy}</dd>
                  <dt className="text-muted-foreground">{t('discoveryStatus')}</dt>
                  <dd>{connector!.discovery_status}</dd>
                </>
              ) : null}
              {row.org_grant_status ? (
                <>
                  <dt className="text-muted-foreground">{t('orgGrant')}</dt>
                  <dd
                    className={
                      row.org_grant_status === 'valid' ? 'text-success-fg' : 'text-destructive'
                    }
                  >
                    {row.org_grant_status === 'valid' ? t('wsEnabled') : t('wsDisabled')}
                  </dd>
                </>
              ) : null}
            </dl>
          </div>
        </TabsContent>

        {hasConnector && syntheticInstall ? (
          <>
            <TabsContent value="tools" className="mt-4">
              <AdminToolsPanel
                tools={connector!.tools}
                connectorId={connectorId!}
                client={client}
                wsId={tryItScopedWsId}
                requiresWorkspacePicker={policy === 'workspace' || policy === 'user'}
                adminWorkspaceOptions={orgWorkspaces.map((w) => ({ id: w.id, name: w.name }))}
                scopedAdminWorkspaceId={tryItScopedWsId}
                onScopedWorkspaceChange={setTryItScopedWsId}
                adminAuthMethod={syntheticInstall.auth_method}
              />
            </TabsContent>

            <TabsContent value="citations" className="mt-4">
              <MCPCitationsTab
                install={syntheticInstall}
                client={client}
                onUpdated={() => void onRefresh()}
              />
            </TabsContent>

            <TabsContent value="workspaces" className="mt-4">
              <MCPWorkspacesTab templateId={templateId} client={client} />
            </TabsContent>
          </>
        ) : null}
      </Tabs>

      <MCPDistributeDialog
        templateName={template.name}
        open={distributeOpen}
        onOpenChange={setDistributeOpen}
        onConfirm={handleDistribute}
      />
    </div>
  )
}

// Auth method switcher — only shown when connector has multiple supported methods
// and no credential is yet provisioned.
function AuthMethodSwitcher({
  install,
  client,
  onChanged,
}: {
  install: MCPConnector
  client: ApiClient
  onChanged: () => Promise<void>
}) {
  const t = useTranslations('mcpAdmin')
  const [submitting, setSubmitting] = useState<MCPAuthMethod | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Supported methods come from the MCPConnector's template — but since we're
  // synthesizing from AdminCatalogRow, we check auth_method directly.
  // Only show when we actually have a grant-free connector.
  if (install.auth_method === 'none') return null

  const current = install.auth_method

  async function handleSwitch(method: MCPAuthMethod): Promise<void> {
    if (method === current) return
    setSubmitting(method)
    setError(null)
    try {
      const nextPolicy: MCPConnector['default_credential_policy'] =
        method === 'none'
          ? 'none'
          : install.default_credential_policy === 'none'
            ? 'org'
            : install.default_credential_policy
      await adminPatchInstall(client, install.connector_id, {
        auth_method: method,
        default_credential_policy: nextPolicy,
      })
      await onChanged()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(null)
    }
  }

  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-border/60 bg-muted/30 px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          {t('authMethodLabel')}
        </span>
        {(['static', 'oauth', 'none'] as MCPAuthMethod[]).map((m) => (
          <Button
            key={m}
            type="button"
            size="sm"
            variant={current === m ? 'default' : 'outline'}
            disabled={submitting !== null}
            onClick={() => void handleSwitch(m)}
          >
            {submitting === m ? (
              <Loader2 data-icon="inline-start" className="animate-spin" />
            ) : null}
            {t(
              m === 'oauth'
                ? 'authMethodOAuth'
                : m === 'static'
                  ? 'authMethodStatic'
                  : 'authMethodNone',
            )}
          </Button>
        ))}
      </div>
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </div>
  )
}
