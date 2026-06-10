'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useFormatter, useTranslations } from 'next-intl'
import { Plus, Trash2 } from 'lucide-react'
import { createApiClient, useAuthStore, useMemberStore, type WsMember } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { AddWsMemberDialog } from './AddWsMemberDialog'

interface WsMembersTableProps {
  wsId: string
}

export function WsMembersTable({ wsId }: WsMembersTableProps) {
  const t = useTranslations('wsMembers')
  const format = useFormatter()
  const client = useMemo(() => createApiClient(''), [])
  const currentUser = useAuthStore((s) => s.user)
  const {
    wsMembers,
    wsLoading,
    available,
    loadWsMembers,
    loadAvailable,
    addWsMember,
    updateWsMemberRole,
    removeWsMember,
  } = useMemberStore()

  const [addOpen, setAddOpen] = useState(false)
  const [removing, setRemoving] = useState<string | null>(null)

  useEffect(() => {
    void loadWsMembers(client, wsId)
  }, [client, wsId, loadWsMembers])

  const handleOpenAdd = useCallback(async () => {
    await loadAvailable(client, wsId)
    setAddOpen(true)
  }, [client, wsId, loadAvailable])

  const handleAdd = useCallback(
    async (userId: string, role: string) => {
      await addWsMember(client, wsId, userId, role)
    },
    [client, wsId, addWsMember],
  )

  const handleRoleChange = useCallback(
    async (userId: string, role: string) => {
      await updateWsMemberRole(client, wsId, userId, role)
    },
    [client, wsId, updateWsMemberRole],
  )

  const handleRemove = useCallback(
    async (userId: string) => {
      await removeWsMember(client, wsId, userId)
      setRemoving(null)
    },
    [client, wsId, removeWsMember],
  )

  function formatDate(iso: string): string {
    try {
      return format.dateTime(new Date(iso), { dateStyle: 'medium' })
    } catch {
      return iso
    }
  }

  return (
    <>
      <header className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">{t('title')}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">{t('subtitle')}</p>
        </div>
        <Button size="sm" className="gap-1.5" onClick={() => void handleOpenAdd()}>
          <Plus className="size-3.5" />
          {t('addMember')}
        </Button>
      </header>

      {wsLoading ? (
        <div className={'py-8 text-center text-xs text-muted-foreground'}>Loading...</div>
      ) : wsMembers.length === 0 ? (
        <div
          className={
            'rounded-md border border-dashed border-border/60 ' +
            'bg-muted/20 px-4 py-8 text-center text-xs ' +
            'text-muted-foreground'
          }
        >
          {t('empty')}
        </div>
      ) : (
        <div className={'rounded-xl border border-border/70 ' + 'bg-card/40 shadow-sm'}>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-xs">{t('email')}</TableHead>
                <TableHead className="text-xs">{t('role')}</TableHead>
                <TableHead className="text-xs">{t('joined')}</TableHead>
                <TableHead className="text-xs w-[80px]">{t('actions')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {wsMembers.map((m) => (
                <WsMemberRow
                  key={m.user_id}
                  member={m}
                  currentUserId={currentUser?.id ?? null}
                  removing={removing}
                  onRoleChange={handleRoleChange}
                  onRemoveClick={setRemoving}
                  onRemoveConfirm={handleRemove}
                  onRemoveCancel={() => setRemoving(null)}
                  formatDate={formatDate}
                  t={t}
                />
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <AddWsMemberDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        available={available}
        onAdd={handleAdd}
      />
    </>
  )
}

interface WsMemberRowProps {
  member: WsMember
  currentUserId: string | null
  removing: string | null
  onRoleChange: (userId: string, role: string) => Promise<void>
  onRemoveClick: (userId: string) => void
  onRemoveConfirm: (userId: string) => Promise<void>
  onRemoveCancel: () => void
  formatDate: (iso: string) => string
  t: ReturnType<typeof useTranslations<'wsMembers'>>
}

function WsMemberRow({
  member,
  currentUserId,
  removing,
  onRoleChange,
  onRemoveClick,
  onRemoveConfirm,
  onRemoveCancel,
  formatDate,
  t,
}: WsMemberRowProps) {
  const isSelf = member.user_id === currentUserId
  const isRemoving = removing === member.user_id

  return (
    <TableRow className="relative">
      <TableCell className="text-sm">{member.email}</TableCell>
      <TableCell>
        <Select
          value={member.role}
          items={[
            { value: 'admin', label: t('admin') },
            { value: 'member', label: t('member') },
          ]}
          onValueChange={(v) => {
            if (v) void onRoleChange(member.user_id, v)
          }}
        >
          <SelectTrigger size="sm" className="h-6 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="admin">{t('admin')}</SelectItem>
            <SelectItem value="member">{t('member')}</SelectItem>
          </SelectContent>
        </Select>
      </TableCell>
      <TableCell className="text-xs text-muted-foreground">
        {formatDate(member.created_at)}
      </TableCell>
      <TableCell>
        {!isSelf && (
          <Button
            variant="ghost"
            size="sm"
            className="h-6 gap-1 text-xs text-destructive"
            onClick={() => onRemoveClick(member.user_id)}
          >
            <Trash2 className="size-3" />
            {t('remove')}
          </Button>
        )}
      </TableCell>

      {isRemoving && (
        <td>
          <div
            className={
              'absolute inset-0 z-10 flex items-center ' +
              'justify-between gap-2 ' +
              'bg-background/95 px-4 backdrop-blur-sm'
            }
          >
            <span className="text-xs">
              {t('removeConfirm.message', {
                email: member.email,
              })}
            </span>
            <div className="flex gap-1.5">
              <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={onRemoveCancel}>
                {t('removeConfirm.cancel')}
              </Button>
              <Button
                variant="destructive"
                size="sm"
                className="h-6 text-xs"
                onClick={() => {
                  void onRemoveConfirm(member.user_id)
                }}
              >
                {t('removeConfirm.confirm')}
              </Button>
            </div>
          </div>
        </td>
      )}
    </TableRow>
  )
}
