'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { ArrowUp, LogOut, UserPlus, X } from 'lucide-react'
import {
  createApiClient,
  useAuthStore,
  useMemberStore,
  useTopicStore,
  type TopicParticipant,
  type WsMember,
} from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { WorkspaceMemberPicker } from '@/components/dialogs/WorkspaceMemberPicker'
import { Avatar } from '@/components/ui/avatar-resolved'
import { cn } from '@/lib/utils'

interface MemberPanelProps {
  wsId: string
  topicId: string
  onClose: () => void
}

export function MemberPanel({ wsId, topicId, onClose }: MemberPanelProps): React.ReactElement {
  const t = useTranslations('topics')
  const tPanel = useTranslations('topics.memberPanel')
  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  const currentUserId = useAuthStore((s) => s.user?.id ?? null)
  const { wsMembers, loadWsMembers } = useMemberStore()
  const { topicParticipants, fetchDetail, addMembers, removeMember, updateParticipantRole } =
    useTopicStore()
  const participants: TopicParticipant[] = useMemo(
    () => topicParticipants[topicId] ?? [],
    [topicParticipants, topicId],
  )

  const [invitePickerOpen, setInvitePickerOpen] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    void fetchDetail(client, topicId).catch(() => undefined)
    void loadWsMembers(client, wsId)
  }, [client, wsId, topicId, fetchDetail, loadWsMembers])

  const currentRole: 'owner' | 'member' | null = useMemo(() => {
    const me = participants.find((p) => p.user_id === currentUserId)
    return me ? me.role : null
  }, [participants, currentUserId])
  const isOwner = currentRole === 'owner'
  const ownerCount = participants.filter((p) => p.role === 'owner').length
  const isSoleOwner = isOwner && ownerCount <= 1

  const participantIds = useMemo(() => new Set(participants.map((p) => p.user_id)), [participants])
  const invitable: WsMember[] = useMemo(
    () => wsMembers.filter((m) => !participantIds.has(m.user_id)),
    [wsMembers, participantIds],
  )
  const memberByUserId = useMemo(() => {
    const map = new Map<string, WsMember>()
    for (const m of wsMembers) map.set(m.user_id, m)
    return map
  }, [wsMembers])

  const displayNameOf = (userId: string): string => {
    const m = memberByUserId.get(userId)
    return m?.display_name || m?.email || userId
  }

  const toggleInvite = (userId: string): void => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(userId)) next.delete(userId)
      else next.add(userId)
      return next
    })
  }

  const handleInvite = async (): Promise<void> => {
    if (selected.size === 0) return
    setBusy('invite')
    setError(null)
    try {
      await addMembers(client, topicId, Array.from(selected))
      setSelected(new Set())
      setInvitePickerOpen(false)
    } catch {
      setError(tPanel('inviteError'))
    } finally {
      setBusy(null)
    }
  }

  const handleRemove = async (userId: string): Promise<void> => {
    setBusy(`remove:${userId}`)
    setError(null)
    try {
      await removeMember(client, topicId, userId)
    } catch {
      setError(tPanel('removeError'))
    } finally {
      setBusy(null)
    }
  }

  const handlePromote = async (userId: string): Promise<void> => {
    setBusy(`promote:${userId}`)
    setError(null)
    try {
      await updateParticipantRole(client, topicId, userId, 'owner')
    } catch {
      setError(tPanel('promoteError'))
    } finally {
      setBusy(null)
    }
  }

  const handleLeave = async (): Promise<void> => {
    if (!currentUserId || isSoleOwner) return
    setBusy('leave')
    setError(null)
    try {
      await removeMember(client, topicId, currentUserId)
      onClose()
    } catch {
      setError(tPanel('leaveError'))
      setBusy(null)
    }
  }

  return (
    <div
      className="flex flex-col gap-3 w-full min-w-72 max-w-md mx-auto"
      data-testid="member-panel"
    >
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium">{tPanel('title', { count: participants.length })}</div>
        {isOwner && !invitePickerOpen && (
          <button
            type="button"
            onClick={() => setInvitePickerOpen(true)}
            className={cn(
              'flex items-center gap-1 rounded-md px-2 py-1 text-xs',
              'text-muted-foreground hover:bg-accent hover:text-foreground',
            )}
          >
            <UserPlus className="size-3.5" />
            {tPanel('invite')}
          </button>
        )}
      </div>

      {invitePickerOpen && (
        <div className="flex flex-col gap-2 rounded-md border border-border bg-background/50 p-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-muted-foreground">
              {tPanel('inviteHeader')}
            </span>
            <button
              type="button"
              onClick={() => {
                setInvitePickerOpen(false)
                setSelected(new Set())
              }}
              className="rounded p-0.5 text-muted-foreground hover:bg-accent"
              aria-label="close"
            >
              <X className="size-3" />
            </button>
          </div>
          <WorkspaceMemberPicker
            invitable={invitable}
            selected={selected}
            onToggle={toggleInvite}
            emptyText={tPanel('inviteEmpty')}
          />
          <div className="flex items-center justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setInvitePickerOpen(false)
                setSelected(new Set())
              }}
              disabled={busy === 'invite'}
            >
              {tPanel('cancel')}
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={() => void handleInvite()}
              disabled={selected.size === 0 || busy === 'invite'}
            >
              {busy === 'invite' ? tPanel('inviting') : tPanel('confirmInvite')}
            </Button>
          </div>
        </div>
      )}

      <ScrollArea className="max-h-72">
        <ul className="flex flex-col gap-0.5">
          {participants.map((p) => {
            // Prefer the participant's hydrated name (always present on
            // /topics responses) and fall back to the workspace-member
            // lookup for legacy state.
            const name = p.display_name || p.email || displayNameOf(p.user_id)
            const isSelf = p.user_id === currentUserId
            const canRemove = isOwner && !isSelf
            const canPromote = isOwner && !isSelf && p.role !== 'owner'
            return (
              <li
                key={p.id}
                className={cn(
                  'group flex items-center gap-2 rounded-md px-1.5 py-1.5 text-xs',
                  'hover:bg-accent/40',
                )}
              >
                <Avatar
                  src={p.avatar_url}
                  seed={p.avatar_seed ?? p.user_id}
                  name={name}
                  userId={p.user_id}
                  size="sm"
                />
                <div className="flex-1 min-w-0 truncate">{name}</div>
                <span
                  className={cn(
                    'shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium',
                    p.role === 'owner'
                      ? 'bg-primary/10 text-primary'
                      : 'bg-muted text-muted-foreground',
                  )}
                >
                  {p.role === 'owner' ? t('owner') : t('member')}
                </span>
                {canPromote && (
                  <button
                    type="button"
                    onClick={() => void handlePromote(p.user_id)}
                    disabled={busy === `promote:${p.user_id}`}
                    className={cn(
                      'shrink-0 rounded p-0.5 text-muted-foreground',
                      'opacity-0 group-hover:opacity-100',
                      'hover:bg-accent hover:text-foreground',
                      'disabled:opacity-50',
                    )}
                    aria-label={tPanel('promote')}
                    title={tPanel('promote')}
                  >
                    <ArrowUp className="size-3.5" />
                  </button>
                )}
                {canRemove && (
                  <button
                    type="button"
                    onClick={() => void handleRemove(p.user_id)}
                    disabled={busy === `remove:${p.user_id}`}
                    className={cn(
                      'shrink-0 rounded p-0.5 text-muted-foreground',
                      'opacity-0 group-hover:opacity-100',
                      'hover:bg-destructive/10 hover:text-destructive',
                      'disabled:opacity-50',
                    )}
                    aria-label={t('removeMember')}
                    title={t('removeMember')}
                  >
                    <X className="size-3.5" />
                  </button>
                )}
              </li>
            )
          })}
        </ul>
      </ScrollArea>

      {error && (
        <div
          className={cn(
            'rounded-md border border-destructive/30 bg-destructive/5',
            'px-2 py-1 text-xs text-destructive',
          )}
        >
          {error}
        </div>
      )}

      {currentRole !== null && (
        <div className="border-t border-border pt-2">
          {isSoleOwner ? (
            <Tooltip>
              <TooltipTrigger
                className={cn(
                  'flex w-full items-center justify-center gap-1.5 rounded-md',
                  'px-2 py-1.5 text-xs text-muted-foreground',
                  'cursor-not-allowed opacity-60',
                )}
                disabled
                aria-disabled
              >
                <LogOut className="size-3.5" />
                {t('leaveGroup')}
              </TooltipTrigger>
              <TooltipContent>{tPanel('soleOwnerHint')}</TooltipContent>
            </Tooltip>
          ) : (
            <button
              type="button"
              onClick={() => void handleLeave()}
              disabled={busy === 'leave'}
              className={cn(
                'flex w-full items-center justify-center gap-1.5 rounded-md',
                'px-2 py-1.5 text-xs text-muted-foreground',
                'hover:bg-destructive/10 hover:text-destructive',
                'disabled:opacity-50',
              )}
            >
              <LogOut className="size-3.5" />
              {busy === 'leave' ? tPanel('leaving') : t('leaveGroup')}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
