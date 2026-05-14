'use client'

import { useState } from 'react'
import type { ApiClient, MCPServer } from '@cubebox/core'
import { useTranslations } from 'next-intl'

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

import { MCPPromoteDialog } from './MCPPromoteDialog'
import { OverviewPanel } from './detail/OverviewPanel'
import { ServerErrorBanner } from './detail/ServerErrorBanner'
import { ServerHero } from './detail/ServerHero'
import { ToolsPanel } from './detail/tools/ToolsPanel'

export interface MCPServerDetailProps {
  server: MCPServer
  mode: 'admin' | 'ws-owned' | 'ws-readonly'
  client: ApiClient
  wsId?: string
  onRefresh: () => Promise<void>
  onDelete?: () => Promise<void>
  onPromote?: (shareCredential: boolean) => Promise<void>
}

export function MCPServerDetail({
  server,
  mode,
  client,
  wsId,
  onRefresh,
  onDelete,
  onPromote,
}: MCPServerDetailProps) {
  const t = useTranslations('mcp.detail')
  const [refreshing, setRefreshing] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [promoteOpen, setPromoteOpen] = useState(false)

  const canRefreshTools = mode !== 'ws-readonly'
  const canShare = mode === 'ws-owned' && Boolean(onPromote)
  const canDelete = Boolean(onDelete)
  const tools = server.tools_cache ?? []

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
      <ServerHero
        server={server}
        canRefresh={canRefreshTools}
        canShare={canShare}
        canDelete={canDelete}
        refreshing={refreshing}
        deleting={deleting}
        onRefresh={() => void handleRefresh()}
        onShare={() => setPromoteOpen(true)}
        onDelete={() => void handleDelete()}
      />

      {server.last_error ? <ServerErrorBanner error={server.last_error} /> : null}

      <Tabs defaultValue="overview">
        <TabsList variant="line" className="w-full justify-start border-b border-border/60 pb-0">
          <TabsTrigger value="overview">{t('overview')}</TabsTrigger>
          <TabsTrigger value="tools">{t('toolsTab', { count: tools.length })}</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="mt-4">
          <OverviewPanel
            server={server}
            mode={mode}
            client={client}
            wsId={wsId}
            onRefresh={onRefresh}
          />
        </TabsContent>

        <TabsContent value="tools" className="mt-4">
          <ToolsPanel tools={tools} />
        </TabsContent>
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
