'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  Check,
  FileText,
  KeyRound,
  Loader2,
  Network,
  RefreshCw,
  Trash2,
  Wrench,
  X,
} from 'lucide-react'
import type { ApiClient, MCPAdminConnector } from '@cubebox/core'
import { useMcpStore } from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'

import { MCPCatalogInstallPanel } from './MCPCatalogInstallPanel'
import { MCPCitationMappingTab } from './MCPCitationMappingTab'
import { MCPCustomCreatePanel } from './MCPCustomCreatePanel'
import { MCPWorkspacesTab } from './MCPWorkspacesTab'
import { OverviewPanel } from './detail/OverviewPanel'
import { ServerErrorBanner } from './detail/ServerErrorBanner'
import { MCPScopeBadge } from './MCPScopeBadge'
import { ToolsPanel } from './detail/tools/ToolsPanel'

const OAUTH_ORIGIN_KEY = 'mcp_oauth_origin'

function persistOAuthOrigin(): void {
  if (typeof window === 'undefined') return
  try {
    window.sessionStorage.setItem(
      OAUTH_ORIGIN_KEY,
      window.location.pathname + window.location.search,
    )
  } catch {
    // sessionStorage may be unavailable; non-fatal.
  }
}

interface MCPAdminDetailPanelProps {
  connector: MCPAdminConnector | null
  mode: 'detail' | 'add_custom' | null
  client: ApiClient
  wsId: string
  onRefresh: (id: string) => Promise<void>
  onDelete: (id: string) => Promise<void>
  onInstalled: (id: string) => void
  onCreated: (id: string) => void
}

export function MCPAdminDetailPanel({
  connector,
  mode,
  client,
  wsId,
  onRefresh,
  onDelete,
  onInstalled,
  onCreated,
}: MCPAdminDetailPanelProps) {
  const t = useTranslations('mcpAdmin')
  const tDetail = useTranslations('mcp.detail')

  const [refreshing, setRefreshing] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [reauthorizing, setReauthorizing] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const startOAuth = useMcpStore((s) => s.startOAuth)

  // ── Placeholder states ──────────────────────────────────────────────

  if (!connector && mode !== 'add_custom') {
    return (
      <div
        className="flex flex-1 items-center justify-center p-8 text-sm
          text-muted-foreground"
      >
        {t('selectConnector')}
      </div>
    )
  }

  if (connector && !connector.installed) {
    if (connector.kind === 'catalog') {
      return (
        <MCPCatalogInstallPanel
          connector={connector}
          client={client}
          wsId={wsId}
          onInstalled={onInstalled}
        />
      )
    }
    return (
      <div
        className="flex flex-1 items-center justify-center p-8 text-sm
          text-muted-foreground"
      >
        {t('selectInstalledConnector')}
      </div>
    )
  }

  if (mode === 'add_custom' && !connector) {
    return <MCPCustomCreatePanel client={client} wsId={wsId} onCreated={onCreated} />
  }

  // From here, connector is non-null and installed with a server
  const server = connector!.server
  if (!server) {
    return (
      <div
        className="flex flex-1 items-center justify-center p-8 text-sm
          text-muted-foreground"
      >
        {t('selectConnector')}
      </div>
    )
  }

  const c = connector!

  const isOrgWide = server.owner_workspace_id === null
  const toolCount = server.tools_cache?.length ?? 0
  const connected = c.authed
  const formattedTime = server.last_discovered_at
    ? new Date(server.last_discovered_at).toLocaleString()
    : null
  const toolsLabel = tDetail('toolsCount', { count: toolCount })
  const metaLine = formattedTime
    ? tDetail('metaLine', { time: formattedTime, tools: toolsLabel })
    : tDetail('metaLineNever', { tools: toolsLabel })

  async function handleRefresh(): Promise<void> {
    setRefreshing(true)
    setActionError(null)
    try {
      await onRefresh(server!.id)
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
      await onDelete(server!.id)
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setDeleting(false)
      setConfirmDelete(false)
    }
  }

  async function handleReauthorize(): Promise<void> {
    setReauthorizing(true)
    setActionError(null)
    try {
      const result = await startOAuth(client, server!.id)
      persistOAuthOrigin()
      window.location.href = result.authorize_url
    } catch (err) {
      setActionError((err as Error).message)
      setReauthorizing(false)
    }
  }

  const needsReauth = server.auth_method === 'oauth' && !c.authed
  const busy = refreshing || deleting || reauthorizing

  return (
    <div className="flex w-full flex-col gap-4 p-6" data-testid="mcp-admin-detail-panel">
      {/* ── Hero ─────────────────────────────────────────────────────── */}
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
              {connected ? tDetail('statusConnected') : tDetail('statusDisconnected')}
            </span>
            <h1 className="truncate text-2xl font-semibold">{c.name}</h1>
            <MCPScopeBadge scope={server.credential_scope} />
            <span className="rounded-md border border-border px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
              {server.transport}
            </span>
            {c.kind === 'catalog' && c.provider ? (
              <Badge variant="outline" className="text-[11px]">
                {c.provider}
              </Badge>
            ) : null}
            {c.kind === 'custom' ? (
              <Badge variant="secondary" className="text-[11px]">
                {t('customBadge')}
              </Badge>
            ) : null}
          </div>
          <p className="text-sm text-muted-foreground">{metaLine}</p>
          {c.description ? (
            <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
              {c.description}
            </p>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {needsReauth ? (
            <Button
              type="button"
              variant="default"
              size="sm"
              disabled={busy}
              onClick={() => void handleReauthorize()}
              data-testid="mcp-admin-reauthorize-button"
            >
              {reauthorizing ? (
                <Loader2 data-icon="inline-start" className="animate-spin" />
              ) : (
                <KeyRound data-icon="inline-start" />
              )}
              {t('reauthorize')}
            </Button>
          ) : null}
          <Button
            type="button"
            variant={needsReauth ? 'outline' : 'default'}
            size="sm"
            disabled={busy}
            onClick={() => void handleRefresh()}
          >
            {refreshing ? (
              <Loader2 data-icon="inline-start" className="animate-spin" />
            ) : (
              <RefreshCw data-icon="inline-start" />
            )}
            {tDetail('refreshTools')}
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
              {t('deleteButton')}
            </Button>
          ) : (
            <div className="flex items-center gap-1.5 rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-1.5">
              <span className="text-xs text-destructive">{t('confirmDeleteLabel')}</span>
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

      {c.last_error ? <ServerErrorBanner error={c.last_error} /> : null}
      {actionError ? <p className="text-xs text-destructive">{actionError}</p> : null}

      {/* ── Tabs ───────────────────────────────────────────────────── */}
      <Tabs defaultValue="overview" className="flex-1 flex-col">
        <TabsList variant="line" className="w-full justify-start border-b border-border/60 pb-0">
          <TabsTrigger value="overview">
            <FileText className="size-3.5" />
            {t('tabOverview')}
          </TabsTrigger>
          <TabsTrigger value="tools">
            <Wrench className="size-3.5" />
            {t('tabTools', { count: toolCount })}
          </TabsTrigger>
          {isOrgWide && (
            <TabsTrigger value="workspaces">
              <Network className="size-3.5" />
              {t('tabWorkspaces')}
            </TabsTrigger>
          )}
          <TabsTrigger value="citations">{t('tabCitations')}</TabsTrigger>
        </TabsList>

        {/* Overview tab */}
        <TabsContent value="overview" className="mt-4">
          <OverviewPanel
            server={server}
            mode="admin"
            client={client}
            onRefresh={() => handleRefresh()}
          />
        </TabsContent>

        {/* Tools tab */}
        <TabsContent value="tools" className="mt-4">
          <ToolsPanel tools={server.tools_cache ?? []} />
        </TabsContent>

        {/* Workspaces tab (org-wide only) */}
        {isOrgWide && (
          <TabsContent value="workspaces" className="mt-4">
            <MCPWorkspacesTab serverId={server.id} client={client} />
          </TabsContent>
        )}

        {/* Citation mapping tab */}
        <TabsContent value="citations" className="mt-4">
          <MCPCitationMappingTab client={client} workspaceId={wsId} serverId={server.id} canEdit />
        </TabsContent>
      </Tabs>
    </div>
  )
}
