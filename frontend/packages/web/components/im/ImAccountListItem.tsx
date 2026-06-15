'use client'

import type { ImAccount } from '@cubebox/core'
import { cn } from '@/lib/utils'

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
  const last = relativeFromIso(account.runtime.last_inbound_at)
  return (
    <button
      type="button"
      role="option"
      aria-selected={selected}
      onClick={() => onSelect(account.id)}
      className={cn(
        'flex w-full items-center gap-3 border-b border-border/40 px-3 py-2.5 text-left text-sm transition-colors',
        selected ? 'bg-accent' : 'hover:bg-accent/50',
      )}
    >
      {account.bot_avatar_url ? (
        <img
          src={account.bot_avatar_url}
          alt={account.bot_app_name ?? account.external_account_id}
          className="h-6 w-6 shrink-0 rounded-full object-cover"
        />
      ) : (
        <PlatformLogo platform={account.platform} className="h-5 w-5 shrink-0" />
      )}
      <ImAccountStatusPill
        connectionState={account.runtime.connection_state}
        enabled={account.enabled}
      />
      <span className="font-medium">{account.bot_app_name ?? account.external_account_id}</span>
      {account.bot_app_name ? (
        <span className="text-xs text-muted-foreground">{account.external_account_id}</span>
      ) : null}
      {showWorkspaceColumn && (
        <span className="text-xs text-muted-foreground">{account.workspace_id}</span>
      )}
      <span className="text-xs text-muted-foreground">· {account.delivery_mode}</span>
      <span className="ml-auto text-xs text-muted-foreground">{last}</span>
    </button>
  )
}
