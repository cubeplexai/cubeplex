'use client'

import { useTranslations } from 'next-intl'
import { AlertTriangle, CheckCircle2, LockKeyhole, PauseCircle, Plug } from 'lucide-react'
import type { MCPConnectorFilter, MCPEffectiveConnector } from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

interface MCPConnectorListProps {
  connectors: MCPEffectiveConnector[]
  loading: boolean
  search: string
  filter: MCPConnectorFilter
  selectedId: string | null
  onSelect: (id: string) => void
}

function nameOf(c: MCPEffectiveConnector): string {
  return c.install.name || c.template?.name || c.install.install_id
}

function providerOf(c: MCPEffectiveConnector): string {
  return c.template?.provider ?? ''
}

function filterConnectors(
  connectors: MCPEffectiveConnector[],
  search: string,
  filter: MCPConnectorFilter,
): MCPEffectiveConnector[] {
  const q = search.trim().toLowerCase()

  return connectors
    .filter((c) => {
      const installed = c.install.install_state === 'active'
      if (filter === 'installed' && !installed) return false
      if (filter === 'available' && installed) return false
      if (filter === 'custom' && c.template !== null) return false
      if (q) {
        const haystack =
          `${nameOf(c)} ${providerOf(c)} ${c.template?.description ?? ''}`.toLowerCase()
        if (!haystack.includes(q)) return false
      }
      return true
    })
    .sort((a, b) => {
      const ai = a.install.install_state === 'active' ? 0 : 1
      const bi = b.install.install_state === 'active' ? 0 : 1
      if (ai !== bi) return ai - bi
      return nameOf(a).localeCompare(nameOf(b))
    })
}

interface StatusPillProps {
  connector: MCPEffectiveConnector
}

function StatusPill({ connector }: StatusPillProps) {
  const t = useTranslations('mcpAdmin')
  const ws = connector.workspace_state

  // Disconnected — install removed
  if (connector.install.install_state === 'uninstalled') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
        <PauseCircle className="size-3" />
        {t('statusUninstalled')}
      </span>
    )
  }

  if (!ws.enabled) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
        <PauseCircle className="size-3" />
        {t('statusWorkspaceDisabled')}
      </span>
    )
  }

  if (connector.usable) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
        <CheckCircle2 className="size-3" />
        {t('ready')}
      </span>
    )
  }

  if (connector.reason === 'pending_oauth' || connector.install.auth_status === 'pending_oauth') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
        <AlertTriangle className="size-3" />
        {t('statusPendingOAuth')}
      </span>
    )
  }

  if (connector.credential_availability === 'missing') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
        <LockKeyhole className="size-3" />
        {t('needsCredential')}
      </span>
    )
  }

  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
      <Plug className="size-3" />
      {t('statusAvailable')}
    </span>
  )
}

export function MCPConnectorList({
  connectors,
  loading,
  search,
  filter,
  selectedId,
  onSelect,
}: MCPConnectorListProps) {
  const t = useTranslations('mcpAdmin')

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center py-10 text-sm text-muted-foreground">
        {t('loading')}
      </div>
    )
  }

  const filtered = filterConnectors(connectors, search, filter)

  if (filtered.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-1 py-10 text-center">
        <p className="text-sm font-medium text-foreground">{t('noConnectors')}</p>
        <p className="text-xs text-muted-foreground">{t('noConnectorsHint')}</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-1.5 p-3">
      {filtered.map((c) => {
        const id = c.install.install_id
        const active = id === selectedId
        return (
          <button
            key={id}
            type="button"
            onClick={() => onSelect(id)}
            data-testid={`connector-card-${id}`}
            aria-current={active ? 'true' : undefined}
            className={cn(
              'group/connector-card flex w-full flex-col gap-1.5 rounded-lg border p-3 text-left transition-all',
              active
                ? 'border-primary/40 bg-primary/5 shadow-sm'
                : 'border-border/70 bg-card/40 hover:border-border hover:bg-accent/40',
            )}
          >
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-semibold">{nameOf(c)}</span>
              <StatusPill connector={c} />
            </div>
            {providerOf(c) && (
              <p className="truncate text-xs text-muted-foreground">{providerOf(c)}</p>
            )}
            <div className="flex flex-wrap items-center gap-1 pt-0.5">
              <Badge variant="outline" className="px-1.5 text-[10px]">
                {c.install.install_scope === 'org' ? t('scopeOrg') : t('scopeWorkspace')}
              </Badge>
              <Badge variant="outline" className="px-1.5 text-[10px]">
                {c.credential_policy}
              </Badge>
            </div>
          </button>
        )
      })}
    </div>
  )
}
