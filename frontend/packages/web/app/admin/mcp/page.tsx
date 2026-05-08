'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import {
  createApiClient,
  useMcpStore,
  useWorkspaceStore,
  type MCPCatalogConnector,
} from '@cubebox/core'
import { CheckCircle2, ChevronDown, ChevronRight } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { buttonVariants } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { MCPServerList } from '@/components/mcp/MCPServerList'
import { MCPCatalogGrid, MCPInstallDrawer } from '@/components/mcp/catalog'

export default function AdminMcpPage() {
  const t = useTranslations('mcp.adminPage')
  const tCat = useTranslations('mcpCatalog')
  const client = useMemo(() => createApiClient(''), [])

  const servers = useMcpStore((s) => s.servers)
  const loading = useMcpStore((s) => s.loading)
  const error = useMcpStore((s) => s.error)
  const fetchAll = useMcpStore((s) => s.fetchAll)
  const catalog = useMcpStore((s) => s.catalog)
  const catalogLoading = useMcpStore((s) => s.catalogLoading)
  const catalogError = useMcpStore((s) => s.catalogError)
  const fetchCatalog = useMcpStore((s) => s.fetchCatalog)

  const workspaces = useWorkspaceStore((s) => s.workspaces)
  const fetchWorkspaceList = useWorkspaceStore((s) => s.fetchList)

  const [lensWsId, setLensWsId] = useState<string>('')
  const [drawerConnector, setDrawerConnector] = useState<MCPCatalogConnector | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)

  // Load workspaces (admin layout doesn't preload them).
  useEffect(() => {
    if (workspaces.length === 0) {
      void fetchWorkspaceList(client)
    }
  }, [client, fetchWorkspaceList, workspaces.length])

  // Pick a default lens once workspaces resolve.
  useEffect(() => {
    if (!lensWsId && workspaces.length > 0) {
      setLensWsId(workspaces[0].id)
    }
  }, [workspaces, lensWsId])

  useEffect(() => {
    void fetchAll(client)
  }, [client, fetchAll])

  useEffect(() => {
    if (lensWsId) {
      void fetchCatalog(client, lensWsId)
    }
  }, [client, fetchCatalog, lensWsId])

  // Hide success after 4s.
  useEffect(() => {
    if (!successMessage) return
    const id = window.setTimeout(() => setSuccessMessage(null), 4000)
    return () => window.clearTimeout(id)
  }, [successMessage])

  function handleSelectConnector(connector: MCPCatalogConnector): void {
    setDrawerConnector(connector)
    setDrawerOpen(true)
  }

  function handleCloseDrawer(): void {
    setDrawerOpen(false)
    // After install, store has already refetched; surface success unless OAuth is in flight.
    const pending = useMcpStore.getState().pendingOAuthInstallId
    if (pending === null && !useMcpStore.getState().catalogError) {
      setSuccessMessage(tCat('installSuccessAdmin'))
    }
  }

  return (
    <div className="flex flex-col gap-6 p-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">{tCat('pageTitleAdmin')}</h1>
        <p className="text-sm text-muted-foreground">{tCat('pageSubtitleAdmin')}</p>
      </header>

      {successMessage ? (
        <Alert>
          <CheckCircle2 className="size-4" aria-hidden />
          <AlertTitle>{successMessage}</AlertTitle>
        </Alert>
      ) : null}

      {/* Workspace lens selector — catalog endpoint is workspace-scoped because per-(ws,user)
          status flags require a workspace context. Installs themselves apply org-wide. */}
      {workspaces.length === 0 ? (
        <Alert>
          <AlertTitle>{tCat('noWorkspaceTitle')}</AlertTitle>
          <AlertDescription>{tCat('noWorkspaceDesc')}</AlertDescription>
        </Alert>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <label htmlFor="ws-lens" className="text-sm font-medium">
            {tCat('workspaceLensLabel')}
          </label>
          <Select
            value={lensWsId}
            onValueChange={(value: string | null) => {
              if (value) setLensWsId(value)
            }}
          >
            <SelectTrigger id="ws-lens" className="min-w-[220px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {workspaces.map((ws) => (
                  <SelectItem key={ws.id} value={ws.id}>
                    {ws.name}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">{tCat('workspaceLensHelp')}</p>
        </div>
      )}

      {lensWsId ? (
        <MCPCatalogGrid
          connectors={catalog}
          loading={catalogLoading}
          error={catalogError}
          mode="admin"
          onSelectConnector={handleSelectConnector}
        />
      ) : null}

      {/* Advanced / debug — handcrafted custom-connector list, demoted. */}
      <section className="flex flex-col gap-3 rounded-lg border border-dashed border-border/70 bg-muted/10 p-4">
        <button
          type="button"
          className="flex items-center justify-between gap-3 text-left"
          onClick={() => setAdvancedOpen((open) => !open)}
          aria-expanded={advancedOpen}
        >
          <div className="flex items-center gap-2">
            {advancedOpen ? (
              <ChevronDown className="size-4 text-muted-foreground" aria-hidden />
            ) : (
              <ChevronRight className="size-4 text-muted-foreground" aria-hidden />
            )}
            <div className="flex flex-col gap-0.5">
              <span className="text-sm font-medium">{tCat('advancedSectionTitle')}</span>
              <span className="text-xs text-muted-foreground">{tCat('advancedSectionHelp')}</span>
            </div>
          </div>
          <span className="text-xs text-muted-foreground">
            {advancedOpen ? tCat('advancedHide') : tCat('advancedShow')}
          </span>
        </button>

        {advancedOpen ? (
          <div className="flex flex-col gap-4 pt-2">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-col gap-1">
                <h2 className="text-base font-semibold">{t('title')}</h2>
                <p className="text-xs text-muted-foreground">{t('subtitle')}</p>
              </div>
              <Link
                href="/admin/mcp/new"
                className={buttonVariants({ variant: 'outline', size: 'sm' })}
              >
                {t('addServer')}
              </Link>
            </div>

            {error ? (
              <Card className="border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
                {error}
              </Card>
            ) : null}

            <MCPServerList
              servers={servers}
              loading={loading}
              detailHrefBase="/admin/mcp"
              emptyTitle={t('emptyTitle')}
              emptyDescription={t('emptyDesc')}
            />
          </div>
        ) : null}
      </section>

      {lensWsId ? (
        <MCPInstallDrawer
          connector={drawerConnector}
          mode="admin"
          open={drawerOpen}
          onClose={handleCloseDrawer}
          client={client}
          wsId={lensWsId}
        />
      ) : null}
    </div>
  )
}
