'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { toast } from 'sonner'
import { useTranslations } from 'next-intl'

import {
  adminDisableImAccount,
  adminEnableImAccount,
  adminListImAccounts,
  createApiClient,
  type ImAccount,
} from '@cubebox/core'

import { ImAccountDetailPanel } from '@/components/im/ImAccountDetailPanel'
import { ImAccountListItem } from '@/components/im/ImAccountListItem'
import { ImAccountToolbar } from '@/components/im/ImAccountToolbar'

const POLL_MS = 5000

export default function AdminImPage(): React.ReactElement {
  const t = useTranslations('im')
  const router = useRouter()
  const search = useSearchParams()
  const client = useMemo(() => createApiClient(''), [])
  const [accounts, setAccounts] = useState<ImAccount[]>([])
  const [loading, setLoading] = useState(true)
  const selectedId = search?.get('account') ?? null

  const load = useCallback(async () => {
    const res = await adminListImAccounts(client)
    setAccounts(res.accounts)
    setLoading(false)
  }, [client])

  useEffect(() => {
    void load()
    const id = window.setInterval(() => {
      if (document.visibilityState === 'visible') void load()
    }, POLL_MS)
    return () => window.clearInterval(id)
  }, [load])

  const selected = accounts.find((a) => a.id === selectedId) ?? accounts[0] ?? null

  function updateUrl(patch: Record<string, string | null>): void {
    const params = new URLSearchParams(search?.toString())
    for (const [k, v] of Object.entries(patch)) {
      if (v === null) params.delete(k)
      else params.set(k, v)
    }
    router.replace(`?${params.toString()}`)
  }

  if (loading) return <div className="p-6 text-sm text-muted-foreground">Loading…</div>

  if (accounts.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 p-12 text-center">
        <h2 className="text-lg font-semibold">{t('empty.admin.headline')}</h2>
        <p className="max-w-md text-sm text-muted-foreground">{t('empty.admin.description')}</p>
        <a
          href="/workspaces"
          className="rounded bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
        >
          {t('empty.admin.cta')}
        </a>
      </div>
    )
  }

  return (
    <div className="flex">
      <div className="flex-1 border-r">
        <ImAccountToolbar showConnect={false} onConnect={() => {}} count={accounts.length} />
        <ul role="listbox" className="flex flex-col">
          {accounts.map((a) => (
            <li key={a.id}>
              <ImAccountListItem
                account={a}
                selected={selected?.id === a.id}
                showWorkspaceColumn={true}
                onSelect={(id) => updateUrl({ account: id })}
              />
            </li>
          ))}
        </ul>
      </div>
      {selected && (
        <ImAccountDetailPanel
          account={selected}
          scope="admin"
          onDisable={async () => {
            await adminDisableImAccount(client, selected.id)
            toast.success(t('error.toast.disabled'))
            void load()
          }}
          onEnable={async () => {
            await adminEnableImAccount(client, selected.id)
            toast.success(t('error.toast.enabled'))
            void load()
          }}
          onDelete={() => {}}
        />
      )}
    </div>
  )
}
