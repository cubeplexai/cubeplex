'use client'

import Link from 'next/link'
import { useMemo, useState } from 'react'
import { usePathname } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { useWorkspaceStore } from '@cubeplex/core'
import { Check, ChevronsUpDown, Plus } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

function WorkspaceAvatar({ name }: { name: string }): React.ReactElement {
  const letter = name[0]?.toUpperCase() ?? 'W'
  return (
    <div className="size-6 rounded-md bg-primary/10 text-primary flex items-center justify-center text-[11px] font-semibold shrink-0 select-none">
      {letter}
    </div>
  )
}

export function WorkspaceSelector(): React.ReactElement {
  const t = useTranslations('workspace')
  const workspaces = useWorkspaceStore((s) => s.workspaces)
  const pathname = usePathname()
  const [open, setOpen] = useState(false)

  const currentWsId = useMemo(() => {
    const match = pathname?.match(/^\/w\/([^/]+)/)
    return match ? match[1] : null
  }, [pathname])

  const currentWs = workspaces.find((w) => w.id === currentWsId)

  const sorted = useMemo(() => {
    return [...workspaces].sort((a, b) => {
      const at = a.last_activity_at ? Date.parse(a.last_activity_at) : 0
      const bt = b.last_activity_at ? Date.parse(b.last_activity_at) : 0
      return bt - at
    })
  }, [workspaces])

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        className={cn(
          'w-full rounded-md border border-border bg-background px-2.5 py-2',
          'hover:bg-accent transition-colors duration-fast text-left',
          open && 'bg-accent',
        )}
      >
        <p className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground/70 mb-1.5 leading-none">
          {t('title')}
        </p>
        <div className="flex items-center gap-2">
          <WorkspaceAvatar name={currentWs?.name ?? 'W'} />
          <span className="flex-1 truncate text-xs font-medium text-foreground">
            {currentWs?.name ?? '—'}
          </span>
          <ChevronsUpDown className="size-3.5 shrink-0 text-muted-foreground" />
        </div>
      </PopoverTrigger>
      <PopoverContent side="right" align="start" sideOffset={8} className="w-52 p-1">
        <ul className="space-y-0.5">
          {sorted.map((ws) => (
            <li key={ws.id}>
              <Link
                href={`/w/${ws.id}`}
                onClick={() => setOpen(false)}
                className="flex items-center gap-2 rounded px-2 py-1.5 text-xs hover:bg-accent transition-colors duration-fast"
              >
                <WorkspaceAvatar name={ws.name} />
                <span className="flex-1 truncate text-foreground">{ws.name}</span>
                {ws.id === currentWsId && <Check className="size-3 text-primary shrink-0" />}
              </Link>
            </li>
          ))}
        </ul>
        <div className="mt-1 pt-1 border-t border-border">
          <Link
            href="/workspaces"
            onClick={() => setOpen(false)}
            className="flex items-center gap-2 rounded px-2 py-1.5 text-xs text-muted-foreground hover:text-foreground hover:bg-accent transition-colors duration-fast"
          >
            <Plus className="size-3.5 shrink-0" />
            <span>{t('new')}</span>
          </Link>
        </div>
      </PopoverContent>
    </Popover>
  )
}
