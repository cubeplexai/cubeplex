'use client'

import { WsMembersTable } from './members/WsMembersTable'

interface MembersPanelProps {
  wsId: string
}

export function MembersPanel({ wsId }: MembersPanelProps) {
  return (
    <div className="flex h-full flex-col overflow-y-auto px-6 py-6">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
        <WsMembersTable wsId={wsId} />
      </div>
    </div>
  )
}
