'use client'

import { OrgMembersTable } from '@/components/admin/members/OrgMembersTable'

export default function AdminMembersPage() {
  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto flex max-w-3xl flex-col gap-6">
          <OrgMembersTable />
        </div>
      </div>
    </div>
  )
}
