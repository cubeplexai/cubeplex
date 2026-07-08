'use client'

import { useTranslations } from 'next-intl'
import { AlertTriangle, CheckCircle2, LockKeyhole, PauseCircle, Plug } from 'lucide-react'
import type { AdminOrgConnector, MCPConnectorFilter } from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

interface MCPConnectorListProps {
  connectors: AdminOrgConnector[]
  loading: boolean
  search: string
  filter: MCPConnectorFilter
  selectedId: string | null
  onSelect: (id: string) => void
}

function nameOf(c: AdminOrgConnector): string {
  return c.install.name || c.template?.name || c.install.connector_id
}

function providerOf(c: AdminOrgConnector): string {
  return c.template?.provider ?? ''
}

function filterConnectors(
  connectors: AdminOrgConnector[],
  search: string,
  filter: MCPConnectorFilter,
): AdminOrgConnector[] {
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
  connector: AdminOrgConnector
}

function StatusPill({ connector }: StatusPillProps) {
  const t = useTranslations('mcpAdmin')
  const eff = connector.org_effective

  // Disconnected — install removed
  if (connector.install.install_state === 'uninstalled') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
        <PauseCircle className="size-3" />
        {t('statusUninstalled')}
      </span>
    )
  }

  if (eff.usable) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-success-solid/10 px-1.5 py-0.5 text-[10px] font-medium text-success-fg">
        <CheckCircle2 className="size-3" />
        {t('ready')}
      </span>
    )
  }

  if (eff.reason === 'pending_oauth' || connector.install.auth_status === 'pending_oauth') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-warning-solid/10 px-1.5 py-0.5 text-[10px] font-medium text-warning-fg">
        <AlertTriangle className="size-3" />
        {t('statusPendingOAuth')}
      </span>
    )
  }

  if (eff.credential_availability === 'missing') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-warning-solid/10 px-1.5 py-0.5 text-[10px] font-medium text-warning-fg">
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
        const id = c.install.connector_id
        const active = id === selectedId
        const dist = c.workspace_distribution
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
              {providerOf(c) && providerOf(c).toLowerCase() !== nameOf(c).toLowerCase() ? (
                <Badge variant="outline" className="shrink-0 text-[10px]">
                  {providerOf(c)}
                </Badge>
              ) : null}
              <StatusPill connector={c} />
            </div>
            {c.template?.description ? (
              <p className="line-clamp-1 text-xs text-muted-foreground">{c.template.description}</p>
            ) : null}
            <div className="flex flex-wrap items-center gap-1 pt-0.5">
              <Badge variant="outline" className="px-1.5 text-[10px]">
                {c.install.install_scope === 'org' ? t('scopeOrg') : t('scopeWorkspace')}
              </Badge>
              <Badge variant="outline" className="px-1.5 text-[10px]">
                {c.install.default_credential_policy}
              </Badge>
              <span
                className="text-[10px] tabular-nums text-muted-foreground"
                title={t('workspaceStateSummary', {
                  enabled: dist.enabled_count,
                  total: dist.eligible_count,
                })}
              >
                {dist.enabled_count}/{dist.eligible_count}
              </span>
            </div>
          </button>
        )
      })}
    </div>
  )
}
