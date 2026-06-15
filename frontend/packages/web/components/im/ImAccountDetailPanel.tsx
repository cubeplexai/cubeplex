'use client'

import { useTranslations } from 'next-intl'

import type { ImAccount } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'

import { ImAccountStatusPill } from './ImAccountStatusPill'

interface Props {
  account: ImAccount
  scope: 'workspace' | 'admin'
  onDisable: () => void
  onEnable: () => void
  onDelete: () => void
}

/**
 * Detail sidebar / inline panel for a single IM account. Action set is
 * driven by ``scope`` per spec §4. Workspace gets Disable/Enable +
 * Delete; admin gets Disable/Enable only.
 */
export function ImAccountDetailPanel({
  account,
  scope,
  onDisable,
  onEnable,
  onDelete,
}: Props): React.ReactElement {
  const t = useTranslations('im')
  return (
    <aside className="flex w-72 flex-col gap-4 p-4 text-sm">
      <header className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          {account.bot_avatar_url ? (
            <img
              src={account.bot_avatar_url}
              alt={account.bot_app_name ?? account.external_account_id}
              className="h-8 w-8 shrink-0 rounded-full object-cover"
            />
          ) : null}
          <div className="min-w-0">
            <strong className="block truncate">
              {account.bot_app_name ?? account.external_account_id}
            </strong>
            {account.bot_app_name ? (
              <span className="block truncate text-xs text-muted-foreground">
                {account.external_account_id}
              </span>
            ) : null}
          </div>
        </div>
        <ImAccountStatusPill
          connectionState={account.runtime.connection_state}
          enabled={account.enabled}
        />
      </header>

      <section>
        <h3 className="mb-2 text-xs uppercase text-muted-foreground">Identity</h3>
        <dl className="grid grid-cols-2 gap-y-1 text-xs">
          <dt className="text-muted-foreground">Acting as</dt>
          <dd>{account.acting_user_id}</dd>
          <dt className="text-muted-foreground">Bot open_id</dt>
          <dd className="truncate">{account.runtime.bot_open_id ?? '—'}</dd>
          <dt className="text-muted-foreground">Mode</dt>
          <dd>{account.delivery_mode}</dd>
        </dl>
      </section>

      <Separator />

      <section>
        <h3 className="mb-2 text-xs uppercase text-muted-foreground">Identity gate (24h)</h3>
        <p className="text-xs">
          {t('runtime.gate.matched', { count: account.runtime.matched_24h })}
          {' · '}
          {t('runtime.gate.rejected', { count: account.runtime.rejected_24h })}
        </p>
      </section>

      <Separator />

      <section className="mt-auto flex flex-col gap-2">
        {account.enabled ? (
          <Button variant="outline" size="sm" onClick={onDisable}>
            {t('action.disable')}
          </Button>
        ) : (
          <Button variant="outline" size="sm" onClick={onEnable}>
            {t('action.enable')}
          </Button>
        )}
        {scope === 'workspace' && (
          <Button variant="destructive" size="sm" onClick={onDelete}>
            {t('action.delete')}
          </Button>
        )}
      </section>
    </aside>
  )
}
