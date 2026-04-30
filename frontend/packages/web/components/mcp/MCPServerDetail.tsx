'use client'

import { useState } from 'react'
import type { ApiClient, MCPServer } from '@cubebox/core'
import { CircleDot, Loader2, RefreshCw, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'

import { MCPBindingGrid, type MCPWorkspaceOption } from './MCPBindingGrid'
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

function formatDate(value: string | null): string {
  if (!value) return 'not discovered yet'
  return new Date(value).toLocaleString()
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
  const [refreshing, setRefreshing] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [promoteOpen, setPromoteOpen] = useState(false)
  const showBindingsTab = mode === 'admin' && server.owner_workspace_id === null
  const showCredentialPanel = (mode === 'ws-owned' || mode === 'ws-readonly') && wsId

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
            {server.transport} · last discovered {formatDate(server.last_discovered_at)}
          </p>
          {server.last_error ? (
            <p className="max-w-3xl text-sm text-destructive">{server.last_error}</p>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-2">
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
            Refresh tools
          </Button>
          {mode === 'ws-owned' && onPromote ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={refreshing || deleting}
              onClick={() => setPromoteOpen(true)}
            >
              Share to org
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
              Delete
            </Button>
          ) : null}
        </div>
      </div>

      <Tabs defaultValue="overview">
        <TabsList variant="line" className="w-full justify-start border-b border-border/60 pb-0">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="tools">Tools ({server.tools_cache?.length ?? 0})</TabsTrigger>
          {showBindingsTab ? <TabsTrigger value="bindings">Workspaces</TabsTrigger> : null}
        </TabsList>

        <TabsContent value="overview" className="mt-4 flex flex-col gap-4">
          <Card>
            <CardHeader>
              <CardTitle>Server details</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2 text-sm">
              <InfoRow label="URL">{server.server_url}</InfoRow>
              <InfoRow label="Transport">{server.transport}</InfoRow>
              <InfoRow label="Auth method">{server.auth_method}</InfoRow>
              <InfoRow label="Credential scope">{server.credential_scope}</InfoRow>
              <InfoRow label="Timeout">
                {server.timeout}s / SSE {server.sse_read_timeout}s
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

        {showBindingsTab && workspaces ? (
          <TabsContent value="bindings" className="mt-4">
            <MCPBindingGrid client={client} serverId={server.id} workspaces={workspaces} />
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
