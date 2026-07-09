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

import { MessagesSquare, Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { EmptyState } from '@/components/shared/EmptyState'
import { PANE_CONTENT_WIDTH, SectionHeader } from '@/components/shared/SectionHeader'
import { ImAccountDetailPanel } from '@/components/im/ImAccountDetailPanel'
import { ImAccountListItem } from '@/components/im/ImAccountListItem'
import { ImConnectWizard } from '@/components/im/ImConnectWizard'
import { PlatformLogo } from '@/components/im/PlatformLogo'
import { ALL_PLATFORMS } from '@/components/im/ImConnectWizard/platforms'
import { ListDetailLayout } from '@/components/shared/ListDetailLayout'
import { cn } from '@/lib/utils'

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
  const connectPlatform = search?.get('platform') ?? undefined
  const selectedId = search?.get('account') ?? null

  const load = useCallback(async () => {
    const res = await wsListImAccounts(client, wsId)
    setAccounts(res.accounts)
    setLoading(false)
  }, [client, wsId])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- load-on-mount + poll
    void load()
    const id = window.setInterval(() => {
      if (document.visibilityState === 'visible') void load()
    }, POLL_MS)
    return () => window.clearInterval(id)
  }, [load])

  const selected = selectedId ? (accounts.find((a) => a.id === selectedId) ?? null) : null

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
      <SectionHeader
        title={t('nav.workspaceTab')}
        description={t('panel.description')}
        action={
          <Button
            size="sm"
            className="gap-1.5"
            onClick={() => updateUrl({ action: 'connect', platform: null })}
          >
            <Plus className="size-3.5" />
            {t('action.connect')}
          </Button>
        }
      />

      {loading ? (
        <div className="flex-1 p-6 text-sm text-muted-foreground">Loading…</div>
      ) : accounts.length === 0 ? (
        <div className="flex-1 overflow-y-auto px-6 py-6">
          <div className={PANE_CONTENT_WIDTH}>
            <EmptyState
              icon={MessagesSquare}
              title={t('empty.workspace.headline')}
              description={t('empty.workspace.description')}
              action={
                <div className="flex flex-wrap justify-center gap-3">
                  {ALL_PLATFORMS.map((platform) => {
                    // eslint-disable-next-line @typescript-eslint/no-explicit-any
                    const label = t(`platform.${platform.id}.label` as any)
                    // eslint-disable-next-line @typescript-eslint/no-explicit-any
                    const coming = platform.live ? '' : t(`platform.${platform.id}.coming` as any)
                    return (
                      <button
                        key={platform.id}
                        disabled={!platform.live}
                        onClick={() =>
                          platform.live && updateUrl({ action: 'connect', platform: platform.id })
                        }
                        className={cn(
                          'flex w-28 flex-col items-center gap-2 rounded-xl border px-5 py-4 transition-all',
                          platform.live
                            ? 'cursor-pointer border-border/70 bg-card/60 shadow-sm hover:border-primary/40 hover:bg-accent hover:shadow-md active:scale-[0.98]'
                            : 'cursor-not-allowed border-border/40 bg-muted/20 opacity-60',
                        )}
                      >
                        <PlatformLogo platform={platform.id} className="size-8" />
                        <span className="text-xs font-medium text-foreground">{label}</span>
                        {!platform.live && (
                          <span className="text-[10px] text-muted-foreground/60">{coming}</span>
                        )}
                      </button>
                    )
                  })}
                </div>
              }
            />
          </div>
        </div>
      ) : (
        <ListDetailLayout
          selected={selected !== null}
          list={
            <div className="flex flex-col gap-2">
              {accounts.map((a) => (
                <ImAccountListItem
                  key={a.id}
                  account={a}
                  selected={selected?.id === a.id}
                  showWorkspaceColumn={false}
                  onSelect={(id) => updateUrl({ account: id })}
                />
              ))}
            </div>
          }
          detail={
            selected ? (
              <ImAccountDetailPanel
                account={selected}
                scope="workspace"
                backLabel={t('back')}
                onBack={() => updateUrl({ account: null })}
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
            ) : null
          }
          placeholder={t('selectHint')}
        />
      )}

      {wizardOpen && (
        <ImConnectWizard
          wsId={wsId}
          open
          initialPlatformId={connectPlatform}
          onClose={() => updateUrl({ action: null, platform: null })}
          onSuccess={() => {
            updateUrl({ action: null, platform: null })
            void load()
          }}
        />
      )}
      <Dialog open={deleteCandidate !== null} onOpenChange={(o) => !o && setDeleteCandidate(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('deleteDialog.title')}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            {t('deleteDialog.body', {
              platform: t(`platform.${deleteCandidate?.platform ?? 'feishu'}.label` as any),
            })}
          </p>
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
