'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Globe, Plug, Plus } from 'lucide-react'
import { createApiClient, useWorkspaceSettingsStore } from '@cubebox/core'
import type { MCPServerItem } from '@cubebox/core'
import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'

interface McpPanelProps {
  wsId: string
}

function McpItemCard({
  srv,
  active,
  toggling,
  onClick,
  onToggle,
}: {
  srv: MCPServerItem
  active: boolean
  toggling: boolean
  onClick: () => void
  onToggle: (enabled: boolean) => void
}) {
  const t = useTranslations('mcp.wsPanel')
  const SourceIcon = srv.scope === 'workspace' ? Plug : Globe
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? 'true' : undefined}
      className={cn(
        'group flex w-full flex-col gap-1.5 rounded-lg border p-3 text-left transition-all',
        active
          ? 'border-primary/40 bg-primary/5 shadow-sm'
          : 'border-border/70 bg-card/40 hover:border-border hover:bg-accent/40',
      )}
    >
      <div className="flex items-center gap-2">
        <SourceIcon
          className={cn(
            'size-3.5 shrink-0',
            srv.scope === 'workspace' ? 'text-muted-foreground' : 'text-primary',
          )}
        />
        <span className="truncate text-sm font-semibold">{srv.name}</span>
        {srv.enabled && (
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="size-3" />
            {t('onBadge')}
          </span>
        )}
        <Switch
          checked={srv.enabled}
          disabled={srv.scope === 'workspace' || toggling}
          onCheckedChange={onToggle}
          onClick={(e) => e.stopPropagation()}
          className="ml-auto shrink-0 scale-75"
        />
      </div>
      <p className="line-clamp-1 truncate text-xs text-muted-foreground">{srv.server_url}</p>
      <div className="flex items-center gap-1 pt-0.5">
        <Badge variant="outline" className="px-1.5 text-[10px]">
          {srv.transport}
        </Badge>
        <Badge variant="outline" className="px-1.5 text-[10px]">
          {srv.scope === 'workspace' ? t('workspaceLabel') : t('orgLabel')}
        </Badge>
      </div>
    </button>
  )
}

export function McpPanel({ wsId }: McpPanelProps) {
  const t = useTranslations('mcp.wsPanel')
  const { mcp, loading, loadAll, toggleMCP } = useWorkspaceSettingsStore()
  const [selected, setSelected] = useState<MCPServerItem | null>(null)
  const [toggling, setToggling] = useState<string | null>(null)

  const client = useCallback(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  useEffect(() => {
    if (!mcp) loadAll(client())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wsId])

  const orgServers = mcp?.org_servers ?? []
  const workspaceServers = mcp?.workspace_servers ?? []
  const allServers = [...orgServers, ...workspaceServers]
  const enabledCount = allServers.filter((s) => s.enabled).length

  async function handleToggle(srv: MCPServerItem, enabled: boolean): Promise<void> {
    if (srv.scope === 'workspace') return
    setToggling(srv.server_id)
    try {
      await toggleMCP(client(), srv.server_id, enabled)
    } finally {
      setToggling(null)
    }
  }

  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden">
      <header className="flex items-center justify-between gap-2 border-b border-border/70 px-6 py-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">{t('title')}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {t('summary', { enabled: enabledCount, total: allServers.length })}
          </p>
        </div>
        <Link href={`/w/${wsId}/integrations/mcp/new`}>
          <Button size="sm">
            <Plus className="size-3.5" />
            {t('addConnector')}
          </Button>
        </Link>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <aside
          aria-label={t('listAria')}
          className="w-[320px] shrink-0 overflow-y-auto border-r border-border/70 bg-card/20"
        >
          {loading && !mcp ? (
            <p className="px-4 py-6 text-center text-xs text-muted-foreground">{t('loading')}</p>
          ) : allServers.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-1 px-6 text-center">
              <p className="text-sm text-muted-foreground">{t('empty')}</p>
              <p className="text-xs text-muted-foreground/70">{t('emptyHint')}</p>
            </div>
          ) : (
            <div className="flex flex-col gap-3 p-3">
              {orgServers.length > 0 && (
                <section className="flex flex-col gap-1.5">
                  <p className="px-1 text-[10px] font-medium uppercase tracking-widest text-muted-foreground/60">
                    {t('orgWide')}
                  </p>
                  <ul className="flex flex-col gap-1.5">
                    {orgServers.map((srv) => (
                      <li key={srv.server_id}>
                        <McpItemCard
                          srv={srv}
                          active={selected?.server_id === srv.server_id}
                          toggling={toggling === srv.server_id}
                          onClick={() => setSelected(srv)}
                          onToggle={(v) => void handleToggle(srv, v)}
                        />
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {workspaceServers.length > 0 && (
                <section className="flex flex-col gap-1.5">
                  <p className="px-1 text-[10px] font-medium uppercase tracking-widest text-muted-foreground/60">
                    {t('workspacePrivate')}
                  </p>
                  <ul className="flex flex-col gap-1.5">
                    {workspaceServers.map((srv) => (
                      <li key={srv.server_id}>
                        <McpItemCard
                          srv={srv}
                          active={selected?.server_id === srv.server_id}
                          toggling={toggling === srv.server_id}
                          onClick={() => setSelected(srv)}
                          onToggle={(v) => void handleToggle(srv, v)}
                        />
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </div>
          )}
        </aside>

        <section className="flex flex-1 overflow-y-auto">
          {selected ? (
            <div className="flex w-full flex-col gap-4 p-6">
              <header className="flex flex-col gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-xl font-semibold tracking-tight">{selected.name}</h3>
                  <Badge variant="outline">{selected.transport}</Badge>
                  <Badge variant={selected.scope === 'workspace' ? 'secondary' : 'default'}>
                    {selected.scope === 'workspace' ? t('workspaceLabel') : t('orgLabel')}
                  </Badge>
                  <Badge
                    variant="outline"
                    className={cn(
                      selected.enabled
                        ? 'border-emerald-500/40 text-emerald-600'
                        : 'text-muted-foreground',
                    )}
                  >
                    {selected.enabled ? t('enabledLabel') : t('disabledLabel')}
                  </Badge>
                  {selected.scope === 'workspace' && (
                    <Link
                      href={`/w/${wsId}/integrations/mcp/${selected.server_id}`}
                      className="ml-auto text-xs text-primary hover:underline"
                    >
                      {t('manage')}
                    </Link>
                  )}
                </div>
              </header>

              <div className="rounded-lg border border-border/70 bg-card/40 p-4">
                <dl className="grid grid-cols-[140px_1fr] gap-y-2 text-sm">
                  <dt className="text-muted-foreground">{t('serverUrl')}</dt>
                  <dd className="break-all font-mono text-xs">{selected.server_url}</dd>
                  <dt className="text-muted-foreground">{t('transport')}</dt>
                  <dd>{selected.transport}</dd>
                  <dt className="text-muted-foreground">{t('scope')}</dt>
                  <dd>{selected.scope}</dd>
                  <dt className="text-muted-foreground">{t('enabled')}</dt>
                  <dd>{selected.enabled ? t('yes') : t('no')}</dd>
                </dl>
              </div>
            </div>
          ) : (
            <div className="flex flex-1 items-center justify-center p-8 text-sm text-muted-foreground">
              {t('selectConnector')}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
