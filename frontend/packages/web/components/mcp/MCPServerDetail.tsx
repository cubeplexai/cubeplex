'use client'

import { useState } from 'react'
import type { ApiClient, MCPServer } from '@cubebox/core'
import { CircleDot, Loader2, RefreshCw, Trash2 } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'

import { MCPOverrideGrid, type MCPWorkspaceOption } from './MCPOverrideGrid'
import { MCPCredentialPanel } from './MCPCredentialPanel'
import { MCPPromoteDialog } from './MCPPromoteDialog'
import { MCPScopeBadge } from './MCPScopeBadge'
import { MCPToolsTable } from './MCPToolsTable'

export interface MCPServerDetailProps {
  server: MCPServer
  mode: 'admin' | 'ws-owned' | 'ws-readonly'
  client: ApiClient
  wsId?: string
  workspaces?: MCPWorkspaceOption[]
  onRefresh: () => Promise<void>
  onDelete?: () => Promise<void>
  onPromote?: (shareCredential: boolean) => Promise<void>
}

function InfoRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg bg-muted/30 p-3 sm:flex-row sm:items-center sm:justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium break-all">{children}</span>
    </div>
  )
}

export function MCPServerDetail({
  server,
  mode,
  client,
  wsId,
  workspaces,
  onRefresh,
  onDelete,
  onPromote,
}: MCPServerDetailProps) {
  const t = useTranslations('mcp.detail')
  const [refreshing, setRefreshing] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [promoteOpen, setPromoteOpen] = useState(false)
  const showOverridesTab = mode === 'admin' && server.owner_workspace_id === null
  const showCredentialPanel = (mode === 'ws-owned' || mode === 'ws-readonly') && wsId
  const canRefreshTools = mode !== 'ws-readonly'

  const formattedTime = server.last_discovered_at
    ? new Date(server.last_discovered_at).toLocaleString()
    : t('notDiscoveredYet')

  async function handleRefresh(): Promise<void> {
    setRefreshing(true)
    try {
      await onRefresh()
    } finally {
      setRefreshing(false)
    }
  }

  async function handleDelete(): Promise<void> {
    if (!onDelete) return
    setDeleting(true)
    try {
      await onDelete()
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-4 rounded-xl border border-border bg-card p-4 text-card-foreground sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 flex-col gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <CircleDot
              className={cn(server.authed ? 'text-primary' : 'text-destructive')}
              aria-hidden="true"
            />
            <h1 className="truncate text-2xl font-semibold">{server.name}</h1>
            <MCPScopeBadge scope={server.credential_scope} />
          </div>
          <p className="text-sm text-muted-foreground">
            {t('lastDiscoveredLine', { transport: server.transport, time: formattedTime })}
          </p>
          {server.last_error ? (
            <p className="max-w-3xl text-sm text-destructive">{server.last_error}</p>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {canRefreshTools ? (
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
          ) : null}
          {mode === 'ws-owned' && onPromote ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={refreshing || deleting}
              onClick={() => setPromoteOpen(true)}
            >
              {t('shareToOrg')}
            </Button>
          ) : null}
          {onDelete ? (
            <Button
              type="button"
              variant="destructive"
              size="sm"
              disabled={refreshing || deleting}
              onClick={() => void handleDelete()}
            >
              {deleting ? (
                <Loader2 data-icon="inline-start" className="animate-spin" />
              ) : (
                <Trash2 data-icon="inline-start" />
              )}
              {t('delete')}
            </Button>
          ) : null}
        </div>
      </div>

      <Tabs defaultValue="overview">
        <TabsList variant="line" className="w-full justify-start border-b border-border/60 pb-0">
          <TabsTrigger value="overview">{t('overview')}</TabsTrigger>
          <TabsTrigger value="tools">
            {t('toolsTab', { count: server.tools_cache?.length ?? 0 })}
          </TabsTrigger>
          {showOverridesTab ? <TabsTrigger value="overrides">{t('workspaces')}</TabsTrigger> : null}
        </TabsList>

        <TabsContent value="overview" className="mt-4 flex flex-col gap-4">
          <Card>
            <CardHeader>
              <CardTitle>{t('serverDetails')}</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2 text-sm">
              <InfoRow label={t('url')}>{server.server_url}</InfoRow>
              <InfoRow label={t('transport')}>{server.transport}</InfoRow>
              <InfoRow label={t('authMethod')}>{server.auth_method}</InfoRow>
              <InfoRow label={t('credentialScope')}>{server.credential_scope}</InfoRow>
              <InfoRow label={t('timeout')}>
                {t('timeoutValue', {
                  timeout: server.timeout,
                  sseTimeout: server.sse_read_timeout,
                })}
              </InfoRow>
            </CardContent>
          </Card>

          {showCredentialPanel ? (
            <MCPCredentialPanel
              server={server}
              wsId={wsId}
              client={client}
              scopeContext={mode === 'ws-owned' ? 'owned' : 'via-binding'}
              onChange={onRefresh}
            />
          ) : null}
        </TabsContent>

        <TabsContent value="tools" className="mt-4">
          <MCPToolsTable tools={server.tools_cache ?? []} />
        </TabsContent>

        {showOverridesTab && workspaces ? (
          <TabsContent value="overrides" className="mt-4">
            <MCPOverrideGrid client={client} serverId={server.id} workspaces={workspaces} />
          </TabsContent>
        ) : null}
      </Tabs>

      {onPromote ? (
        <MCPPromoteDialog
          server={server}
          open={promoteOpen}
          onOpenChange={setPromoteOpen}
          onConfirm={onPromote}
        />
      ) : null}
    </div>
  )
}
