'use client'

import Link from 'next/link'
import { useWorkspaceStore } from '@cubeplex/core'
import { useTranslations } from 'next-intl'

export function WorkspaceList() {
  const t = useTranslations('workspaceList')
  const workspaces = useWorkspaceStore((s) => s.workspaces)

  if (workspaces.length === 0) {
    return <div className="text-sm text-foreground/60">{t('empty')}</div>
  }

  return (
    <ul className="divide-y divide-border rounded-md border border-border">
      {workspaces.map((w) => (
        <li key={w.id} className="flex items-center justify-between px-4 py-3">
          <div>
            <div className="text-sm font-medium">{w.name}</div>
            <div className="text-xs text-foreground/50">
              {t('rolePrefix', { role: w.role ?? t('unknownRole') })}
            </div>
          </div>
          <Link
            href={`/w/${w.id}`}
            className="text-sm underline text-foreground/80 hover:text-foreground"
          >
            {t('open')}
          </Link>
        </li>
      ))}
    </ul>
  )
}
