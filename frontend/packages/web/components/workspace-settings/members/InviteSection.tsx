'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useFormatter, useTranslations } from 'next-intl'
import { Link2, Trash2 } from 'lucide-react'
import { createApiClient, listInvites, revokeInvite, type InviteToken } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { CreateInviteDialog } from './CreateInviteDialog'

function inviteStatus(invite: InviteToken): 'pending' | 'used' | 'expired' {
  if (invite.used_at) return 'used'
  if (new Date(invite.expires_at) < new Date()) return 'expired'
  return 'pending'
}

function statusBadge(s: 'pending' | 'used' | 'expired') {
  const cls =
    s === 'pending'
      ? 'bg-warning-surface text-warning-fg'
      : s === 'used'
        ? 'bg-success-solid/10 text-success-fg'
        : 'bg-muted text-muted-foreground'
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-medium ${cls}`}>
      {s}
    </span>
  )
}

export function InviteSection({ wsId }: { wsId: string }) {
  const t = useTranslations('wsMembers.invite')
  const format = useFormatter()
  const client = useMemo(() => createApiClient(''), [])
  const [invites, setInvites] = useState<InviteToken[]>([])
  const [createOpen, setCreateOpen] = useState(false)

  const load = useCallback(async () => {
    const data = await listInvites(client, wsId)
    setInvites(data)
  }, [client, wsId])

  useEffect(() => {
    /* eslint-disable-next-line react-hooks/set-state-in-effect */
    void load()
  }, [load])

  const onRevoke = async (token: string) => {
    await revokeInvite(client, wsId, token)
    setInvites((prev) => prev.filter((i) => i.token !== token))
  }

  return (
    <div className="mt-6">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-medium">{t('sectionTitle')}</h3>
        <Button variant="outline" size="sm" onClick={() => setCreateOpen(true)} className="gap-1.5">
          <Link2 className="size-3.5" />
          {t('createLink')}
        </Button>
      </div>
      {invites.length > 0 && (
        <div className="rounded-xl border border-border/70 bg-card/40 shadow-sm">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-xs">{t('role')}</TableHead>
                <TableHead className="text-xs">{t('createdBy')}</TableHead>
                <TableHead className="text-xs">{t('expires')}</TableHead>
                <TableHead className="text-xs">{t('status')}</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {invites.map((inv) => {
                const s = inviteStatus(inv)
                return (
                  <TableRow key={inv.token}>
                    <TableCell className="text-sm capitalize">{inv.role}</TableCell>
                    <TableCell className="text-sm text-muted-foreground truncate max-w-[140px]">
                      {inv.created_by}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {format.dateTime(new Date(inv.expires_at), {
                        dateStyle: 'short',
                        timeStyle: 'short',
                      })}
                    </TableCell>
                    <TableCell>{statusBadge(s)}</TableCell>
                    <TableCell>
                      {s === 'pending' && (
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => void onRevoke(inv.token)}
                        >
                          <Trash2 className="size-3.5 text-destructive" />
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      )}
      <CreateInviteDialog
        wsId={wsId}
        open={createOpen}
        onOpenChange={(o) => {
          setCreateOpen(o)
          if (!o) void load()
        }}
      />
    </div>
  )
}
