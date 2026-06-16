'use client'

import { useTranslations } from 'next-intl'

import type { ImAccount } from '@cubebox/core'
import { RailCard } from '@/components/shared/RailCard'

import { ImAccountStatusPill } from './ImAccountStatusPill'
import { PlatformLogo } from './PlatformLogo'

interface Props {
  account: ImAccount
  selected: boolean
  showWorkspaceColumn: boolean
  onSelect: (id: string) => void
}

// Tiny relative-time helper. Avoids pulling in date-fns just for one
// row of "12m / 3h / 5d" text.
function relativeFromIso(iso: string | null): string {
  if (iso === null) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  const diffMs = Date.now() - then
  if (diffMs < 0) return '0s'
  const s = Math.floor(diffMs / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h`
  return `${Math.floor(h / 24)}d`
}

/**
 * One compact row in the IM accounts list. Used by both workspace and
 * admin scopes; toggle ``showWorkspaceColumn`` for the admin view.
 */
export function ImAccountListItem({
  account,
  selected,
  showWorkspaceColumn,
  onSelect,
}: Props): React.ReactElement {
  const t = useTranslations('im')
  const last = relativeFromIso(account.runtime.last_inbound_at)
  const title = account.bot_app_name ?? account.external_account_id
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const platformLabel = t(`platform.${account.platform}.label` as any)
  const metaParts = [account.delivery_mode, last]
  if (showWorkspaceColumn) metaParts.unshift(account.workspace_id)
  return (
    <RailCard
      selected={selected}
      onSelect={() => onSelect(account.id)}
      leading={
        account.bot_avatar_url ? (
          <img
            src={account.bot_avatar_url}
            alt={title}
            className="size-6 rounded-full object-cover"
          />
        ) : (
          <PlatformLogo platform={account.platform} className="size-6" />
        )
      }
      title={title}
      badge={
        <ImAccountStatusPill
          connectionState={account.runtime.connection_state}
          enabled={account.enabled}
        />
      }
      secondary={
        <span className="inline-flex items-center gap-1">
          <PlatformLogo platform={account.platform} className="size-3 opacity-60" />
          <span>{platformLabel}</span>
          {account.bot_app_name && (
            <>
              <span className="mx-0.5">·</span>
              <span>{account.external_account_id}</span>
            </>
          )}
        </span>
      }
      meta={metaParts.join(' · ')}
    />
  )
}
