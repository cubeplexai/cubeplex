'use client'

import type { MCPCatalogConnector } from '@cubebox/core'
import { useTranslations } from 'next-intl'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Card } from '@/components/ui/card'

import { MCPCatalogCard } from './MCPCatalogCard'

export interface MCPCatalogGridProps {
  connectors: MCPCatalogConnector[]
  loading: boolean
  error: { code: string; message: string } | null
  mode: 'admin' | 'workspace'
  onSelectConnector: (connector: MCPCatalogConnector) => void
}

const SKELETON_COUNT = 6

export function MCPCatalogGrid({
  connectors,
  loading,
  error,
  mode,
  onSelectConnector,
}: MCPCatalogGridProps) {
  const t = useTranslations('mcpCatalog')

  if (error) {
    return (
      <Alert variant="destructive">
        <AlertTitle>{t('errorBannerTitle')}</AlertTitle>
        <AlertDescription>{t('errorBanner', { message: error.message })}</AlertDescription>
      </Alert>
    )
  }

  if (loading) {
    return (
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: SKELETON_COUNT }).map((_, idx) => (
          <Card key={idx} className="flex flex-col gap-3 p-5">
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-center gap-3">
                <div className="size-10 animate-pulse rounded-md bg-muted" />
                <div className="flex flex-col gap-1.5">
                  <div className="h-3.5 w-28 animate-pulse rounded bg-muted" />
                  <div className="h-3 w-16 animate-pulse rounded bg-muted" />
                </div>
              </div>
              <div className="h-5 w-20 animate-pulse rounded-full bg-muted" />
            </div>
            <div className="flex flex-col gap-1.5">
              <div className="h-3 w-full animate-pulse rounded bg-muted" />
              <div className="h-3 w-5/6 animate-pulse rounded bg-muted" />
              <div className="h-3 w-2/3 animate-pulse rounded bg-muted" />
            </div>
          </Card>
        ))}
      </div>
    )
  }

  if (connectors.length === 0) {
    return (
      <Card>
        <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
          <h3 className="font-semibold">{t('emptyTitle')}</h3>
          <p className="max-w-md text-sm text-muted-foreground">{t('empty')}</p>
        </div>
      </Card>
    )
  }

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
      {connectors.map((connector) => (
        <MCPCatalogCard
          key={connector.id}
          connector={connector}
          mode={mode}
          onSelect={onSelectConnector}
        />
      ))}
    </div>
  )
}
