'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowUpCircle,
  Check,
  CheckCircle2,
  Loader2,
  PauseCircle,
  Plug,
  Trash2,
  Wrench,
  X,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import {
  adminPromoteToOrg,
  createApiClient,
  useOrgAdminFlag,
  useWorkspaceStore,
  wsListAvailable,
  wsListEffectiveConnectors,
  wsDeleteInstall,
  wsPatchConnectorState,
  wsRefreshDiscovery,
  type MCPCredentialScope,
  type MCPEffectiveConnector,
  type PromoteDistribution,
  type WsAvailable,
} from '@cubebox/core'

import { AvailableConnectorRow } from '@/components/mcp/AvailableConnectorRow'
import { WsAuthBand } from '@/components/mcp/WsAuthBand'
import { ServerErrorBanner } from '@/components/mcp/detail/ServerErrorBanner'
import { WsToolsPanel } from '@/components/mcp/detail/tools/WsToolsPanel'
import { MCPPromoteDialog } from '@/components/mcp/MCPPromoteDialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ListDetailLayout } from '@/components/shared/ListDetailLayout'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'

interface McpPanelProps {
  wsId: string
}

type RowStatus = 'ready' | 'needsCredential' | 'pendingOAuth' | 'workspaceDisabled' | 'uninstalled'

function statusOf(c: MCPEffectiveConnector): RowStatus {
  if (c.install.install_state === 'uninstalled') return 'uninstalled'
  if (!c.workspace_state?.enabled) return 'workspaceDisabled'
  if (c.reason === 'pending_oauth' || c.install.auth_status === 'pending_oauth') {
    return 'pendingOAuth'
  }
  if (c.credential_availability === 'missing') return 'needsCredential'
  return 'ready'
}

function StatusPill({ status }: { status: RowStatus }) {
  const t = useTranslations('mcpAdmin')
  if (status === 'ready') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-success-solid/10 px-1.5 py-0.5 text-[10px] font-medium text-success-fg">
        <CheckCircle2 className="size-3" />
        {t('ready')}
      </span>
    )
  }
  if (status === 'needsCredential') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-warning-solid/10 px-1.5 py-0.5 text-[10px] font-medium text-warning-fg">
        <AlertTriangle className="size-3" />
        {t('needsCredential')}
      </span>
    )
  }
  if (status === 'pendingOAuth') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-warning-solid/10 px-1.5 py-0.5 text-[10px] font-medium text-warning-fg">
        <AlertTriangle className="size-3" />
        {t('statusPendingOAuth')}
      </span>
    )
  }
  if (status === 'workspaceDisabled') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
        <PauseCircle className="size-3" />
        {t('statusWorkspaceDisabled')}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
      <PauseCircle className="size-3" />
      {t('statusUninstalled')}
    </span>
  )
}

function ConnectorRow({
  connector,
  active,
  onClick,
}: {
  connector: MCPEffectiveConnector
  active: boolean
  onClick: () => void
}) {
  const t = useTranslations('mcpAdmin')
  const name = connector.install.name || connector.template?.name || connector.install.connector_id
  const provider = connector.template?.provider ?? ''
  const description = connector.template?.description ?? ''
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? 'true' : undefined}
      data-testid={`ws-connector-row-${connector.install.connector_id}`}
      className={cn(
        'group flex w-full flex-col gap-1.5 rounded-lg border p-3 text-left transition-all',
        active
          ? 'border-primary/40 bg-primary/5 shadow-sm'
          : 'border-border/70 bg-card/40 hover:border-border hover:bg-accent/40',
      )}
    >
      <div className="flex items-center gap-2">
        <Plug className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="truncate text-sm font-semibold">{name}</span>
        {provider && provider.toLowerCase() !== name.toLowerCase() ? (
          <Badge variant="outline" className="shrink-0 text-[10px]">
            {provider}
          </Badge>
        ) : null}
        <span className="ml-auto shrink-0">
          <StatusPill status={statusOf(connector)} />
        </span>
      </div>
      {description ? (
        <p className="line-clamp-1 text-xs text-muted-foreground">{description}</p>
      ) : null}
      <div className="flex flex-wrap items-center gap-1 pt-0.5">
        <Badge variant="outline" className="px-1.5 text-[10px]">
          {connector.install.install_scope === 'org' ? t('scopeOrg') : t('scopeWorkspace')}
        </Badge>
        <Badge variant="outline" className="px-1.5 text-[10px]">
          {connector.credential_policy}
        </Badge>
      </div>
    </button>
  )
}

function ConnectorDetail({
  connector,
  wsId,
  onChanged,
}: {
  connector: MCPEffectiveConnector
  wsId: string
  onChanged: () => Promise<void>
}) {
  const t = useTranslations('mcpAdmin')
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmUninstall, setConfirmUninstall] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const wsState = connector.workspace_state
  const install = connector.install
  const connectorId = install.connector_id

  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  const wsRole = useWorkspaceStore((s) => s.workspaces.find((w) => w.id === wsId)?.role)
  const callerRole: 'admin' | 'member' = wsRole === 'admin' ? 'admin' : 'member'

  const orgId = useWorkspaceStore((s) => s.workspaces.find((w) => w.id === wsId)?.org_id ?? null)
  const isOrgAdmin = useOrgAdminFlag(orgId)
  const canPromote = install.install_scope === 'workspace' && isOrgAdmin
  const canUninstall = install.install_scope === 'workspace'
  const [promoteOpen, setPromoteOpen] = useState(false)

  async function handlePromote(distribution: PromoteDistribution): Promise<void> {
    await adminPromoteToOrg(client, connectorId, distribution)
    await onChanged()
  }

  async function handleRefresh(): Promise<void> {
    setRefreshing(true)
    setError(null)
    try {
      await wsRefreshDiscovery(client, wsId, connectorId)
      await onChanged()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setRefreshing(false)
    }
  }

  async function handleUninstall(): Promise<void> {
    setDeleting(true)
    setError(null)
    try {
      await wsDeleteInstall(client, wsId, connectorId)
      await onChanged()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setDeleting(false)
      setConfirmUninstall(false)
    }
  }

  const discoveryError = install.discovery_status === 'error'
  const busy = saving || refreshing || deleting

  async function toggle(): Promise<void> {
    setSaving(true)
    setError(null)
    try {
      await wsPatchConnectorState(client, wsId, connectorId, { enabled: !wsState?.enabled })
      await onChanged()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  async function changePolicy(next: MCPCredentialScope): Promise<void> {
    setSaving(true)
    setError(null)
    try {
      await wsPatchConnectorState(client, wsId, connectorId, { credential_policy: next })
      await onChanged()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex w-full flex-col gap-4 p-6">
      {discoveryError ? (
        <ServerErrorBanner
          error={install.last_error ?? 'Discovery failed.'}
          onRetry={() => void handleRefresh()}
          retrying={refreshing}
        />
      ) : null}
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-xl font-semibold tracking-tight">
            {install.name || connector.template?.name || connectorId}
          </h3>
          <StatusPill status={statusOf(connector)} />
          {canPromote ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="ml-auto"
              disabled={busy}
              onClick={() => setPromoteOpen(true)}
              data-testid="mcp-promote-menu-item"
            >
              <ArrowUpCircle className="mr-1.5 size-3.5" />
              {t('promoteToOrg')}
            </Button>
          ) : null}
          <Button
            type="button"
            size="sm"
            variant="outline"
            className={canPromote ? undefined : 'ml-auto'}
            disabled={busy}
            onClick={() => void handleRefresh()}
          >
            {refreshing ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : null}
            {t('refreshTools')}
          </Button>
          {canUninstall && !confirmUninstall ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="text-destructive hover:bg-destructive/10 hover:text-destructive"
              disabled={busy}
              onClick={() => setConfirmUninstall(true)}
            >
              <Trash2 className="mr-1.5 size-3.5" />
              {t('uninstallButton')}
            </Button>
          ) : null}
          {canUninstall && confirmUninstall ? (
            <div className="flex items-center gap-1.5 rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-1.5">
              <span className="text-xs text-destructive">{t('confirmUninstallLabel')}</span>
              <button
                type="button"
                aria-label="Confirm uninstall"
                className="cursor-pointer rounded p-0.5 text-destructive hover:bg-destructive/20"
                disabled={deleting}
                onClick={() => void handleUninstall()}
              >
                <Check className="size-3.5" />
              </button>
              <button
                type="button"
                aria-label="Cancel uninstall"
                className="cursor-pointer rounded p-0.5 text-muted-foreground hover:bg-muted"
                disabled={deleting}
                onClick={() => setConfirmUninstall(false)}
              >
                <X className="size-3.5" />
              </button>
            </div>
          ) : null}
        </div>
        {connector.template?.description && (
          <p className="text-sm text-muted-foreground">{connector.template.description}</p>
        )}
      </header>

      <WsAuthBand
        connector={connector}
        client={client}
        wsId={wsId}
        callerRole={callerRole}
        onChanged={onChanged}
      />

      <Tabs defaultValue="overview" className="flex-1 flex-col">
        <TabsList variant="line" className="w-full justify-start border-b border-border/60 pb-0">
          <TabsTrigger value="overview">{t('tabOverview')}</TabsTrigger>
          <TabsTrigger value="tools">
            <Wrench className="size-3.5" />
            {t('tabTools', { count: install.tool_count })}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="mt-4 flex flex-col gap-4">
          <div className="rounded-lg border border-border/70 bg-card/40 p-4">
            <h4 className="mb-3 text-sm font-semibold">{t('workspaceState')}</h4>
            <div className="flex items-center justify-between gap-3 text-sm">
              <span>{wsState?.enabled ? t('wsEnabled') : t('wsDisabled')}</span>
              <Button
                size="sm"
                variant={wsState?.enabled ? 'outline' : 'default'}
                disabled={saving}
                onClick={() => void toggle()}
              >
                {wsState?.enabled ? 'Disconnect' : 'Connect'}
              </Button>
            </div>
            {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
          </div>

          <div className="rounded-lg border border-border/70 bg-card/40 p-4">
            <h4 className="mb-3 text-sm font-semibold">{t('credentialPolicy')}</h4>
            <div className="flex flex-wrap gap-2">
              {(['org', 'workspace', 'user', 'none'] as MCPCredentialScope[]).map((p) => (
                <Button
                  key={p}
                  size="sm"
                  variant={connector.credential_policy === p ? 'default' : 'outline'}
                  disabled={saving}
                  onClick={() => void changePolicy(p)}
                >
                  {p}
                </Button>
              ))}
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              {t('credentialAvailability')}: {connector.credential_availability}
              {connector.credential_source ? ` (${connector.credential_source})` : ''}
            </p>
          </div>
        </TabsContent>

        <TabsContent value="tools" className="mt-4">
          <WsToolsPanel
            tools={install.tools}
            connectorId={connectorId}
            client={client}
            wsId={wsId}
          />
        </TabsContent>
      </Tabs>

      <MCPPromoteDialog
        install={install}
        open={promoteOpen}
        onOpenChange={setPromoteOpen}
        onConfirm={handlePromote}
      />
    </div>
  )
}

export function McpPanel({ wsId }: McpPanelProps) {
  const t = useTranslations('mcpAdmin')
  const tAvailable = useTranslations('mcp.available')
  const tMcp = useTranslations('mcp')
  const [connectors, setConnectors] = useState<MCPEffectiveConnector[]>([])
  const [available, setAvailable] = useState<WsAvailable[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [search, setSearch] = useState('')

  // Adding new connectors (Available section) is admin-only in workspace
  // settings. Spec §5.1 "New UI rule introduced by this spec".
  const meWsRole = useWorkspaceStore((s) => s.workspaces.find((w) => w.id === wsId)?.role)

  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [eff, avail] = await Promise.all([
        wsListEffectiveConnectors(client, wsId),
        wsListAvailable(client, wsId),
      ])
      setConnectors(eff.items)
      setAvailable(avail.items)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }, [client, wsId])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])

  const filteredConnectors = useMemo(() => {
    const q = search.trim().toLowerCase()
    return connectors
      .filter((c) => {
        if (!q) return true
        const name = c.install.name || c.template?.name || ''
        return `${name} ${c.template?.provider ?? ''} ${c.template?.description ?? ''}`
          .toLowerCase()
          .includes(q)
      })
      .sort((a, b) => {
        const an = a.install.name || a.template?.name || a.install.connector_id
        const bn = b.install.name || b.template?.name || b.install.connector_id
        return an.localeCompare(bn)
      })
  }, [connectors, search])

  const filteredAvailable = useMemo(() => {
    const q = search.trim().toLowerCase()
    return available
      .filter((row) => {
        if (!q) return true
        const name = row.install?.name ?? row.template?.name ?? ''
        const provider = row.template?.provider ?? ''
        const description = row.template?.description ?? ''
        return `${name} ${provider} ${description}`.toLowerCase().includes(q)
      })
      .sort((a, b) => {
        const an = a.install?.name ?? a.template?.name ?? ''
        const bn = b.install?.name ?? b.template?.name ?? ''
        return an.localeCompare(bn)
      })
  }, [available, search])

  const selected = useMemo(
    () => connectors.find((c) => c.install.connector_id === selectedId) ?? null,
    [connectors, selectedId],
  )

  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden">
      <header className="flex items-center justify-between gap-2 border-b border-border/70 px-6 py-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">{tMcp('wsTitle')}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">{tMcp('wsSubtitle')}</p>
        </div>
        <Input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t('searchPlaceholder')}
          className="max-w-xs"
        />
      </header>

      <ListDetailLayout
        selected={selected !== null}
        onBack={() => setSelectedId(null)}
        backLabel={t('back')}
        placeholder={t('selectConnector')}
        railClassName="w-[340px] bg-card/20 px-0 py-0"
        list={
          <div aria-label="MCP connector list">
            {loading && connectors.length === 0 ? (
              <p className="px-4 py-6 text-center text-xs text-muted-foreground">{t('loading')}</p>
            ) : error ? (
              <p className="px-4 py-6 text-center text-xs text-destructive">{error}</p>
            ) : (
              <div className="flex flex-col gap-4 p-3">
                <section>
                  <h3 className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    {tMcp('installed')}
                  </h3>
                  {filteredConnectors.length === 0 ? (
                    <p className="px-1 text-xs text-muted-foreground">{t('noConnectors')}</p>
                  ) : (
                    <div className="flex flex-col gap-1.5">
                      {filteredConnectors.map((c) => (
                        <ConnectorRow
                          key={c.install.connector_id}
                          connector={c}
                          active={c.install.connector_id === selectedId}
                          onClick={() => setSelectedId(c.install.connector_id)}
                        />
                      ))}
                    </div>
                  )}
                </section>

                {meWsRole === 'admin' && (
                  <section>
                    <h3 className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      {tAvailable('title')}
                    </h3>
                    {filteredAvailable.length === 0 ? (
                      <p className="px-1 text-xs text-muted-foreground">{tAvailable('empty')}</p>
                    ) : (
                      <div className="flex flex-col gap-1.5">
                        {filteredAvailable.map((row) => (
                          <AvailableConnectorRow
                            key={
                              row.install?.connector_id ?? row.template?.template_id ?? 'unknown'
                            }
                            row={row}
                            client={client}
                            wsId={wsId}
                            onConnected={async (connectorId: string) => {
                              await load()
                              setSelectedId(connectorId)
                            }}
                          />
                        ))}
                      </div>
                    )}
                  </section>
                )}
              </div>
            )}
          </div>
        }
        detail={
          selected ? <ConnectorDetail connector={selected} wsId={wsId} onChanged={load} /> : null
        }
      />
    </div>
  )
}
