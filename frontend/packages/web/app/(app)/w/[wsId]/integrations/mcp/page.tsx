'use client'

import { useEffect, useMemo } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { createApiClient, useWorkspaceMcpStore } from '@cubebox/core'
import { useTranslations } from 'next-intl'

import { buttonVariants } from '@/components/ui/button'
import { MCPServerList } from '@/components/mcp/MCPServerList'

export default function WorkspaceMcpListPage() {
  const t = useTranslations('mcp.wsPage')
  const { wsId } = useParams<{ wsId: string }>()
  const client = useMemo(() => {
    const next = createApiClient('')
    next.setWorkspaceId(wsId)
    return next
  }, [wsId])
  const { owned, inherited, loading, error, fetchAll } = useWorkspaceMcpStore()

  useEffect(() => {
    void fetchAll(client, wsId)
  }, [client, fetchAll, wsId])

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold">{t('title')}</h1>
          <p className="text-sm text-muted-foreground">{t('subtitle')}</p>
        </div>
        <Link
          href={`/w/${wsId}/integrations/mcp/new`}
          className={buttonVariants({ variant: 'default' })}
        >
          {t('addServer')}
        </Link>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <section className="flex flex-col gap-3">
        <h2 className="text-base font-medium">{t('private')}</h2>
        <MCPServerList
          servers={owned}
          loading={loading}
          detailHrefBase={`/w/${wsId}/integrations/mcp`}
          emptyTitle={t('privateEmptyTitle')}
          emptyDescription={t('privateEmptyDesc')}
        />
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-base font-medium">{t('shared')}</h2>
        <MCPServerList
          servers={inherited}
          loading={loading}
          detailHrefBase={`/w/${wsId}/integrations/mcp`}
          emptyTitle={t('sharedEmptyTitle')}
          emptyDescription={t('sharedEmptyDesc')}
        />
      </section>
    </div>
  )
}
