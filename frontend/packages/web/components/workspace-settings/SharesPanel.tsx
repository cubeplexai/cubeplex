'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Check, Copy, Loader2, Share2 } from 'lucide-react'
import { useTranslations, useFormatter } from 'next-intl'
import { createApiClient, listShares, revokeShare } from '@cubeplex/core'
import type { ConversationShare } from '@cubeplex/core'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/shared/EmptyState'
import { SETTINGS_CONTENT_WIDTH, SectionHeader } from '@/components/shared/SectionHeader'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

interface SharesPanelProps {
  wsId: string
}

export function SharesPanel({ wsId }: SharesPanelProps) {
  const t = useTranslations('wsShares')
  const fmt = useFormatter()

  const [shares, setShares] = useState<ConversationShare[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [copiedId, setCopiedId] = useState<string | null>(null)

  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await listShares(client, { workspaceId: wsId })
      setShares(result.items)
      setTotal(result.total)
    } finally {
      setLoading(false)
    }
  }, [client, wsId])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- load-on-mount
    void load()
  }, [load])

  const handleRevoke = useCallback(
    async (shareId: string) => {
      const updated = await revokeShare(client, shareId)
      setShares((prev) => prev.map((s) => (s.id === shareId ? updated : s)))
    },
    [client],
  )

  const handleCopy = useCallback((share: ConversationShare) => {
    void navigator.clipboard.writeText(window.location.origin + share.url).then(() => {
      setCopiedId(share.id)
      setTimeout(() => setCopiedId((prev) => (prev === share.id ? null : prev)), 2000)
    })
  }, [])

  return (
    <div className="flex h-full flex-col">
      <SectionHeader
        title={t('title')}
        description={t('subtitle')}
        contained={SETTINGS_CONTENT_WIDTH}
      />

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className={cn('flex w-full flex-col gap-6', SETTINGS_CONTENT_WIDTH)}>
          {/* Body */}
          {loading ? (
            <div className="flex items-center justify-center py-16 text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
          ) : shares.length === 0 ? (
            <EmptyState icon={Share2} title={t('empty')} />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t('conversation')}</TableHead>
                  <TableHead>{t('sharedDate')}</TableHead>
                  <TableHead>{t('scope')}</TableHead>
                  <TableHead>{t('status')}</TableHead>
                  <TableHead className="w-[120px]" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {shares.map((share) => (
                  <TableRow key={share.id}>
                    <TableCell className="max-w-[220px] truncate font-medium">
                      {share.title || share.conversation_id}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {fmt.dateTime(new Date(share.created_at), {
                        year: 'numeric',
                        month: 'short',
                        day: 'numeric',
                      })}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">{share.scope}</TableCell>
                    <TableCell>
                      <span
                        className={cn(
                          'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium',
                          share.is_active
                            ? 'bg-success/15 text-success'
                            : 'bg-muted text-muted-foreground',
                        )}
                      >
                        {share.is_active ? t('active') : t('revoked')}
                      </span>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1">
                        {share.is_active && (
                          <>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-7 w-7"
                              title={t('copyLink')}
                              onClick={() => handleCopy(share)}
                            >
                              {copiedId === share.id ? (
                                <Check className="h-3.5 w-3.5 text-success" />
                              ) : (
                                <Copy className="h-3.5 w-3.5" />
                              )}
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 px-2 text-xs text-destructive hover:text-destructive"
                              onClick={() => void handleRevoke(share.id)}
                            >
                              {t('revoke')}
                            </Button>
                          </>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}

          {/* Footer count */}
          {!loading && total > 0 && <p className="text-xs text-muted-foreground">{total} total</p>}
        </div>
      </div>
    </div>
  )
}
