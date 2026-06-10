'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Plus, Trash2 } from 'lucide-react'
import { createApiClient, useMemberStore, type OrgMember } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
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
import { AddOrgMemberDialog } from './AddOrgMemberDialog'

export function OrgMembersTable() {
  const t = useTranslations('adminMembers')
  const client = useMemo(() => createApiClient(''), [])
  const {
    orgMembers,
    orgLoading,
    loadOrgMembers,
    addOrgMember,
    updateOrgMemberRole,
    removeOrgMember,
  } = useMemberStore()

  const [addOpen, setAddOpen] = useState(false)
  const [removing, setRemoving] = useState<string | null>(null)

  useEffect(() => {
    void loadOrgMembers(client)
  }, [client, loadOrgMembers])

  const handleAdd = useCallback(
    async (email: string, role: string) => {
      await addOrgMember(client, email, role)
    },
    [client, addOrgMember],
  )

  const handleRoleChange = useCallback(
    async (userId: string, role: string) => {
      await updateOrgMemberRole(client, userId, role)
    },
    [client, updateOrgMemberRole],
  )

  const handleRemove = useCallback(
    async (userId: string) => {
      await removeOrgMember(client, userId)
      setRemoving(null)
    },
    [client, removeOrgMember],
  )

  function formatDate(iso: string): string {
    try {
      return new Date(iso).toLocaleDateString()
    } catch {
      return iso
    }
  }

  return (
    <>
      <header className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">{t('title')}</h2>
          <p className="mt-0.5 text-sm text-muted-foreground">{t('subtitle')}</p>
        </div>
        <Button size="sm" className="gap-1.5" onClick={() => setAddOpen(true)}>
          <Plus className="size-3.5" />
          {t('addMember')}
        </Button>
      </header>

      {orgLoading ? (
        <div className="py-8 text-center text-xs text-muted-foreground">Loading...</div>
      ) : orgMembers.length === 0 ? (
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
        <div className={'rounded-xl border border-border/70 bg-card/40 shadow-sm'}>
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
              {orgMembers.map((m) => (
                <MemberRow
                  key={m.user_id}
                  member={m}
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

      <AddOrgMemberDialog open={addOpen} onOpenChange={setAddOpen} onAdd={handleAdd} />
    </>
  )
}

interface MemberRowProps {
  member: OrgMember
  removing: string | null
  onRoleChange: (userId: string, role: string) => Promise<void>
  onRemoveClick: (userId: string) => void
  onRemoveConfirm: (userId: string) => Promise<void>
  onRemoveCancel: () => void
  formatDate: (iso: string) => string
  t: ReturnType<typeof useTranslations<'adminMembers'>>
}

function MemberRow({
  member,
  removing,
  onRoleChange,
  onRemoveClick,
  onRemoveConfirm,
  onRemoveCancel,
  formatDate,
  t,
}: MemberRowProps) {
  const isOwner = member.role === 'owner'
  const isRemoving = removing === member.user_id

  return (
    <TableRow className="relative">
      <TableCell className="text-sm">{member.email}</TableCell>
      <TableCell>
        {isOwner ? (
          <Badge variant="secondary" className="text-[11px]">
            {t('owner')}
          </Badge>
        ) : (
          <Select
            value={member.role}
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
        )}
      </TableCell>
      <TableCell className="text-xs text-muted-foreground">
        {formatDate(member.created_at)}
      </TableCell>
      <TableCell>
        {!isOwner && (
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
              'absolute inset-0 z-10 flex items-center justify-between ' +
              'gap-2 bg-background/95 px-4 backdrop-blur-sm'
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
