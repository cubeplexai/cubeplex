'use client'

import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  Eye,
  EyeOff,
  Globe,
  Loader2,
  Plug,
} from 'lucide-react'
import { createApiClient, useWorkspaceMcpCatalogStore, wsOAuthStart } from '@cubebox/core'
import type { MCPAuthMethod, MCPCatalogConnector, MCPCatalogStaticFormField } from '@cubebox/core'
import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'

const OAUTH_ORIGIN_KEY = 'mcp_oauth_origin'

interface McpPanelProps {
  wsId: string
}

type ConnectorStatus = 'enabled' | 'available' | 'needsSetup' | 'notInstalled'

function statusOf(c: MCPCatalogConnector): ConnectorStatus {
  if (c.user_install_id) return c.workspace_visible ? 'enabled' : 'needsSetup'
  if (c.org_install_id) return c.workspace_visible ? 'enabled' : 'available'
  return 'notInstalled'
}

function defaultAuthMethod(supported: MCPAuthMethod[]): MCPAuthMethod {
  if (supported.includes('oauth')) return 'oauth'
  if (supported.includes('static')) return 'static'
  return 'none'
}

function persistOAuthOrigin(): void {
  if (typeof window === 'undefined') return
  try {
    window.sessionStorage.setItem(
      OAUTH_ORIGIN_KEY,
      window.location.pathname + window.location.search,
    )
  } catch {
    // sessionStorage may be unavailable; non-fatal.
  }
}

function StatusChip({ status }: { status: ConnectorStatus }) {
  const t = useTranslations('mcp.wsPanel.catalog')

  if (status === 'enabled') {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10
          px-1.5 py-0.5 text-[10px] font-medium text-emerald-600
          dark:text-emerald-400"
      >
        <CheckCircle2 className="size-3" />
        {t('statusEnabled')}
      </span>
    )
  }
  if (status === 'available') {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-full bg-muted
          px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground"
      >
        <Plug className="size-3" />
        {t('statusAvailable')}
      </span>
    )
  }
  if (status === 'needsSetup') {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-full bg-amber-500/10
          px-1.5 py-0.5 text-[10px] font-medium text-amber-600
          dark:text-amber-400"
      >
        <AlertTriangle className="size-3" />
        {t('statusNeedsSetup')}
      </span>
    )
  }
  return null
}

function ConnectorCard({
  connector,
  active,
  onClick,
}: {
  connector: MCPCatalogConnector
  active: boolean
  onClick: () => void
}) {
  const status = statusOf(connector)
  const ScopeIcon = connector.org_install_id ? Globe : Plug
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? 'true' : undefined}
      data-testid={`ws-catalog-card-${connector.slug}`}
      className={cn(
        'group flex w-full flex-col gap-1.5 rounded-lg border p-3 text-left transition-all',
        active
          ? 'border-primary/40 bg-primary/5 shadow-sm'
          : 'border-border/70 bg-card/40 hover:border-border hover:bg-accent/40',
      )}
    >
      <div className="flex items-center gap-2">
        <ScopeIcon
          className={cn(
            'size-3.5 shrink-0',
            connector.org_install_id ? 'text-primary' : 'text-muted-foreground',
          )}
        />
        <span className="truncate text-sm font-semibold">{connector.name}</span>
        <span className="ml-auto shrink-0">
          <StatusChip status={status} />
        </span>
      </div>
      {connector.provider && (
        <p className="truncate text-xs text-muted-foreground">{connector.provider}</p>
      )}
      <div className="flex flex-wrap items-center gap-1 pt-0.5">
        <Badge variant="outline" className="px-1.5 text-[10px]">
          {connector.transport === 'streamable_http' ? 'HTTP' : 'SSE'}
        </Badge>
        {connector.supported_auth_methods.map((m) => (
          <Badge key={m} variant="outline" className="px-1.5 text-[10px]">
            {m}
          </Badge>
        ))}
      </div>
    </button>
  )
}

function StaticFieldRow({
  field,
  value,
  onChange,
}: {
  field: MCPCatalogStaticFormField
  value: string
  onChange: (next: string) => void
}) {
  const t = useTranslations('mcpCatalog')
  const [reveal, setReveal] = useState(false)
  const inputType = field.secret && !reveal ? 'password' : 'text'

  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={`ws-catalog-static-${field.name}`}>
        {field.label}
        <span className="ml-0.5 text-destructive">*</span>
      </Label>
      <div className="flex gap-2">
        <Input
          id={`ws-catalog-static-${field.name}`}
          type={inputType}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.placeholder}
          autoComplete={field.secret ? 'new-password' : 'off'}
          required
        />
        {field.secret && (
          <Button
            type="button"
            variant="outline"
            size="icon"
            onClick={() => setReveal((r) => !r)}
            aria-label={reveal ? t('hideSecret') : t('showSecret')}
          >
            {reveal ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
          </Button>
        )}
      </div>
      {field.helper_url && (
        <a
          href={field.helper_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
        >
          <ExternalLink className="size-3" />
          {t('helperLearnMore')}
        </a>
      )}
    </div>
  )
}

function InstallForm({ connector, wsId }: { connector: MCPCatalogConnector; wsId: string }) {
  const t = useTranslations('mcp.wsPanel.catalog')
  const tc = useTranslations('mcpCatalog')
  const installForWorkspace = useWorkspaceMcpCatalogStore((s) => s.installForWorkspace)

  const supported = useMemo(() => connector.supported_auth_methods, [connector])
  const staticFields = useMemo(() => connector.static_form_fields ?? [], [connector])

  const [authMethod, setAuthMethod] = useState<MCPAuthMethod>(() => defaultAuthMethod(supported))
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(staticFields.map((f) => [f.name, ''])),
  )
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setAuthMethod(defaultAuthMethod(supported))
    setValues(Object.fromEntries(staticFields.map((f) => [f.name, ''])))
    setError(null)
  }, [connector.id, supported, staticFields])

  const allStaticFilled = staticFields.every((f) => (values[f.name] ?? '').trim().length > 0)
  const multiField = staticFields.length > 1

  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  async function handleStatic(): Promise<void> {
    if (multiField) {
      setError(tc('multiFieldUnsupported'))
      return
    }
    if (!allStaticFilled) return
    setSubmitting(true)
    setError(null)
    try {
      const fieldName = staticFields[0]?.name ?? 'token'
      await installForWorkspace(client, wsId, connector.id, {
        auth_method: 'static',
        credential_plaintext: (values[fieldName] ?? '').trim(),
      })
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  async function handleOAuth(): Promise<void> {
    setSubmitting(true)
    setError(null)
    try {
      await installForWorkspace(client, wsId, connector.id, { auth_method: 'oauth' })
      // The fresh state may already include the new user_install_id; fetch the
      // fresh row to get the install id for the OAuth start call.
      const after = useWorkspaceMcpCatalogStore
        .getState()
        .connectors.find((c) => c.id === connector.id)
      const installId = after?.user_install_id
      if (!installId) {
        throw new Error('install id not found after install')
      }
      persistOAuthOrigin()
      const oauth = await wsOAuthStart(client, wsId, installId)
      if (typeof window !== 'undefined') {
        window.location.href = oauth.authorize_url
      }
    } catch (err) {
      setError((err as Error).message)
      setSubmitting(false)
    }
  }

  async function handleNone(): Promise<void> {
    setSubmitting(true)
    setError(null)
    try {
      await installForWorkspace(client, wsId, connector.id, { auth_method: 'none' })
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex flex-col gap-4 rounded-lg border border-border/70 bg-card/40 p-4">
      <div className="flex flex-col gap-1">
        <h4 className="text-sm font-semibold">{t('installTitle')}</h4>
        <p className="text-xs text-muted-foreground">{tc('workspaceOAuthNotice')}</p>
      </div>

      {supported.length > 1 && (
        <div className="flex gap-2">
          {supported.map((m) => (
            <Button
              key={m}
              type="button"
              size="sm"
              variant={authMethod === m ? 'default' : 'outline'}
              onClick={() => setAuthMethod(m)}
            >
              {tc(`auth${m.charAt(0).toUpperCase() + m.slice(1)}` as 'authOAuth')}
            </Button>
          ))}
        </div>
      )}

      {authMethod === 'static' && staticFields.length > 0 && (
        <div className="flex flex-col gap-3">
          {staticFields.map((f) => (
            <StaticFieldRow
              key={f.name}
              field={f}
              value={values[f.name] ?? ''}
              onChange={(next) => setValues((v) => ({ ...v, [f.name]: next }))}
            />
          ))}
        </div>
      )}

      {authMethod === 'none' && <p className="text-xs text-muted-foreground">{tc('noneNotice')}</p>}

      {error && <p className="text-xs text-destructive">{error}</p>}

      <div className="flex justify-end">
        <Button
          type="button"
          onClick={() => {
            if (authMethod === 'oauth') void handleOAuth()
            else if (authMethod === 'static') void handleStatic()
            else void handleNone()
          }}
          disabled={submitting || (authMethod === 'static' && (!allStaticFilled || multiField))}
        >
          {submitting && <Loader2 className="mr-2 size-4 animate-spin" />}
          {authMethod === 'oauth' ? tc('connectWithOAuth') : tc('installButton')}
        </Button>
      </div>
    </div>
  )
}

function ActionPanel({ connector, wsId }: { connector: MCPCatalogConnector; wsId: string }) {
  const t = useTranslations('mcp.wsPanel.catalog')
  const enableOrgInstall = useWorkspaceMcpCatalogStore((s) => s.enableOrgInstall)
  const disableOrgInstall = useWorkspaceMcpCatalogStore((s) => s.disableOrgInstall)
  const uninstallWorkspacePrivate = useWorkspaceMcpCatalogStore((s) => s.uninstallWorkspacePrivate)
  const [busy, setBusy] = useState(false)

  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  async function withBusy(fn: () => Promise<void>): Promise<void> {
    setBusy(true)
    try {
      await fn()
    } finally {
      setBusy(false)
    }
  }

  if (connector.user_install_id) {
    return (
      <Button
        variant="destructive"
        disabled={busy}
        onClick={() =>
          withBusy(() =>
            uninstallWorkspacePrivate(client, wsId, connector.user_install_id as string),
          )
        }
      >
        {busy && <Loader2 className="mr-2 size-4 animate-spin" />}
        {t('actionUninstall')}
      </Button>
    )
  }

  if (connector.org_install_id) {
    if (connector.workspace_visible) {
      return (
        <Button
          variant="outline"
          disabled={busy}
          onClick={() =>
            withBusy(() => disableOrgInstall(client, wsId, connector.org_install_id as string))
          }
        >
          {busy && <Loader2 className="mr-2 size-4 animate-spin" />}
          {t('actionDisable')}
        </Button>
      )
    }
    return (
      <Button
        disabled={busy}
        onClick={() =>
          withBusy(() => enableOrgInstall(client, wsId, connector.org_install_id as string))
        }
      >
        {busy && <Loader2 className="mr-2 size-4 animate-spin" />}
        {t('actionEnable')}
      </Button>
    )
  }

  return <InstallForm connector={connector} wsId={wsId} />
}

function ConnectorDetail({ connector, wsId }: { connector: MCPCatalogConnector; wsId: string }) {
  const t = useTranslations('mcp.wsPanel.catalog')
  const status = statusOf(connector)

  return (
    <div className="flex w-full flex-col gap-4 p-6">
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-xl font-semibold tracking-tight">{connector.name}</h3>
          <StatusChip status={status} />
        </div>
        {connector.description && (
          <p className="text-sm text-muted-foreground">{connector.description}</p>
        )}
      </header>

      <div className="rounded-lg border border-border/70 bg-card/40 p-4">
        <dl className="grid grid-cols-[140px_1fr] gap-y-2 text-sm">
          <dt className="text-muted-foreground">{t('provider')}</dt>
          <dd>{connector.provider || '-'}</dd>
          <dt className="text-muted-foreground">{t('serverUrl')}</dt>
          <dd className="break-all font-mono text-xs">{connector.server_url}</dd>
          <dt className="text-muted-foreground">{t('transport')}</dt>
          <dd>{connector.transport}</dd>
          <dt className="text-muted-foreground">{t('authMethods')}</dt>
          <dd>{connector.supported_auth_methods.join(', ')}</dd>
          {connector.org_install_id && (
            <>
              <dt className="text-muted-foreground">{t('scope')}</dt>
              <dd>{t('scopeOrgWide')}</dd>
            </>
          )}
          {connector.user_install_id && (
            <>
              <dt className="text-muted-foreground">{t('scope')}</dt>
              <dd>{t('scopeWorkspacePrivate')}</dd>
            </>
          )}
        </dl>
      </div>

      <ActionPanel connector={connector} wsId={wsId} />
    </div>
  )
}

export function McpPanel({ wsId }: McpPanelProps) {
  const t = useTranslations('mcp.wsPanel.catalog')
  const connectors = useWorkspaceMcpCatalogStore((s) => s.connectors)
  const loading = useWorkspaceMcpCatalogStore((s) => s.loading)
  const error = useWorkspaceMcpCatalogStore((s) => s.error)
  const selectedSlug = useWorkspaceMcpCatalogStore((s) => s.selectedSlug)
  const selectSlug = useWorkspaceMcpCatalogStore((s) => s.selectSlug)
  const load = useWorkspaceMcpCatalogStore((s) => s.load)

  const [search, setSearch] = useState('')

  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  useEffect(() => {
    void load(client, wsId)
  }, [client, wsId, load])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return connectors
      .filter((c) => {
        if (!q) return true
        return `${c.name} ${c.provider} ${c.description}`.toLowerCase().includes(q)
      })
      .sort((a, b) => {
        const order: Record<ConnectorStatus, number> = {
          enabled: 0,
          needsSetup: 1,
          available: 2,
          notInstalled: 3,
        }
        const oa = order[statusOf(a)]
        const ob = order[statusOf(b)]
        if (oa !== ob) return oa - ob
        return a.name.localeCompare(b.name)
      })
  }, [connectors, search])

  const selected = useMemo(
    () => connectors.find((c) => c.slug === selectedSlug) ?? null,
    [connectors, selectedSlug],
  )

  const enabledCount = connectors.filter((c) => c.workspace_visible).length

  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden">
      <header
        className="flex items-center justify-between gap-2 border-b border-border/70
          px-6 py-4"
      >
        <div>
          <h2 className="text-lg font-semibold tracking-tight">{t('title')}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {t('summary', { enabled: enabledCount, total: connectors.length })}
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
          aria-label={t('listAria')}
          className="w-[340px] shrink-0 overflow-y-auto border-r
            border-border/70 bg-card/20"
        >
          {loading && connectors.length === 0 ? (
            <p className="px-4 py-6 text-center text-xs text-muted-foreground">{t('loading')}</p>
          ) : error ? (
            <p className="px-4 py-6 text-center text-xs text-destructive">{error}</p>
          ) : filtered.length === 0 ? (
            <div
              className="flex h-full flex-col items-center justify-center gap-1
                px-6 text-center"
            >
              <p className="text-sm text-muted-foreground">{t('empty')}</p>
              <p className="text-xs text-muted-foreground/70">{t('emptyHint')}</p>
            </div>
          ) : (
            <div className="flex flex-col gap-1.5 p-3">
              {filtered.map((c) => (
                <ConnectorCard
                  key={c.slug}
                  connector={c}
                  active={c.slug === selectedSlug}
                  onClick={() => selectSlug(c.slug)}
                />
              ))}
            </div>
          )}
        </aside>

        <section className="flex flex-1 overflow-y-auto">
          {selected ? (
            <ConnectorDetail connector={selected} wsId={wsId} />
          ) : (
            <div
              className="flex flex-1 items-center justify-center p-8 text-sm
                text-muted-foreground"
            >
              {t('selectConnector')}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
