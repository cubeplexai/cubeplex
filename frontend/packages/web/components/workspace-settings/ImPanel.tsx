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

import { Plus } from 'lucide-react'
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
import { PlatformLogo } from '@/components/im/PlatformLogo'
import { ALL_PLATFORMS } from '@/components/im/ImConnectWizard/platforms'

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

  return (
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-center justify-between border-b border-border/70 px-6 py-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">{t('nav.workspaceTab')}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">{t('panel.description')}</p>
        </div>
        <Button size="sm" className="gap-1.5" onClick={() => updateUrl({ action: 'connect' })}>
          <Plus className="size-3.5" />
          {t('action.connect')}
        </Button>
      </header>

      {loading ? (
        <div className="flex-1 p-6 text-sm text-muted-foreground">Loading…</div>
      ) : accounts.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-6 px-12 py-16 text-center">
          <div className="flex flex-col items-center gap-2">
            <p className="text-sm font-medium text-muted-foreground">
              {t('empty.workspace.description')}
            </p>
            <p className="text-xs text-muted-foreground/70">{t('empty.workspace.comingNote')}</p>
          </div>
          <div className="flex gap-3">
            {ALL_PLATFORMS.map((platform) => (
              <button
                key={platform.id}
                disabled={!platform.live}
                onClick={() => platform.live && updateUrl({ action: 'connect' })}
                className={[
                  'flex flex-col items-center gap-2 rounded-xl border px-5 py-4 transition-colors',
                  platform.live
                    ? 'border-border/70 bg-card/60 shadow-sm hover:bg-accent cursor-pointer'
                    : 'border-border/40 bg-muted/20 opacity-50 cursor-not-allowed',
                ].join(' ')}
              >
                <PlatformLogo
                  platform={platform.id as 'feishu' | 'slack' | 'teams'}
                  className="size-8"
                />
                <span className="text-xs font-medium text-muted-foreground">
                  {platform.id === 'feishu'
                    ? t('platform.feishu.label')
                    : platform.id === 'slack'
                      ? t('platform.slack.label')
                      : t('platform.teams.label')}
                </span>
                {!platform.live && (
                  <span className="text-[10px] text-muted-foreground/60">Coming soon</span>
                )}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="flex flex-1 overflow-hidden">
          <div className="flex-1 border-r">
            <ImAccountToolbar
              showConnect={false}
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
        </div>
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
