'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { AlertTriangle, X } from 'lucide-react'
import {
  createApiClient,
  useAuthStore,
  useMemberStore,
  useTopicStore,
  type WsMember,
} from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

interface CreateGroupChatDialogProps {
  wsId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

type SandboxMode = 'dedicated' | 'creator'

export function CreateGroupChatDialog({
  wsId,
  open,
  onOpenChange,
}: CreateGroupChatDialogProps): React.ReactElement {
  const t = useTranslations('topics')
  const tDialog = useTranslations('topics.createDialog')
  const router = useRouter()
  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])
  const currentUserId = useAuthStore((s) => s.user?.id ?? null)
  const { wsMembers, loadWsMembers } = useMemberStore()
  const createTopic = useTopicStore((s) => s.create)

  const [title, setTitle] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [sandboxMode, setSandboxMode] = useState<SandboxMode>('dedicated')
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      /* eslint-disable react-hooks/set-state-in-effect */
      setTitle('')
      setSelected(new Set())
      setSandboxMode('dedicated')
      setError(null)
      setCreating(false)
      /* eslint-enable react-hooks/set-state-in-effect */
      void loadWsMembers(client, wsId)
    }
  }, [open, client, wsId, loadWsMembers])

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

  const canSubmit = title.trim().length > 0 && !creating

  const handleSubmit = async (): Promise<void> => {
    if (!canSubmit) return
    setCreating(true)
    setError(null)
    try {
      const { conversationId } = await createTopic(client, {
        title: title.trim(),
        sandbox_mode: sandboxMode,
        member_user_ids: Array.from(selected),
      })
      onOpenChange(false)
      router.push(`/w/${wsId}/conversations/${conversationId}`)
    } catch {
      setError(tDialog('createError'))
    } finally {
      setCreating(false)
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
          data-testid="create-group-chat-dialog"
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
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="group-chat-title">{tDialog('titleLabel')}</Label>
              <Input
                id="group-chat-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder={tDialog('titlePlaceholder')}
                autoFocus
                maxLength={120}
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label>{tDialog('membersLabel')}</Label>
              {invitable.length === 0 ? (
                <p className="text-xs text-muted-foreground">{tDialog('membersEmpty')}</p>
              ) : (
                <ScrollArea
                  className={cn('max-h-40 rounded-md border border-border bg-background/50')}
                >
                  <ul className="py-1">
                    {invitable.map((m) => {
                      const checked = selected.has(m.user_id)
                      return (
                        <li key={m.user_id}>
                          <label
                            className={cn(
                              'flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm',
                              'hover:bg-accent/50',
                            )}
                          >
                            <Checkbox
                              checked={checked}
                              onCheckedChange={() => toggleMember(m.user_id)}
                            />
                            <span className="flex-1 truncate">{m.display_name || m.email}</span>
                            {m.display_name && (
                              <span className="truncate text-xs text-muted-foreground">
                                {m.email}
                              </span>
                            )}
                          </label>
                        </li>
                      )
                    })}
                  </ul>
                </ScrollArea>
              )}
            </div>

            <div className="flex flex-col gap-2">
              <Label>{tDialog('sandboxLabel')}</Label>
              <RadioGroup
                value={sandboxMode}
                onValueChange={(v) => setSandboxMode((v as SandboxMode) ?? 'dedicated')}
              >
                <label className="flex cursor-pointer items-center gap-2 text-sm">
                  <RadioGroupItem value="dedicated" />
                  <span>{t('sandboxDedicated')}</span>
                </label>
                <label className="flex cursor-pointer items-center gap-2 text-sm">
                  <RadioGroupItem value="creator" />
                  <span>{t('sandboxCreator')}</span>
                </label>
              </RadioGroup>
              {sandboxMode === 'creator' && (
                <div
                  className={cn(
                    'flex items-start gap-2 rounded-md border border-warning-border',
                    'bg-warning-surface px-2.5 py-2 text-xs text-warning-fg',
                  )}
                >
                  <AlertTriangle className="size-3.5 shrink-0 mt-0.5" />
                  <span>{t('sandboxWarning')}</span>
                </div>
              )}
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
                <Button type="button" variant="ghost" size="sm" disabled={creating}>
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
              {creating ? tDialog('creating') : tDialog('create')}
            </Button>
          </div>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
