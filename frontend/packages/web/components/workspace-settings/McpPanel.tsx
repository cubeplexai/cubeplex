'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, Loader2, PauseCircle, Plug } from 'lucide-react'
import { useTranslations } from 'next-intl'
import {
  createApiClient,
  wsCreateInstall,
  wsListEffectiveConnectors,
  wsListTemplates,
  wsPatchConnectorState,
  type MCPConnectorTemplate,
  type MCPCredentialScope,
  type MCPEffectiveConnector,
} from '@cubebox/core'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
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
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
        <CheckCircle2 className="size-3" />
        {t('ready')}
      </span>
    )
  }
  if (status === 'needsCredential') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
        <AlertTriangle className="size-3" />
        {t('needsCredential')}
      </span>
    )
  }
  if (status === 'pendingOAuth') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
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
  const name = connector.install.name || connector.template?.name || connector.install.install_id
  const provider = connector.template?.provider ?? ''
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? 'true' : undefined}
      data-testid={`ws-connector-row-${connector.install.install_id}`}
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
        <span className="ml-auto shrink-0">
          <StatusPill status={statusOf(connector)} />
        </span>
      </div>
      {provider && <p className="truncate text-xs text-muted-foreground">{provider}</p>}
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

function TemplateRow({
  template,
  installing,
  onInstall,
}: {
  template: MCPConnectorTemplate
  installing: boolean
  onInstall: () => void
}) {
  return (
    <div
      className="flex items-center justify-between gap-2 rounded-lg border border-border/70 bg-card/40 p-3"
      data-testid={`ws-template-row-${template.slug}`}
    >
      <div className="flex min-w-0 flex-col gap-0.5">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-semibold">{template.name}</span>
          {template.provider && (
            <Badge variant="outline" className="text-[10px]">
              {template.provider}
            </Badge>
          )}
        </div>
        {template.description && (
          <p className="line-clamp-1 text-xs text-muted-foreground">{template.description}</p>
        )}
      </div>
      <Button size="sm" disabled={installing} onClick={onInstall}>
        {installing && <Loader2 className="mr-2 size-3.5 animate-spin" />}
        Connect
      </Button>
    </div>
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
  const [error, setError] = useState<string | null>(null)
  const wsState = connector.workspace_state
  const installId = connector.install.install_id

  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  async function toggle(): Promise<void> {
    setSaving(true)
    setError(null)
    try {
      await wsPatchConnectorState(client, wsId, installId, { enabled: !wsState?.enabled })
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
      await wsPatchConnectorState(client, wsId, installId, { credential_policy: next })
      await onChanged()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex w-full flex-col gap-4 p-6">
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-xl font-semibold tracking-tight">
            {connector.install.name || connector.template?.name || installId}
          </h3>
          <StatusPill status={statusOf(connector)} />
        </div>
        {connector.template?.description && (
          <p className="text-sm text-muted-foreground">{connector.template.description}</p>
        )}
      </header>

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
    </div>
  )
}

export function McpPanel({ wsId }: McpPanelProps) {
  const t = useTranslations('mcpAdmin')
  const [connectors, setConnectors] = useState<MCPEffectiveConnector[]>([])
  const [templates, setTemplates] = useState<MCPConnectorTemplate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [installing, setInstalling] = useState<string | null>(null)
  const [search, setSearch] = useState('')

  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [eff, tpl] = await Promise.all([
        wsListEffectiveConnectors(client, wsId),
        wsListTemplates(client, wsId),
      ])
      setConnectors(eff.items)
      setTemplates(tpl.items)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }, [client, wsId])

  useEffect(() => {
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
        const an = a.install.name || a.template?.name || a.install.install_id
        const bn = b.install.name || b.template?.name || b.install.install_id
        return an.localeCompare(bn)
      })
  }, [connectors, search])

  const filteredTemplates = useMemo(() => {
    // Only ACTIVE installs mask their template — tombstoned (uninstalled)
    // rows must not block reinstalling, otherwise "Disconnect" turns into
    // a one-shot per template from the workspace settings panel.
    const installedTemplateIds = new Set(
      connectors
        .filter((c) => c.install.install_state === 'active')
        .map((c) => c.template?.template_id)
        .filter((v): v is string => Boolean(v)),
    )
    const q = search.trim().toLowerCase()
    return templates
      .filter((tpl) => !installedTemplateIds.has(tpl.template_id))
      .filter((tpl) => {
        if (!q) return true
        return `${tpl.name} ${tpl.provider} ${tpl.description}`.toLowerCase().includes(q)
      })
      .sort((a, b) => a.name.localeCompare(b.name))
  }, [templates, connectors, search])

  const selected = useMemo(
    () => connectors.find((c) => c.install.install_id === selectedId) ?? null,
    [connectors, selectedId],
  )

  async function installTemplate(template: MCPConnectorTemplate): Promise<void> {
    setInstalling(template.template_id)
    try {
      const method =
        template.supported_auth_methods.find((m) => m === 'static') ??
        template.supported_auth_methods.find((m) => m === 'none') ??
        template.supported_auth_methods[0]
      const policy: MCPCredentialScope =
        method === 'none'
          ? 'none'
          : template.default_credential_policy === 'none'
            ? 'user'
            : template.default_credential_policy
      const result = await wsCreateInstall(client, wsId, {
        template_id: template.template_id,
        install_scope: 'workspace',
        auth_method: method,
        default_credential_policy: policy,
      })
      await load()
      setSelectedId(result.install_id)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setInstalling(null)
    }
  }

  const enabledCount = connectors.filter((c) => c.workspace_state?.enabled).length

  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden">
      <header className="flex items-center justify-between gap-2 border-b border-border/70 px-6 py-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">MCP Connectors</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {t('workspaceStateSummary', { enabled: enabledCount, total: connectors.length })}
          </p>
        </div>
        <Input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t('searchPlaceholder')}
          className="max-w-xs"
        />
      </header>

      <div className="flex flex-1 overflow-hidden">
        <aside
          aria-label="MCP connector list"
          className="w-[340px] shrink-0 overflow-y-auto border-r border-border/70 bg-card/20"
        >
          {loading && connectors.length === 0 ? (
            <p className="px-4 py-6 text-center text-xs text-muted-foreground">{t('loading')}</p>
          ) : error ? (
            <p className="px-4 py-6 text-center text-xs text-destructive">{error}</p>
          ) : (
            <div className="flex flex-col gap-4 p-3">
              <section>
                <h3 className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {t('installs')}
                </h3>
                {filteredConnectors.length === 0 ? (
                  <p className="px-1 text-xs text-muted-foreground">{t('noConnectors')}</p>
                ) : (
                  <div className="flex flex-col gap-1.5">
                    {filteredConnectors.map((c) => (
                      <ConnectorRow
                        key={c.install.install_id}
                        connector={c}
                        active={c.install.install_id === selectedId}
                        onClick={() => setSelectedId(c.install.install_id)}
                      />
                    ))}
                  </div>
                )}
              </section>

              {filteredTemplates.length > 0 && (
                <section>
                  <h3 className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    {t('templates')}
                  </h3>
                  <div className="flex flex-col gap-1.5">
                    {filteredTemplates.map((tpl) => (
                      <TemplateRow
                        key={tpl.template_id}
                        template={tpl}
                        installing={installing === tpl.template_id}
                        onInstall={() => void installTemplate(tpl)}
                      />
                    ))}
                  </div>
                </section>
              )}
            </div>
          )}
        </aside>

        <section className="flex flex-1 overflow-y-auto">
          {selected ? (
            <ConnectorDetail connector={selected} wsId={wsId} onChanged={load} />
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
