'use client'

import { useTranslations } from 'next-intl'
import { useWorkspaceStore } from '@cubebox/core'
import { WsMembersTable } from './members/WsMembersTable'
import { InviteSection } from './members/InviteSection'

interface MembersPanelProps {
  wsId: string
}

export function MembersPanel({ wsId }: MembersPanelProps) {
  const t = useTranslations('wsMembers')
  const wsRole = useWorkspaceStore((s) => s.workspaces.find((w) => w.id === wsId)?.role)

  if (wsRole !== 'admin') {
    return (
      <div className="flex h-full flex-col overflow-y-auto px-6 py-6">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
          <p className="rounded-md border border-border/60 bg-muted/30 px-4 py-3 text-sm text-muted-foreground">
            {t('accessDenied')}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto px-6 py-6">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
        <WsMembersTable wsId={wsId} />
        <InviteSection wsId={wsId} />
      </div>
    </div>
  )
}
