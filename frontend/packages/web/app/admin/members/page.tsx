'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Link as LinkIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { AdminPageShell } from '@/components/management/AdminPageShell'
import { OrgMembersTable } from '@/components/admin/members/OrgMembersTable'
import { CreateOrgInviteDialog } from '@/components/admin/members/CreateOrgInviteDialog'

export default function AdminMembersPage() {
  const t = useTranslations('adminMembers')
  const [inviteOpen, setInviteOpen] = useState(false)

  return (
    <AdminPageShell
      title={t('title')}
      description={t('subtitle')}
      action={
        <Button size="sm" className="gap-1.5" onClick={() => setInviteOpen(true)}>
          <LinkIcon className="size-3.5" />
          {t('inviteMember')}
        </Button>
      }
    >
      <OrgMembersTable />
      <CreateOrgInviteDialog open={inviteOpen} onOpenChange={setInviteOpen} />
    </AdminPageShell>
  )
}
