'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  ArrowUpCircle,
  BookOpen,
  Check,
  FileText,
  Loader2,
  Network,
  Trash2,
  Wrench,
  X,
} from 'lucide-react'
import {
  adminDeleteInstall,
  adminDeleteOrgGrant,
  adminPatchInstall,
  adminPromoteToOrg,
  adminRefreshDiscovery,
  useOrgAdminFlag,
  useWorkspaceStore,
  wsListTemplates,
  type AdminOrgConnector,
  type ApiClient,
  type MCPAuthMethod,
  type MCPConnectorTemplate,
  type PromoteDistribution,
} from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'

import { AdminAuthBand } from './AdminAuthBand'
import { MCPCitationsTab } from './MCPCitationsTab'
import { MCPCustomCreatePanel } from './MCPCustomCreatePanel'
import { MCPPromoteDialog } from './MCPPromoteDialog'
import { MCPTemplateInstallPanel } from './MCPTemplateInstallPanel'
import { MCPWorkspacesTab } from './MCPWorkspacesTab'
import { ServerErrorBanner } from './detail/ServerErrorBanner'
import { AdminToolsPanel } from './detail/tools/AdminToolsPanel'

interface MCPAdminDetailPanelProps {
  connector: AdminOrgConnector | null
  mode: 'detail' | 'install_template' | 'custom_install' | null
  installTemplate: MCPConnectorTemplate | null
  client: ApiClient
  onRefresh: () => Promise<void>
  onDelete: (connectorId: string) => Promise<void>
  onInstalled: (connectorId: string) => void
}

export function MCPAdminDetailPanel({
  connector,
  mode,
  installTemplate,
  client,
  onRefresh,
  onDelete,
  onInstalled,
}: MCPAdminDetailPanelProps) {
  const t = useTranslations('mcpAdmin')

  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [replacingCredential, setReplacingCredential] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [promoteOpen, setPromoteOpen] = useState(false)

  // Admin page has no workspace lens. Use the first workspace's org_id as
  // the caller's org context — single-org-per-user today; revisit when
  // multi-org ships.
  const currentOrgId = useWorkspaceStore((s) => s.workspaces[0]?.org_id ?? null)
  const isOrgAdmin = useOrgAdminFlag(currentOrgId)
  // Workspaces in caller's org — used by Try It's workspace picker
  // when an install needs scoped-grant resolution (workspace/user policy).
  const orgWorkspaces = useWorkspaceStore((s) =>
    currentOrgId ? s.workspaces.filter((w) => w.org_id === currentOrgId) : [],
  )
  const [tryItScopedWsId, setTryItScopedWsId] = useState<string | null>(null)

  if (mode === 'install_template' && installTemplate) {
    return (
      <MCPTemplateInstallPanel
        template={installTemplate}
        client={client}
        onInstalled={onInstalled}
      />
    )
  }

  if (mode === 'custom_install') {
    return (
      <MCPCustomCreatePanel
        client={client}
        onCreated={(install) => onInstalled(install.connector_id)}
      />
    )
  }

  if (!connector) {
    return (
      <div className="flex flex-1 items-center justify-center p-8 text-sm text-muted-foreground">
        {t('selectConnector')}
      </div>
    )
  }

  const install = connector.install
  const orgEff = connector.org_effective
  const isOrgWide = install.install_scope === 'org'
  const connectorId = install.connector_id
  const connected = orgEff.usable
  const policy = install.default_credential_policy

  async function handleRefresh(): Promise<void> {
    setRefreshing(true)
    setActionError(null)
    try {
      // Re-run tool discovery on the backend, then reload the list so the
      // parent's connector state reflects the new tools_cache/discovery_status.
      //
      // Lens selection (admin page has no workspace lens):
      // - 'org' / 'none' policy → null (use org grant or no creds).
      // - 'workspace' / 'user' policy → use the Try It picker selection
      //   when present; otherwise null and let the backend surface
      //   'workspace_id_required_for_scoped_policy' so the user
      //   understands they need a picker selection.
      const lens = policy === 'org' || policy === 'none' ? null : tryItScopedWsId
      await adminRefreshDiscovery(client, connectorId, lens)
      await onRefresh()
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setRefreshing(false)
    }
  }

  async function handleDelete(): Promise<void> {
    setDeleting(true)
    setActionError(null)
    try {
      await onDelete(connectorId)
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setDeleting(false)
      setConfirmDelete(false)
    }
  }

  async function handlePromote(distribution: PromoteDistribution): Promise<void> {
    await adminPromoteToOrg(client, connectorId, distribution)
    await onRefresh()
  }

  async function handleReplaceCredential(): Promise<void> {
    // Discovery_failed after a fresh grant usually means the credential
    // is bad (wrong static token, OAuth granted but server rejects).
    // The admin page only manages the ORG grant; workspace/user grants
    // live on the workspace settings UI. We delete the org grant then
    // refresh so the auth band re-renders in "needs credential" state.
    setReplacingCredential(true)
    setActionError(null)
    try {
      await adminDeleteOrgGrant(client, connectorId)
      await onRefresh()
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setReplacingCredential(false)
    }
  }

  const canPromote = !isOrgWide && isOrgAdmin

  const busy = refreshing || deleting || replacingCredential

  const discoveryError = install.discovery_status === 'error'
  // "Replace credential" only fires `adminDeleteOrgGrant`, which is a
  // no-op for installs whose policy keeps credentials at the
  // workspace/user level — clicking it on those would silently fail and
  // leave the stale discovery error. Gate on org-policy + credentialed
  // auth method. Workspace/user policy installs surface the same fix
  // path on the per-workspace settings page (Replace credential there
  // hits the matching scope).
  const hasCredential =
    install.auth_method !== 'none' && install.default_credential_policy === 'org'

  return (
    <div className="flex w-full flex-col gap-4 p-6" data-testid="mcp-admin-detail-panel">
      {discoveryError ? (
        <ServerErrorBanner
          error={install.last_error ?? 'Discovery failed.'}
          onRetry={() => void handleRefresh()}
          retrying={refreshing}
          onReplaceCredential={hasCredential ? () => void handleReplaceCredential() : undefined}
          replacing={replacingCredential}
        />
      ) : null}
      <div className="flex flex-col gap-4 rounded-xl border border-border bg-card p-5 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={cn(
                'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium',
                connected
                  ? 'bg-success-surface text-success-fg'
                  : 'bg-danger-surface text-danger-fg',
              )}
            >
              <span
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  connected ? 'bg-success-solid' : 'bg-danger-solid',
                )}
              />
              {connected ? t('ready') : t('needsCredential')}
            </span>
            <h1 className="truncate text-2xl font-semibold">
              {install.name || connector.template?.name || connectorId}
            </h1>
            <Badge variant="outline" className="text-[11px]">
              {isOrgWide ? t('scopeOrg') : t('scopeWorkspace')}
            </Badge>
            <Badge variant="secondary" className="text-[11px]">
              {policy}
            </Badge>
          </div>
          <p className="text-sm text-muted-foreground">
            {t('installAuthSummary', {
              auth: install.auth_method,
              authStatus: install.auth_status,
              discoveryStatus: install.discovery_status,
            })}
          </p>
          {connector.template?.description ? (
            <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
              {connector.template.description}
            </p>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-2">
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
          {canPromote ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => setPromoteOpen(true)}
              data-testid="mcp-promote-menu-item"
            >
              <ArrowUpCircle data-icon="inline-start" />
              {t('promoteToOrg')}
            </Button>
          ) : null}
          {!confirmDelete ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="text-destructive hover:bg-destructive/10 hover:text-destructive"
              disabled={busy}
              onClick={() => setConfirmDelete(true)}
            >
              <Trash2 data-icon="inline-start" />
              {t('uninstallButton')}
            </Button>
          ) : (
            <div className="flex items-center gap-1.5 rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-1.5">
              <span className="text-xs text-destructive">{t('confirmUninstallLabel')}</span>
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
          )}
        </div>
      </div>

      {actionError ? <p className="text-xs text-destructive">{actionError}</p> : null}

      <AuthMethodSwitcher connector={connector} client={client} onChanged={onRefresh} />

      <AdminAuthBand connector={connector} client={client} onChanged={onRefresh} />

      <Tabs defaultValue="overview" className="flex-1 flex-col">
        <TabsList variant="line" className="w-full justify-start border-b border-border/60 pb-0">
          <TabsTrigger value="overview">
            <FileText className="size-3.5" />
            {t('tabOverview')}
          </TabsTrigger>
          <TabsTrigger value="tools">
            <Wrench className="size-3.5" />
            {t('tabTools', { count: install.tool_count })}
          </TabsTrigger>
          <TabsTrigger value="citations">
            <BookOpen className="size-3.5" />
            {t('tabCitations')}
          </TabsTrigger>
          {isOrgWide && (
            <TabsTrigger value="workspaces">
              <Network className="size-3.5" />
              {t('tabWorkspaces')}
            </TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="overview" className="mt-4">
          <div className="flex flex-col gap-3 rounded-lg border border-border/70 bg-card/40 p-4 text-sm">
            <dl className="grid grid-cols-[180px_1fr] gap-y-2">
              <dt className="text-muted-foreground">{t('installs')}</dt>
              <dd className="font-mono text-xs">{connectorId}</dd>
              <dt className="text-muted-foreground">{t('credentialPolicy')}</dt>
              <dd>{policy}</dd>
              <dt className="text-muted-foreground">{t('authStatus')}</dt>
              <dd>{install.auth_status}</dd>
              <dt className="text-muted-foreground">{t('discoveryStatus')}</dt>
              <dd>{install.discovery_status}</dd>
              <dt className="text-muted-foreground">{t('credentialAvailability')}</dt>
              <dd>{orgEff.credential_availability ?? '—'}</dd>
            </dl>
          </div>
        </TabsContent>

        <TabsContent value="tools" className="mt-4">
          <AdminToolsPanel
            tools={install.tools}
            connectorId={connectorId}
            client={client}
            // Admin page has no workspace lens. For credentialed scoped
            // policies the Try It picker drives wsId; for org/none
            // policies wsId stays null and the backend resolves via
            // the org grant.
            wsId={tryItScopedWsId}
            requiresWorkspacePicker={policy === 'workspace' || policy === 'user'}
            // Workspace picker wiring for scoped Try It. Without
            // adminWorkspaceOptions + onScopedWorkspaceChange the
            // picker never shows and Try It silently uses null wsId,
            // which fails for scoped policies.
            adminWorkspaceOptions={orgWorkspaces.map((w) => ({ id: w.id, name: w.name }))}
            scopedAdminWorkspaceId={tryItScopedWsId}
            onScopedWorkspaceChange={setTryItScopedWsId}
            adminAuthMethod={install.auth_method}
          />
        </TabsContent>

        <TabsContent value="citations" className="mt-4">
          <MCPCitationsTab install={install} client={client} onUpdated={() => void onRefresh()} />
        </TabsContent>

        {isOrgWide && (
          <TabsContent value="workspaces" className="mt-4">
            <MCPWorkspacesTab connectorId={connectorId} client={client} />
          </TabsContent>
        )}
      </Tabs>

      <MCPPromoteDialog
        install={install}
        open={promoteOpen}
        onOpenChange={setPromoteOpen}
        onConfirm={handlePromote}
      />
    </div>
  )
}

// Multi-method auth picker. Renders only when the template supports more
// than one auth method AND no credential is already provisioned —
// switching with a grant attached would orphan it, which the backend
// also rejects (409). Calls PATCH install with auth_method + a derived
// default_credential_policy; the (auth, policy) pair is validated
// server-side.
function AuthMethodSwitcher({
  connector,
  client,
  onChanged,
}: {
  connector: AdminOrgConnector
  client: ApiClient
  onChanged: () => Promise<void>
}) {
  const t = useTranslations('mcpAdmin')
  const [submitting, setSubmitting] = useState<MCPAuthMethod | null>(null)
  const [error, setError] = useState<string | null>(null)
  const supported = connector.template?.supported_auth_methods ?? []
  const hasGrant = connector.org_effective.credential_availability === 'available'
  // Don't render the chooser when there's nothing to choose (single
  // method, or grant already exists and the switch would be rejected).
  if (supported.length < 2 || hasGrant) return null

  const current = connector.install.auth_method

  async function handleSwitch(method: MCPAuthMethod): Promise<void> {
    if (method === current) return
    setSubmitting(method)
    setError(null)
    try {
      // Pair (auth_method, default_credential_policy) per the install
      // validator: 'none' policy iff 'none' auth. When switching TO
      // 'none', force policy='none'. When switching FROM 'none' to a
      // credentialed method, set a sensible policy ('org') because the
      // existing policy='none' is incompatible. Server re-validates.
      const nextPolicy: 'none' | 'org' | 'workspace' | 'user' =
        method === 'none'
          ? 'none'
          : connector.install.default_credential_policy === 'none'
            ? 'org'
            : connector.install.default_credential_policy
      await adminPatchInstall(client, connector.install.connector_id, {
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
        {supported.map((m) => (
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

// Helper: re-export template-loading hook for consumers that need to compose a
// template-driven install flow from the admin page.
export async function loadTemplates(
  client: ApiClient,
  wsId: string,
): Promise<MCPConnectorTemplate[]> {
  const res = await wsListTemplates(client, wsId)
  return res.items
}

// Reference imports used by the admin page when wiring uninstall. Kept here so
// callers don't have to duplicate the import map.
export { adminDeleteInstall }
