'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Hash, MessageCircle } from 'lucide-react'
import { createApiClient, wsListImAccounts } from '@cubeplex/core'
import type { ImAccount } from '@cubeplex/core'

interface ReadOnlyImChannelDestinationProps {
  wsId: string
  accountId: string
  channelId: string
  scopeKey: string
  scopeKind: string | null
}

/**
 * Compact, non-editable card shown in the schedule edit form when the row's
 * `target_mode === 'im_channel'`. Resolves the IM account so we can show the
 * platform + bot label, then renders the channel id + scope as a read-only
 * pill. There's no channel-lookup endpoint, so the channel id is shown verbatim.
 */
export function ReadOnlyImChannelDestination({
  wsId,
  accountId,
  channelId,
  scopeKey,
  scopeKind,
}: ReadOnlyImChannelDestinationProps) {
  const t = useTranslations('scheduledTasks')
  const client = useMemo(() => createApiClient(''), [])
  const [account, setAccount] = useState<ImAccount | null>(null)

  useEffect(() => {
    let cancelled = false
    wsListImAccounts(client, wsId)
      .then((data) => {
        if (!cancelled) {
          setAccount(data.accounts.find((a) => a.id === accountId) ?? null)
        }
      })
      .catch(() => {
        if (!cancelled) setAccount(null)
      })
    return () => {
      cancelled = true
    }
  }, [client, wsId, accountId])

  const platformLabel =
    account?.bot_app_name ?? account?.platform ?? t('imDestinationAccountFallback')

  return (
    <div
      className="rounded-lg border border-warning-border bg-warning-surface p-3"
      data-testid="im-channel-destination-readonly"
    >
      <div className="flex items-center gap-2">
        <MessageCircle className="size-3.5 shrink-0 text-warning-fg" />
        <span className="text-xs font-medium text-foreground">{t('imDestinationTitle')}</span>
        <span className="rounded border border-warning-border bg-warning-surface px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-warning-fg">
          {t('imDestinationReadOnlyBadge')}
        </span>
      </div>
      <div className="mt-2 flex flex-col gap-1 text-xs text-muted-foreground">
        <div className="flex items-center gap-1.5">
          <span className="font-medium text-foreground">{platformLabel}</span>
          {account?.bot_avatar_url && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={account.bot_avatar_url} alt="" className="size-4 rounded-sm" />
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <Hash className="size-3 shrink-0" />
          <span className="font-mono">{channelId}</span>
        </div>
        <div className="text-[11px]">
          {t('imDestinationScope', {
            kind: scopeKind ?? '—',
            key: scopeKey,
          })}
        </div>
      </div>
      <p className="mt-2 text-[11px] italic text-muted-foreground">
        {t('imDestinationReadOnlyHint')}
      </p>
    </div>
  )
}
