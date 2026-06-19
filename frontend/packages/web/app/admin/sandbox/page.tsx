'use client'

import { AdminPageShell } from '@/components/management/AdminPageShell'
import { PolicyEditor } from './_components/PolicyEditor'

export default function SandboxPolicyPage() {
  return (
    <AdminPageShell
      title="Sandbox policy"
      description="Set the default image and the network / command rules applied to every workspace sandbox in this org."
    >
      <PolicyEditor />
    </AdminPageShell>
  )
}
