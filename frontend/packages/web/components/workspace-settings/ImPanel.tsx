'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { toast } from 'sonner'
import { useTranslations } from 'next-intl'

import {
  createApiClient,
  wsDeleteImAccount,
  wsDisableImAccount,
  wsEnableImAccount,
  wsListImAccounts,
  type ImAccount,
} from '@cubebox/core'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { ImAccountDetailPanel } from '@/components/im/ImAccountDetailPanel'
import { ImAccountListItem } from '@/components/im/ImAccountListItem'
import { ImAccountToolbar } from '@/components/im/ImAccountToolbar'
import { ImConnectWizard } from '@/components/im/ImConnectWizard'

interface Props {
  wsId: string
}

const POLL_MS = 5000

export function ImPanel({ wsId }: Props): React.ReactElement {
  const t = useTranslations('im')
  const router = useRouter()
  const search = useSearchParams()
  const client = useMemo(() => createApiClient(''), [])
  const [accounts, setAccounts] = useState<ImAccount[]>([])
  const [loading, setLoading] = useState(true)
  const [deleteCandidate, setDeleteCandidate] = useState<ImAccount | null>(null)
  const [deleteText, setDeleteText] = useState('')
  const wizardOpen = search?.get('action') === 'connect'
  const selectedId = search?.get('account') ?? null

  const load = useCallback(async () => {
    const res = await wsListImAccounts(client, wsId)
    setAccounts(res.accounts)
    setLoading(false)
  }, [client, wsId])

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

  if (loading) {
    return <div className="flex-1 p-6 text-sm text-muted-foreground">Loading…</div>
  }

  if (accounts.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-12 text-center">
        <h2 className="text-lg font-semibold">{t('empty.workspace.headline')}</h2>
        <p className="max-w-md text-sm text-muted-foreground">{t('empty.workspace.description')}</p>
        <button
          type="button"
          className="rounded bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
          onClick={() => updateUrl({ action: 'connect' })}
        >
          {t('empty.workspace.cta', { platform: t('platform.feishu.label') })}
        </button>
        <p className="text-xs text-muted-foreground">{t('empty.workspace.comingNote')}</p>
        {wizardOpen && (
          <ImConnectWizard
            wsId={wsId}
            open
            onClose={() => updateUrl({ action: null })}
            onSuccess={() => {
              updateUrl({ action: null })
              void load()
            }}
          />
        )}
      </div>
    )
  }

  return (
    <div className="flex flex-1">
      <div className="flex-1 border-r">
        <ImAccountToolbar
          showConnect
          onConnect={() => updateUrl({ action: 'connect' })}
          count={accounts.length}
        />
        <ul role="listbox" className="flex flex-col">
          {accounts.map((a) => (
            <li key={a.id}>
              <ImAccountListItem
                account={a}
                selected={selected?.id === a.id}
                showWorkspaceColumn={false}
                onSelect={(id) => updateUrl({ account: id })}
              />
            </li>
          ))}
        </ul>
      </div>
      {selected && (
        <ImAccountDetailPanel
          account={selected}
          scope="workspace"
          onDisable={async () => {
            await wsDisableImAccount(client, wsId, selected.id)
            toast.success(t('error.toast.disabled'))
            void load()
          }}
          onEnable={async () => {
            await wsEnableImAccount(client, wsId, selected.id)
            toast.success(t('error.toast.enabled'))
            void load()
          }}
          onDelete={() => {
            setDeleteCandidate(selected)
            setDeleteText('')
          }}
        />
      )}
      {wizardOpen && (
        <ImConnectWizard
          wsId={wsId}
          open
          onClose={() => updateUrl({ action: null })}
          onSuccess={() => {
            updateUrl({ action: null })
            void load()
          }}
        />
      )}
      <Dialog open={deleteCandidate !== null} onOpenChange={(o) => !o && setDeleteCandidate(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('deleteDialog.title')}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">{t('deleteDialog.body')}</p>
          <p className="text-sm">
            {t('deleteDialog.confirmGate', {
              botName: deleteCandidate?.external_account_id ?? '',
            })}
          </p>
          <Input
            autoFocus
            value={deleteText}
            onChange={(e) => setDeleteText(e.target.value)}
            placeholder={deleteCandidate?.external_account_id ?? ''}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteCandidate(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={
                deleteCandidate === null || deleteText !== deleteCandidate.external_account_id
              }
              onClick={async () => {
                if (deleteCandidate === null) return
                await wsDeleteImAccount(client, wsId, deleteCandidate.id)
                toast.success(t('error.toast.deleted'))
                setDeleteCandidate(null)
                updateUrl({ account: null })
                void load()
              }}
            >
              {t('action.delete')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
