'use client'

import type { MCPServer } from '@cubebox/core'
import { Loader2, RefreshCw, Share2, Trash2 } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

import { MCPScopeBadge } from '../MCPScopeBadge'

export interface ServerHeroProps {
  server: MCPServer
  canRefresh: boolean
  canShare: boolean
  canDelete: boolean
  refreshing: boolean
  deleting: boolean
  onRefresh: () => void
  onShare: () => void
  onDelete: () => void
}

export function ServerHero({
  server,
  canRefresh,
  canShare,
  canDelete,
  refreshing,
  deleting,
  onRefresh,
  onShare,
  onDelete,
}: ServerHeroProps) {
  const t = useTranslations('mcp.detail')
  const connected = server.authed
  const toolsCount = server.tools_cache?.length ?? 0
  const formattedTime = server.last_discovered_at
    ? new Date(server.last_discovered_at).toLocaleString()
    : null
  const toolsLabel = t('toolsCount', { count: toolsCount })
  const metaLine = formattedTime
    ? t('metaLine', { time: formattedTime, tools: toolsLabel })
    : t('metaLineNever', { tools: toolsLabel })

  return (
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
            {connected ? t('statusConnected') : t('statusDisconnected')}
          </span>
          <h1 className="truncate text-2xl font-semibold">{server.name}</h1>
          <MCPScopeBadge scope={server.credential_scope} />
          <span className="rounded-md border border-border px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
            {server.transport}
          </span>
        </div>
        <p className="text-sm text-muted-foreground">{metaLine}</p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {canRefresh ? (
          <Button
            type="button"
            variant="default"
            size="sm"
            disabled={refreshing || deleting}
            onClick={onRefresh}
          >
            {refreshing ? (
              <Loader2 data-icon="inline-start" className="animate-spin" />
            ) : (
              <RefreshCw data-icon="inline-start" />
            )}
            {t('refreshTools')}
          </Button>
        ) : null}
        {canShare ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={refreshing || deleting}
            onClick={onShare}
          >
            <Share2 data-icon="inline-start" />
            {t('shareToOrg')}
          </Button>
        ) : null}
        {canDelete ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={refreshing || deleting}
            onClick={onDelete}
            className="text-destructive hover:bg-destructive/10 hover:text-destructive"
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
  )
}
