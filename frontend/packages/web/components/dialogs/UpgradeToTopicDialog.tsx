'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { Info, X } from 'lucide-react'
import {
  ApiError,
  createApiClient,
  useAuthStore,
  useConversationStore,
  useMemberStore,
  useTopicStore,
  type WsMember,
} from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { cn } from '@/lib/utils'
import { WorkspaceMemberPicker } from '@/components/dialogs/WorkspaceMemberPicker'

interface UpgradeToTopicDialogProps {
  wsId: string
  conversationId: string
  initialTitle: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

type SandboxMode = 'dedicated' | 'creator'

export function UpgradeToTopicDialog({
  wsId,
  conversationId,
  initialTitle,
  open,
  onOpenChange,
}: UpgradeToTopicDialogProps): React.ReactElement {
  const t = useTranslations('topics')
  const tDialog = useTranslations('topics.upgradeDialog')
  const router = useRouter()
  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])
  const currentUserId = useAuthStore((s) => s.user?.id ?? null)
  const { wsMembers, loadWsMembers } = useMemberStore()
  const upgradeConversationToTopic = useTopicStore((s) => s.upgradeConversationToTopic)
  const fetchTopicList = useTopicStore((s) => s.fetchList)
  const fetchConversationList = useConversationStore((s) => s.fetchList)

  const [title, setTitle] = useState(initialTitle)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [sandboxMode, setSandboxMode] = useState<SandboxMode>('creator')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      /* eslint-disable react-hooks/set-state-in-effect */
      setTitle(initialTitle)
      setSelected(new Set())
      setSandboxMode('creator')
      setError(null)
      setSubmitting(false)
      /* eslint-enable react-hooks/set-state-in-effect */
      void loadWsMembers(client, wsId)
    }
  }, [open, initialTitle, client, wsId, loadWsMembers])

  const invitable: WsMember[] = useMemo(
    () => wsMembers.filter((m) => m.user_id !== currentUserId),
    [wsMembers, currentUserId],
  )

  const toggleMember = (userId: string): void => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(userId)) next.delete(userId)
      else next.add(userId)
      return next
    })
  }

  const canSubmit = title.trim().length > 0 && !submitting

  const handleSubmit = async (): Promise<void> => {
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      await upgradeConversationToTopic(client, conversationId, {
        title: title.trim(),
        sandbox_mode: sandboxMode,
        member_user_ids: Array.from(selected),
      })
      // Refresh sidebar lists so the conversation moves under its new topic
      // and the topic appears in the topic list.
      void fetchTopicList(client).catch(() => undefined)
      void fetchConversationList(client).catch(() => undefined)
      onOpenChange(false)
      router.refresh()
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const msg = (e.message || '').toLowerCase()
        if (msg.includes('binding')) {
          setError(tDialog('externalBindingError'))
        } else {
          setError(tDialog('alreadyTopicError'))
        }
      } else {
        setError(tDialog('upgradeError'))
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop
          className={cn(
            'fixed inset-0 z-50 bg-black/40 backdrop-blur-sm',
            'data-[ending-style]:opacity-0',
            'data-[starting-style]:opacity-0',
            'transition-opacity duration-200',
          )}
        />
        <DialogPrimitive.Popup
          className={cn(
            'fixed left-1/2 top-1/2 z-50',
            'w-[min(460px,calc(100vw-32px))]',
            '-translate-x-1/2 -translate-y-1/2',
            'rounded-xl border border-border bg-popover p-5',
            'text-popover-foreground shadow-2xl',
            'data-[ending-style]:opacity-0',
            'data-[starting-style]:opacity-0',
            'transition-opacity duration-200',
          )}
          data-testid="upgrade-to-topic-dialog"
        >
          <div className="flex items-start justify-between gap-3">
            <DialogPrimitive.Title className="text-base font-semibold">
              {tDialog('title')}
            </DialogPrimitive.Title>
            <DialogPrimitive.Close
              render={
                <button
                  type="button"
                  aria-label="close"
                  className={cn(
                    'rounded-md p-1 text-muted-foreground',
                    'hover:bg-muted hover:text-foreground',
                  )}
                >
                  <X className="size-4" />
                </button>
              }
            />
          </div>

          <div className="mt-4 flex flex-col gap-4">
            <div
              className={cn(
                'flex items-start gap-2 rounded-md border border-info-border',
                'bg-info-surface px-2.5 py-2 text-xs text-info-fg leading-relaxed',
              )}
            >
              <Info className="size-3.5 shrink-0 mt-0.5" />
              <span>{tDialog('irreversibleWarning')}</span>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="upgrade-topic-title">{tDialog('titleLabel')}</Label>
              <Input
                id="upgrade-topic-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder={tDialog('titlePlaceholder')}
                autoFocus
                maxLength={120}
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label>{tDialog('membersLabel')}</Label>
              <WorkspaceMemberPicker
                invitable={invitable}
                selected={selected}
                onToggle={toggleMember}
                emptyText={tDialog('membersEmpty')}
              />
            </div>

            <div className="flex flex-col gap-2">
              <Label>{tDialog('sandboxLabel')}</Label>
              <RadioGroup
                value={sandboxMode}
                onValueChange={(v) => setSandboxMode((v as SandboxMode) ?? 'creator')}
              >
                <label className="flex cursor-pointer flex-col gap-0.5 rounded-md px-2 py-1.5 hover:bg-accent/40">
                  <div className="flex items-center gap-2 text-sm">
                    <RadioGroupItem value="creator" />
                    <span>{t('sandboxCreator')}</span>
                  </div>
                  <p className="pl-6 text-xs text-muted-foreground leading-relaxed">
                    {t('sandboxCreatorDescription')}
                  </p>
                </label>
                <label className="flex cursor-pointer flex-col gap-0.5 rounded-md px-2 py-1.5 hover:bg-accent/40">
                  <div className="flex items-center gap-2 text-sm">
                    <RadioGroupItem value="dedicated" />
                    <span>{t('sandboxDedicated')}</span>
                  </div>
                  <p className="pl-6 text-xs text-muted-foreground leading-relaxed">
                    {t('sandboxDedicatedDescription')}
                  </p>
                </label>
              </RadioGroup>
            </div>

            {error && (
              <div
                className={cn(
                  'rounded-md border border-destructive/30',
                  'bg-destructive/5 px-2.5 py-1.5',
                  'text-xs text-destructive',
                )}
              >
                {error}
              </div>
            )}
          </div>

          <div className="mt-5 flex items-center justify-end gap-2">
            <DialogPrimitive.Close
              render={
                <Button type="button" variant="ghost" size="sm" disabled={submitting}>
                  {tDialog('cancel')}
                </Button>
              }
            />
            <Button
              type="button"
              size="sm"
              onClick={() => void handleSubmit()}
              disabled={!canSubmit}
            >
              {submitting ? tDialog('upgrading') : tDialog('upgrade')}
            </Button>
          </div>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
