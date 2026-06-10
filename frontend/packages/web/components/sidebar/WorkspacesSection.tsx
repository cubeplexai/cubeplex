'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { useWorkspaceStore } from '@cubebox/core'
import { Folder, Plus } from 'lucide-react'
import { cn } from '@/lib/utils'

const DEFAULT_VISIBLE = 5

export function WorkspacesSection() {
  const t = useTranslations('workspace')
  const workspaces = useWorkspaceStore((s) => s.workspaces)
  const pathname = usePathname()
  const [showAll, setShowAll] = useState(false)

  // Sort by last_activity_at desc; nulls last
  const sorted = useMemo(() => {
    return [...workspaces].sort((a, b) => {
      const at = a.last_activity_at ? Date.parse(a.last_activity_at) : 0
      const bt = b.last_activity_at ? Date.parse(b.last_activity_at) : 0
      return bt - at
    })
  }, [workspaces])

  const visible = showAll ? sorted : sorted.slice(0, DEFAULT_VISIBLE)
  const hidden = Math.max(0, sorted.length - DEFAULT_VISIBLE)

  // Detect current workspace from URL: /w/[wsId]/...
  const currentWsId = useMemo(() => {
    const match = pathname?.match(/^\/w\/([^/]+)/)
    return match ? match[1] : null
  }, [pathname])

  return (
    <div className="px-2 py-2">
      <p className="px-2 mb-1 text-2xs font-medium uppercase tracking-wider text-faint">
        {t('title')}
      </p>
      <ul className="space-y-0.5">
        {visible.map((ws) => {
          const active = ws.id === currentWsId
          return (
            <li key={ws.id}>
              <Link
                href={`/w/${ws.id}`}
                className={cn(
                  'group relative flex items-center gap-2 rounded px-2 py-1.5 text-xs transition-colors duration-fast',
                  active
                    ? 'bg-accent text-foreground font-medium'
                    : 'text-muted-foreground hover:text-foreground hover:bg-accent',
                )}
              >
                {active && (
                  <span className="absolute left-0 top-[22%] bottom-[22%] w-0.5 bg-primary rounded-r" />
                )}
                <Folder
                  className={cn('size-3.5 shrink-0', active ? 'text-primary' : 'text-faint')}
                />
                <span className="truncate flex-1">{ws.name}</span>
              </Link>
            </li>
          )
        })}
        {hidden > 0 && !showAll && (
          <li>
            <button
              type="button"
              onClick={() => setShowAll(true)}
              className="w-full text-left px-2 py-1 text-2xs text-faint hover:text-foreground transition-colors duration-fast"
            >
              {t('showMore', { count: hidden })}
            </button>
          </li>
        )}
        <li>
          <Link
            href="/workspaces"
            className="flex items-center gap-2 rounded px-2 py-1.5 text-xs text-muted-foreground hover:text-foreground hover:bg-accent transition-colors duration-fast"
          >
            <Plus className="size-3.5 shrink-0" />
            <span>{t('new')}</span>
          </Link>
        </li>
      </ul>
    </div>
  )
}
