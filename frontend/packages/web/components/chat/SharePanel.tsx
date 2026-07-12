'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslations, useFormatter } from 'next-intl'
import { Check, Copy, Link, Share2 } from 'lucide-react'
import {
  createApiClient,
  createShare,
  listConversationShares,
  revokeShare,
  type ConversationShare,
  type ShareScope,
} from '@cubeplex/core'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { cn } from '@/lib/utils'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'

const SCOPE_OPTIONS: ShareScope[] = ['public', 'org', 'workspace']

interface SharePanelProps {
  conversationId: string
}

export function SharePanel({ conversationId }: SharePanelProps) {
  const t = useTranslations('sharePanel')
  const format = useFormatter()
  const { workspaceId } = useWorkspaceContext()
  const [open, setOpen] = useState(false)
  const [shares, setShares] = useState<ConversationShare[]>([])
  const [loading, setLoading] = useState(false)
  const [creating, setCreating] = useState(false)
  const [copiedId, setCopiedId] = useState<string | null>(null)
  const [scope, setScope] = useState<ShareScope>('public')

  const copyText = useCallback(async (text: string) => {
    if (navigator.clipboard) {
      await navigator.clipboard.writeText(text)
      return
    }
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  }, [])

  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(workspaceId)
    return c
  }, [workspaceId])

  const loadShares = useCallback(async () => {
    setLoading(true)
    try {
      const data = await listConversationShares(client, conversationId)
      setShares(data)
    } finally {
      setLoading(false)
    }
  }, [client, conversationId])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- load when panel opens
    if (open) void loadShares()
  }, [open, loadShares])

  const handleCreate = useCallback(async () => {
    setCreating(true)
    try {
      const share = await createShare(client, conversationId, scope)
      setShares((prev) => [share, ...prev])
      await copyText(window.location.origin + share.url)
      setCopiedId(share.id)
      setTimeout(() => setCopiedId(null), 2000)
    } finally {
      setCreating(false)
    }
  }, [client, conversationId, scope, copyText])

  const handleRevoke = useCallback(
    async (shareId: string) => {
      if (!confirm(t('revokeConfirm'))) return
      const updated = await revokeShare(client, shareId)
      setShares((prev) => prev.map((s) => (s.id === updated.id ? updated : s)))
    },
    [client, t],
  )

  const handleCopy = useCallback(
    async (url: string, id: string) => {
      await copyText(window.location.origin + url)
      setCopiedId(id)
      setTimeout(() => setCopiedId(null), 2000)
    },
    [copyText],
  )

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        className={cn(
          'inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs',
          'text-muted-foreground hover:text-foreground hover:bg-muted transition-colors',
        )}
      >
        <Share2 className="size-3.5" />
        {t('share')}
      </PopoverTrigger>
      <PopoverContent side="bottom" align="end" sideOffset={8} className="w-80 p-4 shadow-lg">
        <div className="space-y-3">
          {loading ? (
            <p className="text-xs text-muted-foreground text-center py-2">...</p>
          ) : shares.length === 0 ? (
            <p className="text-xs text-muted-foreground text-center py-2">{t('noShares')}</p>
          ) : (
            <ul className="space-y-2 max-h-48 overflow-y-auto">
              {shares.map((s) => (
                <li
                  key={s.id}
                  className={cn(
                    'flex items-center justify-between rounded-lg border px-3 py-2',
                    s.is_active
                      ? 'border-border bg-card'
                      : 'border-border/50 bg-muted/30 opacity-60',
                  )}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <Link className="size-3 shrink-0 text-muted-foreground" />
                      <span className="text-xs truncate">
                        {t('sharedAt', {
                          date: format.dateTime(new Date(s.created_at), {
                            month: 'short',
                            day: 'numeric',
                            hour: 'numeric',
                            minute: '2-digit',
                          }),
                        })}
                      </span>
                      <span className="text-[10px] text-muted-foreground/60">{s.scope}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-1 ml-2 shrink-0">
                    {s.is_active && (
                      <>
                        <button
                          onClick={() => void handleCopy(s.url, s.id)}
                          className="rounded p-1 text-muted-foreground hover:text-foreground hover:bg-muted"
                          title={t('copyLink')}
                          type="button"
                        >
                          {copiedId === s.id ? (
                            <Check className="size-3.5 text-success" />
                          ) : (
                            <Copy className="size-3.5" />
                          )}
                        </button>
                        <button
                          onClick={() => void handleRevoke(s.id)}
                          className="rounded px-1.5 py-0.5 text-[10px] text-destructive hover:bg-destructive/10"
                          type="button"
                        >
                          {t('revoke')}
                        </button>
                      </>
                    )}
                    {!s.is_active && (
                      <span className="text-[10px] text-muted-foreground">{t('revoked')}</span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}

          {/* Scope selector + create button */}
          <div className="flex items-center gap-2">
            <select
              value={scope}
              onChange={(e) => setScope(e.target.value as ShareScope)}
              className={cn(
                'h-8 rounded-md border border-border bg-background px-2 text-xs',
                'text-foreground focus:outline-none focus:ring-1 focus:ring-ring',
              )}
            >
              {SCOPE_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {t(`scope.${s}`)}
                </option>
              ))}
            </select>
            <button
              onClick={() => void handleCreate()}
              disabled={creating}
              className={cn(
                'flex-1 rounded-lg border border-dashed border-border px-3 py-1.5',
                'text-xs text-muted-foreground hover:text-foreground hover:border-foreground/30',
                'transition-colors disabled:opacity-50',
              )}
              type="button"
            >
              {creating ? t('creating') : t('createShare')}
            </button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}
