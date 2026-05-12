'use client'

import { AlertTriangle, CheckCircle2, Globe, LockKeyhole, Server } from 'lucide-react'
import { useTranslations } from 'next-intl'
import type { MCPAdminConnector } from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

interface MCPConnectorCardProps {
  connector: MCPAdminConnector
  active: boolean
  onClick: () => void
}

function StatusChip({ connector }: { connector: MCPAdminConnector }) {
  const t = useTranslations('mcpAdmin')

  if (!connector.installed) return null

  if (connector.last_error) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
        <AlertTriangle className="size-3" />
        {t('statusError')}
      </span>
    )
  }

  if (!connector.authed) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
        <LockKeyhole className="size-3" />
        {t('statusNotAuthed')}
      </span>
    )
  }

  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 transition-colors group-hover/connector-card:bg-emerald-500/20 dark:text-emerald-400">
      <CheckCircle2 className="size-3" />
      {t('statusInstalled')}
    </span>
  )
}

export function MCPConnectorCard({ connector, active, onClick }: MCPConnectorCardProps) {
  const t = useTranslations('mcpAdmin')
  const ConnectorIcon = connector.kind === 'catalog' ? Globe : Server

  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={`connector-card-${connector.id}`}
      aria-current={active ? 'true' : undefined}
      className={cn(
        'group/connector-card flex w-full flex-col gap-1.5 rounded-lg border p-3 text-left transition-all',
        active
          ? 'border-primary/40 bg-primary/5 shadow-sm'
          : 'border-border/70 bg-card/40 hover:border-border hover:bg-accent/40',
      )}
    >
      <div className="flex items-center gap-2">
        <ConnectorIcon
          className={cn(
            'size-3.5 shrink-0',
            connector.kind === 'catalog' ? 'text-primary' : 'text-muted-foreground',
          )}
        />
        <span className="truncate text-sm font-semibold">{connector.name}</span>
        <StatusChip connector={connector} />
      </div>

      {connector.provider && (
        <p className="truncate text-xs text-muted-foreground">{connector.provider}</p>
      )}

      <div className="flex flex-wrap items-center gap-1 pt-0.5">
        <Badge variant="outline" className="px-1.5 text-[10px]">
          {connector.transport === 'streamable_http' ? 'HTTP' : 'SSE'}
        </Badge>

        {connector.installed && connector.workspace_count > 0 && (
          <Badge variant="outline" className="px-1.5 text-[10px]">
            {t('workspaceCount', { count: connector.workspace_count })}
          </Badge>
        )}
      </div>
    </button>
  )
}
