'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { useRouter } from 'next/navigation'
import useSWR from 'swr'
import { ExternalLink, Loader2 } from 'lucide-react'
import { createApiClient, wsListCatalog, type WorkspaceCatalogRow } from '@cubeplex/core'
import { cn } from '@/lib/utils'
import { ConnectorLogo } from '@/components/mcp/ConnectorLogo'
import { ComposerOverlayShell } from './ComposerOverlayShell'

type RowStatus = 'ready' | 'needsAttention' | 'disabled' | 'notInstalled'

function statusOf(row: WorkspaceCatalogRow): RowStatus {
  if (!row.connector) return 'notInstalled'
  if (!row.enabled) return 'disabled'
  if (row.usable === false || row.reason) return 'needsAttention'
  return 'ready'
}

async function fetchCatalog(wsId: string): Promise<WorkspaceCatalogRow[]> {
  const client = createApiClient('')
  client.setWorkspaceId(wsId)
  const res = await wsListCatalog(client, wsId)
  return res.items
}

export type ComposerMcpPickerProps = {
  open: boolean
  workspaceId: string
  onClose: () => void
}

export function ComposerMcpPicker({
  open,
  workspaceId,
  onClose,
}: ComposerMcpPickerProps): React.ReactElement | null {
  const t = useTranslations('composerExtras')
  const router = useRouter()
  const [query, setQuery] = useState('')

  const { data, error, isLoading } = useSWR<WorkspaceCatalogRow[]>(
    open ? ['composer-mcp-catalog', workspaceId] : null,
    () => fetchCatalog(workspaceId),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  const rows = data ?? []
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return rows
    return rows.filter((row) => {
      const name = row.template.name.toLowerCase()
      const slug = row.template.slug.toLowerCase()
      const desc = (row.template.description ?? '').toLowerCase()
      return name.includes(q) || slug.includes(q) || desc.includes(q)
    })
  }, [rows, query])

  useEffect(() => {
    if (!open) setQuery('')
  }, [open])

  if (!open) return null

  const openManage = (): void => {
    onClose()
    router.push(`/w/${workspaceId}/mcp`)
  }

  const statusLabel = (status: RowStatus): string => {
    switch (status) {
      case 'ready':
        return t('mcpStatusReady')
      case 'needsAttention':
        return t('mcpStatusNeedsAttention')
      case 'disabled':
        return t('mcpStatusDisabled')
      case 'notInstalled':
        return t('mcpStatusNotInstalled')
    }
  }

  return (
    <ComposerOverlayShell
      role="dialog"
      aria-label={t('mcpPickerAria')}
      data-testid="composer-mcp-picker"
    >
      <div className="shrink-0 border-b border-border px-2 py-1.5">
        <input
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              e.preventDefault()
              e.stopPropagation()
              onClose()
            }
          }}
          placeholder={t('mcpSearchPlaceholder')}
          data-testid="composer-mcp-search"
          className="h-8 w-full rounded-md bg-transparent px-2 text-sm outline-none placeholder:text-muted-foreground"
        />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-1">
        {isLoading ? (
          <div className="flex h-16 items-center justify-center text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            <span className="sr-only">{t('loading')}</span>
          </div>
        ) : error ? (
          <div className="px-2 py-3 text-xs text-destructive">{t('mcpLoadError')}</div>
        ) : filtered.length === 0 ? (
          <div className="px-2 py-3 text-xs text-muted-foreground">
            {rows.length === 0 ? t('mcpEmpty') : t('mcpNoMatches')}
          </div>
        ) : (
          filtered.map((row) => {
            const status = statusOf(row)
            return (
              <div
                key={row.template.template_id}
                data-testid={`composer-mcp-row-${row.template.slug}`}
                className={cn('flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left')}
              >
                <ConnectorLogo
                  name={row.template.name}
                  icon={row.template.icon}
                  size="sm"
                  className="shrink-0"
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{row.template.name}</span>
                  {row.template.description ? (
                    <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
                      {row.template.description}
                    </span>
                  ) : null}
                </span>
                <span
                  className={cn(
                    'shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium',
                    status === 'ready' && 'bg-success-solid/10 text-success-fg',
                    status === 'needsAttention' && 'bg-warning-solid/10 text-warning-fg',
                    (status === 'disabled' || status === 'notInstalled') &&
                      'bg-muted text-muted-foreground',
                  )}
                >
                  {statusLabel(status)}
                </span>
              </div>
            )
          })
        )}
      </div>
      <div className="flex shrink-0 items-center justify-between gap-2 border-t border-border px-2 py-1.5">
        <span className="text-[11px] text-muted-foreground">{t('mcpFooterHint')}</span>
        <button
          type="button"
          onClick={openManage}
          data-testid="composer-mcp-manage"
          className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          {t('manageMcp')}
          <ExternalLink className="size-3" aria-hidden />
        </button>
      </div>
    </ComposerOverlayShell>
  )
}
