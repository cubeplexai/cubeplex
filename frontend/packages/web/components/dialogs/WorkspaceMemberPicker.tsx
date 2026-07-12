'use client'

import { useMemo, useState } from 'react'
import { Search, X } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { type WsMember } from '@cubeplex/core'
import { Checkbox } from '@/components/ui/checkbox'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

interface WorkspaceMemberPickerProps {
  invitable: WsMember[]
  selected: Set<string>
  onToggle: (userId: string) => void
  emptyText: string
}

const SEARCH_THRESHOLD = 5

function matchesQuery(member: WsMember, q: string): boolean {
  if (!q) return true
  const needle = q.toLowerCase()
  if (member.display_name?.toLowerCase().includes(needle)) return true
  if (member.email.toLowerCase().includes(needle)) return true
  return false
}

export function WorkspaceMemberPicker({
  invitable,
  selected,
  onToggle,
  emptyText,
}: WorkspaceMemberPickerProps): React.ReactElement {
  const tCommon = useTranslations('common')
  const [query, setQuery] = useState('')

  const filtered = useMemo(
    () => invitable.filter((m) => matchesQuery(m, query.trim())),
    [invitable, query],
  )

  if (invitable.length === 0) {
    return <p className="text-xs text-muted-foreground">{emptyText}</p>
  }

  const showSearch = invitable.length >= SEARCH_THRESHOLD

  return (
    <div className="flex flex-col gap-2">
      {showSearch && (
        <div className="relative">
          <Search
            className={cn(
              'pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2',
              'size-3.5 text-muted-foreground',
            )}
          />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={tCommon('searchMembers')}
            aria-label={tCommon('searchMembers')}
            className={cn(
              'w-full rounded-md border border-border bg-background',
              'py-1.5 pl-8 pr-7 text-sm',
              'placeholder:text-muted-foreground/60 focus:outline-none',
              'focus:border-primary',
            )}
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery('')}
              aria-label={tCommon('clear')}
              className={cn(
                'absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5',
                'text-muted-foreground hover:bg-accent hover:text-foreground',
              )}
            >
              <X className="size-3" />
            </button>
          )}
        </div>
      )}
      {filtered.length === 0 ? (
        <p className="text-xs text-muted-foreground px-1 py-2">{tCommon('noResults')}</p>
      ) : (
        <ScrollArea className={cn('max-h-56 rounded-md border border-border bg-background/50')}>
          <ul className="py-1">
            {filtered.map((m) => {
              const checked = selected.has(m.user_id)
              return (
                <li key={m.user_id}>
                  <label
                    className={cn(
                      'flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm',
                      'hover:bg-accent/50',
                    )}
                  >
                    <Checkbox checked={checked} onCheckedChange={() => onToggle(m.user_id)} />
                    <span className="flex-1 truncate">{m.display_name || m.email}</span>
                    {m.display_name && (
                      <span className="truncate text-xs text-muted-foreground">{m.email}</span>
                    )}
                  </label>
                </li>
              )
            })}
          </ul>
        </ScrollArea>
      )}
      {selected.size > 0 && (
        <p className="text-xs text-muted-foreground px-1">
          {tCommon('selectedCount', { count: selected.size })}
        </p>
      )}
    </div>
  )
}
