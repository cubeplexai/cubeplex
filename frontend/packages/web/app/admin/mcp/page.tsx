'use client'

import { useEffect, useMemo } from 'react'
import Link from 'next/link'
import { createApiClient, useMcpStore } from '@cubebox/core'
import { useTranslations } from 'next-intl'

import { buttonVariants } from '@/components/ui/button'
import { MCPServerList } from '@/components/mcp/MCPServerList'

export default function AdminMcpPage() {
  const t = useTranslations('mcp.adminPage')
  const client = useMemo(() => createApiClient(''), [])
  const { servers, loading, error, fetchAll } = useMcpStore()

  useEffect(() => {
    void fetchAll(client)
  }, [client, fetchAll])

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold">{t('title')}</h1>
          <p className="text-sm text-muted-foreground">{t('subtitle')}</p>
        </div>
        <Link href="/admin/mcp/new" className={buttonVariants({ variant: 'default' })}>
          {t('addServer')}
        </Link>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      <MCPServerList
        servers={servers}
        loading={loading}
        detailHrefBase="/admin/mcp"
        emptyTitle={t('emptyTitle')}
        emptyDescription={t('emptyDesc')}
      />
    </div>
  )
}
