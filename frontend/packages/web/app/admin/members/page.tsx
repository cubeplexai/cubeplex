'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { AdminPageShell } from '@/components/management/AdminPageShell'
import { OrgMembersTable } from '@/components/admin/members/OrgMembersTable'

export default function AdminMembersPage() {
  const t = useTranslations('adminMembers')
  const [addOpen, setAddOpen] = useState(false)

  return (
    <AdminPageShell
      title={t('title')}
      description={t('subtitle')}
      action={
        <Button size="sm" className="gap-1.5" onClick={() => setAddOpen(true)}>
          <Plus className="size-3.5" />
          {t('addMember')}
        </Button>
      }
    >
      <OrgMembersTable addOpen={addOpen} onAddOpenChange={setAddOpen} />
    </AdminPageShell>
  )
}
