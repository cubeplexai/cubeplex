'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Link as LinkIcon, Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { AdminPageShell } from '@/components/management/AdminPageShell'
import { OrgMembersTable } from '@/components/admin/members/OrgMembersTable'
import { CreateOrgInviteDialog } from '@/components/admin/members/CreateOrgInviteDialog'

export default function AdminMembersPage() {
  const t = useTranslations('adminMembers')
  const [addOpen, setAddOpen] = useState(false)
  const [inviteOpen, setInviteOpen] = useState(false)

  return (
    <AdminPageShell
      title={t('title')}
      description={t('subtitle')}
      action={
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            className="gap-1.5"
            onClick={() => setInviteOpen(true)}
          >
            <LinkIcon className="size-3.5" />
            {t('inviteMember')}
          </Button>
          <Button size="sm" className="gap-1.5" onClick={() => setAddOpen(true)}>
            <Plus className="size-3.5" />
            {t('addMember')}
          </Button>
        </div>
      }
    >
      <OrgMembersTable addOpen={addOpen} onAddOpenChange={setAddOpen} />
      <CreateOrgInviteDialog open={inviteOpen} onOpenChange={setInviteOpen} />
    </AdminPageShell>
  )
}
