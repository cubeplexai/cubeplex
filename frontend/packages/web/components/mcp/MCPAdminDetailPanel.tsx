'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Check, FileText, Loader2, Network, RefreshCw, Trash2, Wrench, X } from 'lucide-react'
import type { ApiClient, MCPAdminConnector } from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

import { MCPToolsTable } from './MCPToolsTable'
import { MCPWorkspacesTab } from './MCPWorkspacesTab'

interface MCPAdminDetailPanelProps {
  connector: MCPAdminConnector | null
  mode: 'detail' | 'add_custom' | null
  client: ApiClient
  onRefresh: (id: string) => Promise<void>
  onDelete: (id: string) => Promise<void>
}

function InfoRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div
      className="flex flex-col gap-1 rounded-lg bg-muted/30 p-3
        sm:flex-row sm:items-center sm:justify-between"
    >
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium break-all">{children}</span>
    </div>
  )
}

export function MCPAdminDetailPanel({
  connector,
  mode,
  client,
  onRefresh,
  onDelete,
}: MCPAdminDetailPanelProps) {
  const t = useTranslations('mcpAdmin')

  const [refreshing, setRefreshing] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

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
    return (
      <div
        className="flex flex-1 items-center justify-center p-8 text-sm
          text-muted-foreground"
      >
        {t('addCustomPlaceholder')}
      </div>
    )
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

  return (
    <div className="flex w-full flex-col gap-4 p-6" data-testid="mcp-admin-detail-panel">
      {/* ── Header ─────────────────────────────────────────────────── */}
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-xl font-semibold tracking-tight">{c.name}</h3>

          {c.kind === 'catalog' && c.provider && (
            <Badge variant="outline" className="text-[11px]">
              {c.provider}
            </Badge>
          )}

          {c.authed ? (
            <Badge variant="outline" className="border-emerald-500/40 text-[11px] text-emerald-600">
              {t('authenticated')}
            </Badge>
          ) : (
            <Badge variant="outline" className="border-amber-500/40 text-[11px] text-amber-600">
              {t('notAuthenticated')}
            </Badge>
          )}

          {c.kind === 'custom' && (
            <Badge variant="secondary" className="text-[11px]">
              {t('customBadge')}
            </Badge>
          )}

          {/* Spacer + action buttons */}
          <div className="ml-auto flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={refreshing || deleting}
              onClick={() => void handleRefresh()}
            >
              {refreshing ? (
                <Loader2 data-icon="inline-start" className="animate-spin" />
              ) : (
                <RefreshCw data-icon="inline-start" />
              )}
              {t('refreshTools')}
            </Button>

            {!confirmDelete ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="text-destructive hover:bg-destructive/10
                  hover:text-destructive"
                disabled={refreshing || deleting}
                onClick={() => setConfirmDelete(true)}
              >
                <Trash2 data-icon="inline-start" />
                {t('deleteButton')}
              </Button>
            ) : (
              <div
                className="flex items-center gap-1.5 rounded-md border
                  border-destructive/30 bg-destructive/5 px-2.5 py-1.5"
              >
                <span className="text-xs text-destructive">{t('confirmDeleteLabel')}</span>
                <button
                  type="button"
                  className="cursor-pointer rounded p-0.5 text-destructive
                    hover:bg-destructive/20"
                  disabled={deleting}
                  onClick={() => void handleDelete()}
                >
                  <Check className="size-3.5" />
                </button>
                <button
                  type="button"
                  className="cursor-pointer rounded p-0.5 text-muted-foreground
                    hover:bg-muted"
                  onClick={() => setConfirmDelete(false)}
                >
                  <X className="size-3.5" />
                </button>
              </div>
            )}
          </div>
        </div>

        {c.description && (
          <p className="text-sm leading-relaxed text-muted-foreground">{c.description}</p>
        )}
        {c.last_error && <p className="text-sm text-destructive">{c.last_error}</p>}
        {actionError && <p className="text-xs text-destructive">{actionError}</p>}
      </header>

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
        </TabsList>

        {/* Overview tab */}
        <TabsContent value="overview" className="mt-4 flex flex-col gap-4">
          <Card>
            <CardHeader>
              <CardTitle>{t('overviewServerDetails')}</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2 text-sm">
              <InfoRow label={t('overviewUrl')}>{server.server_url}</InfoRow>
              <InfoRow label={t('overviewTransport')}>{server.transport}</InfoRow>
              <InfoRow label={t('overviewAuthMethod')}>{server.auth_method}</InfoRow>
              <InfoRow label={t('overviewCredentialScope')}>{server.credential_scope}</InfoRow>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>{t('overviewOrgCredential')}</CardTitle>
            </CardHeader>
            <CardContent className="text-sm">
              {server.authed ? (
                <Badge variant="outline" className="border-emerald-500/40 text-emerald-600">
                  {t('authenticated')}
                </Badge>
              ) : (
                <Badge variant="outline" className="border-amber-500/40 text-amber-600">
                  {t('notAuthenticated')}
                </Badge>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Tools tab */}
        <TabsContent value="tools" className="mt-4">
          <MCPToolsTable tools={server.tools_cache ?? []} />
        </TabsContent>

        {/* Workspaces tab (org-wide only) */}
        {isOrgWide && (
          <TabsContent value="workspaces" className="mt-4">
            <MCPWorkspacesTab serverId={server.id} client={client} />
          </TabsContent>
        )}
      </Tabs>
    </div>
  )
}
