'use client'

import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Check, FileText, Loader2, Network, Trash2, X } from 'lucide-react'
import {
  adminDeleteInstall,
  adminGetInstallEffective,
  wsListTemplates,
  type ApiClient,
  type MCPAdminInstallEffective,
  type MCPConnectorTemplate,
  type MCPEffectiveConnector,
} from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'

import { AuthActionBand } from './AuthActionBand'
import { MCPTemplateInstallPanel } from './MCPTemplateInstallPanel'
import { MCPWorkspacesTab } from './MCPWorkspacesTab'

interface MCPAdminDetailPanelProps {
  connector: MCPEffectiveConnector | null
  mode: 'detail' | 'install_template' | null
  installTemplate: MCPConnectorTemplate | null
  client: ApiClient
  wsId: string
  onRefresh: () => Promise<void>
  onDelete: (installId: string) => Promise<void>
  onInstalled: (installId: string) => void
}

export function MCPAdminDetailPanel({
  connector,
  mode,
  installTemplate,
  client,
  wsId,
  onRefresh,
  onDelete,
  onInstalled,
}: MCPAdminDetailPanelProps) {
  const t = useTranslations('mcpAdmin')

  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  if (mode === 'install_template' && installTemplate) {
    return (
      <MCPTemplateInstallPanel
        template={installTemplate}
        client={client}
        wsId={wsId}
        onInstalled={onInstalled}
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
  const ws = connector.workspace_state
  const isOrgWide = install.install_scope === 'org'
  const installId = install.install_id
  const connected = connector.usable

  async function handleRefresh(): Promise<void> {
    setRefreshing(true)
    setActionError(null)
    try {
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
      await onDelete(installId)
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setDeleting(false)
      setConfirmDelete(false)
    }
  }

  const busy = refreshing || deleting

  return (
    <div className="flex w-full flex-col gap-4 p-6" data-testid="mcp-admin-detail-panel">
      <div className="flex flex-col gap-4 rounded-xl border border-border bg-card p-5 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={cn(
                'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium',
                connected
                  ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300'
                  : 'bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300',
              )}
            >
              <span
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  connected ? 'bg-emerald-500' : 'bg-rose-500',
                )}
              />
              {connected ? t('ready') : t('needsCredential')}
            </span>
            <h1 className="truncate text-2xl font-semibold">
              {install.name || connector.template?.name || installId}
            </h1>
            <Badge variant="outline" className="text-[11px]">
              {isOrgWide ? t('scopeOrg') : t('scopeWorkspace')}
            </Badge>
            <Badge variant="secondary" className="text-[11px]">
              {connector.credential_policy}
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

      <AdminAuthActionBand
        connector={connector}
        client={client}
        wsId={wsId}
        onChanged={onRefresh}
      />

      <Tabs defaultValue="overview" className="flex-1 flex-col">
        <TabsList variant="line" className="w-full justify-start border-b border-border/60 pb-0">
          <TabsTrigger value="overview">
            <FileText className="size-3.5" />
            {t('tabOverview')}
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
              <dd className="font-mono text-xs">{installId}</dd>
              <dt className="text-muted-foreground">{t('credentialPolicy')}</dt>
              <dd>{connector.credential_policy}</dd>
              <dt className="text-muted-foreground">{t('workspaceState')}</dt>
              <dd>{ws?.enabled ? t('wsEnabled') : t('wsDisabled')}</dd>
              <dt className="text-muted-foreground">{t('authStatus')}</dt>
              <dd>{install.auth_status}</dd>
              <dt className="text-muted-foreground">{t('discoveryStatus')}</dt>
              <dd>{install.discovery_status}</dd>
              <dt className="text-muted-foreground">{t('credentialAvailability')}</dt>
              <dd>{connector.credential_availability}</dd>
              {connector.credential_source ? (
                <>
                  <dt className="text-muted-foreground">{t('credentialSource')}</dt>
                  <dd>{connector.credential_source}</dd>
                </>
              ) : null}
            </dl>
          </div>
        </TabsContent>

        {isOrgWide && (
          <TabsContent value="workspaces" className="mt-4">
            <MCPWorkspacesTab installId={installId} client={client} />
          </TabsContent>
        )}
      </Tabs>
    </div>
  )
}

/**
 * Wraps AuthActionBand for the admin detail panel.
 *
 * For org-scope installs the workspace lens can misreport `required_grant_scope`
 * (e.g. an org install whose workspace lens overrides to `user` would surface
 * the wrong band on the admin page). On admin we always want the org-row
 * semantics — see spec §4 admin row. We therefore pre-fetch the dedicated
 * admin /effective endpoint and synthesize an `MCPEffectiveConnector` with
 * `required_grant_scope='org'` before handing it to the band.
 *
 * For workspace-scope installs the lens is already the right answer; no extra
 * call is needed.
 */
function AdminAuthActionBand({
  connector,
  client,
  wsId,
  onChanged,
}: {
  connector: MCPEffectiveConnector
  client: ApiClient
  wsId: string
  onChanged: () => Promise<void>
}) {
  const isOrgScope = connector.install.install_scope === 'org'
  const [orgEffective, setOrgEffective] = useState<MCPAdminInstallEffective | null>(null)
  const [loaded, setLoaded] = useState(!isOrgScope)

  useEffect(() => {
    if (!isOrgScope) {
      setLoaded(true)
      return
    }
    let cancelled = false
    setLoaded(false)
    void adminGetInstallEffective(client, connector.install.install_id)
      .then((res) => {
        if (cancelled) return
        setOrgEffective(res)
      })
      .catch(() => {
        // Fall back to lens connector — band will hide if reason is unknown.
      })
      .finally(() => {
        if (!cancelled) setLoaded(true)
      })
    return () => {
      cancelled = true
    }
  }, [client, connector.install.install_id, isOrgScope])

  if (!loaded) return null

  const synthesized: MCPEffectiveConnector =
    isOrgScope && orgEffective
      ? {
          ...connector,
          required_grant_scope: 'org',
          usable: orgEffective.usable,
          reason: orgEffective.reason,
          // For auth_method='none' installs the backend reports
          // reason='usable' AND no credential is involved at all.
          // The ready band has a dedicated "No credential required"
          // sub-state that keys off `credential_availability ===
          // 'not_required'` — forcing 'available' here would route
          // the band into the "credential from <source>" variant
          // even though credential_source is null, which would
          // render incorrectly. Mirror the backend's effective-state
          // mapping: auth_method='none' → not_required.
          credential_availability:
            connector.install.auth_method === 'none'
              ? 'not_required'
              : orgEffective.usable
                ? 'available'
                : 'missing',
          // The workspace lens (when the admin's current workspace
          // overrides the org-policy install down to user/workspace)
          // surfaces a different credential_source. For the org-row
          // band we always want 'org' (when usable + needs a
          // credential) so the ready band reads "credential from
          // Org grant" and the Disconnect menu targets the org
          // grant — not whichever workspace-lens grant happened to
          // be there.
          credential_source:
            connector.install.auth_method === 'none' ? null : orgEffective.usable ? 'org' : null,
        }
      : connector

  return (
    <AuthActionBand
      connector={synthesized}
      client={client}
      wsId={wsId}
      callerRole="admin"
      isOrgAdmin={true}
      onChanged={onChanged}
    />
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
