'use client'

import Link from 'next/link'
import { useWorkspaceStore } from '@cubebox/core'
import { useTranslations } from 'next-intl'
import { Folder } from 'lucide-react'

export function WorkspaceList() {
  const t = useTranslations('workspaceList')
  const workspaces = useWorkspaceStore((s) => s.workspaces)

  if (workspaces.length === 0) {
    return (
      <div className="op-panel">
        <div className="op-empty">
          <h4>{t('emptyTitle')}</h4>
          <p>{t('emptyBody')}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="op-panel">
      <div className="op-panel__head">
        <h3>{t('panelTitle')}</h3>
        <span className="op-meta">{t('totalSuffix', { count: workspaces.length })}</span>
      </div>
      <ul>
        {workspaces.map((w, idx) => (
          <li
            key={w.id}
            className={
              'flex items-center gap-3 px-4 py-3 transition-colors hover:bg-muted/60 ' +
              (idx > 0 ? 'border-t border-border' : '')
            }
          >
            <Folder className="size-4 text-muted-foreground shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="text-[13.5px] font-medium text-foreground truncate">{w.name}</div>
              <div className="text-[11.5px] text-muted-foreground font-mono mt-0.5">
                {t('rolePrefix', { role: w.role ?? t('unknownRole') })} · {w.id}
              </div>
            </div>
            <Link
              href={`/w/${w.id}`}
              className={
                'inline-flex items-center gap-1 h-7 px-2.5 rounded text-[12.5px] font-medium ' +
                'text-foreground border border-border hover:bg-card hover:border-muted-foreground/30 ' +
                'transition-colors bg-muted/30'
              }
            >
              {t('open')} →
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}
