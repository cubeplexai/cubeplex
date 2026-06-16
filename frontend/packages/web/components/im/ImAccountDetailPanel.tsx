'use client'

import { useTranslations } from 'next-intl'

import type { ImAccount } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { DetailPanel } from '@/components/shared/DetailPanel'

import { ImAccountStatusPill } from './ImAccountStatusPill'

interface Props {
  account: ImAccount
  scope: 'workspace' | 'admin'
  onDisable: () => void
  onEnable: () => void
  onDelete: () => void
  onBack?: () => void
  backLabel?: string
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
  onBack,
  backLabel,
}: Props): React.ReactElement {
  const t = useTranslations('im')
  return (
    <DetailPanel
      onBack={onBack}
      backLabel={backLabel}
      title={account.bot_app_name ?? account.external_account_id}
      badge={
        <ImAccountStatusPill
          connectionState={account.runtime.connection_state}
          enabled={account.enabled}
        />
      }
      subtitle={account.bot_app_name ? account.external_account_id : undefined}
      actions={
        <>
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
        </>
      }
    >
      <div className="flex max-w-2xl flex-col gap-4 text-sm">
        <section>
          <h3 className="mb-2 text-xs uppercase text-muted-foreground">Identity</h3>
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-xs">
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
      </div>
    </DetailPanel>
  )
}
