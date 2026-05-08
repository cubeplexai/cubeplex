'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { createApiClient, useWorkspaceMcpStore, type MCPCatalogConnector } from '@cubebox/core'
import { CheckCircle2, ChevronDown, ChevronRight } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Alert, AlertTitle } from '@/components/ui/alert'
import { buttonVariants } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { MCPServerList } from '@/components/mcp/MCPServerList'
import { MCPCatalogGrid, MCPInstallDrawer } from '@/components/mcp/catalog'

export default function WorkspaceMcpListPage() {
  const t = useTranslations('mcp.wsPage')
  const tCat = useTranslations('mcpCatalog')
  const { wsId } = useParams<{ wsId: string }>()
  const client = useMemo(() => {
    const next = createApiClient('')
    next.setWorkspaceId(wsId)
    return next
  }, [wsId])

  const owned = useWorkspaceMcpStore((s) => s.owned)
  const inherited = useWorkspaceMcpStore((s) => s.inherited)
  const loading = useWorkspaceMcpStore((s) => s.loading)
  const error = useWorkspaceMcpStore((s) => s.error)
  const fetchAll = useWorkspaceMcpStore((s) => s.fetchAll)
  const catalog = useWorkspaceMcpStore((s) => s.catalog)
  const catalogLoading = useWorkspaceMcpStore((s) => s.catalogLoading)
  const catalogError = useWorkspaceMcpStore((s) => s.catalogError)
  const fetchCatalog = useWorkspaceMcpStore((s) => s.fetchCatalog)

  const [drawerConnector, setDrawerConnector] = useState<MCPCatalogConnector | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)

  useEffect(() => {
    void fetchAll(client, wsId)
  }, [client, fetchAll, wsId])

  useEffect(() => {
    void fetchCatalog(client, wsId)
  }, [client, fetchCatalog, wsId])

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
    const pending = useWorkspaceMcpStore.getState().pendingOAuthInstallId
    if (pending === null && !useWorkspaceMcpStore.getState().catalogError) {
      setSuccessMessage(tCat('installSuccessWorkspace'))
    }
  }

  return (
    <div className="flex flex-col gap-6 p-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">{tCat('pageTitleWorkspace')}</h1>
        <p className="text-sm text-muted-foreground">{tCat('pageSubtitleWorkspace')}</p>
      </header>

      {successMessage ? (
        <Alert>
          <CheckCircle2 className="size-4" aria-hidden />
          <AlertTitle>{successMessage}</AlertTitle>
        </Alert>
      ) : null}

      <MCPCatalogGrid
        connectors={catalog}
        loading={catalogLoading}
        error={catalogError}
        mode="workspace"
        onSelectConnector={handleSelectConnector}
      />

      {/* Advanced / debug — handcrafted custom-connector list, demoted behind a disclosure. */}
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
                href={`/w/${wsId}/integrations/mcp/new`}
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

            <section className="flex flex-col gap-3">
              <h3 className="text-sm font-medium">{t('private')}</h3>
              <MCPServerList
                servers={owned}
                loading={loading}
                detailHrefBase={`/w/${wsId}/integrations/mcp`}
                emptyTitle={t('privateEmptyTitle')}
                emptyDescription={t('privateEmptyDesc')}
              />
            </section>

            <section className="flex flex-col gap-3">
              <h3 className="text-sm font-medium">{t('shared')}</h3>
              <MCPServerList
                servers={inherited}
                loading={loading}
                detailHrefBase={`/w/${wsId}/integrations/mcp`}
                emptyTitle={t('sharedEmptyTitle')}
                emptyDescription={t('sharedEmptyDesc')}
              />
            </section>
          </div>
        ) : null}
      </section>

      <MCPInstallDrawer
        connector={drawerConnector}
        mode="workspace"
        open={drawerOpen}
        onClose={handleCloseDrawer}
        client={client}
        wsId={wsId}
      />
    </div>
  )
}
