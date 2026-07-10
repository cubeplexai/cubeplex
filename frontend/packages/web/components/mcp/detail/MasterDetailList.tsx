'use client'

import { type ReactNode, useState } from 'react'
import { Search } from 'lucide-react'

import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

export interface MasterDetailListProps<T extends { name?: string; description?: string | null }> {
  items: T[]
  getKey: (item: T) => string
  filter?: (item: T, query: string) => boolean
  selectedKey: string | null
  onSelect: (key: string) => void
  searchPlaceholder: string
  countLabel: (matched: number, total: number, query: string) => string
  emptyState: ReactNode
  emptyMatchState: (query: string) => ReactNode
  renderItem: (item: T, isSelected: boolean) => ReactNode
  /** Free-form section rendered below the filtered list (for orphan callouts, etc.) */
  footerSection?: ReactNode
  /** Control the search input externally. If omitted, internal state is used. */
  query?: string
  onQueryChange?: (q: string) => void
}

function defaultFilter<T extends { name?: string; description?: string | null }>(
  item: T,
  q: string,
): boolean {
  const lq = q.toLowerCase()
  return (
    (item.name ?? '').toLowerCase().includes(lq) ||
    (item.description ?? '').toLowerCase().includes(lq)
  )
}

export function MasterDetailList<T extends { name?: string; description?: string | null }>({
  items,
  getKey,
  filter,
  selectedKey,
  onSelect,
  searchPlaceholder,
  countLabel,
  emptyState,
  emptyMatchState,
  renderItem,
  footerSection,
  query: controlledQuery,
  onQueryChange: controlledOnQueryChange,
}: MasterDetailListProps<T>) {
  const [internalQuery, setInternalQuery] = useState('')
  const isControlled = controlledQuery !== undefined
  const query = isControlled ? (controlledQuery ?? '') : internalQuery
  const setQuery = isControlled ? (controlledOnQueryChange ?? (() => {})) : setInternalQuery

  const trimmed = query.trim()
  const filterFn = filter ?? defaultFilter<T>
  const filtered = trimmed ? items.filter((item) => filterFn(item, trimmed)) : items

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={searchPlaceholder}
          aria-label={searchPlaceholder}
          name="mcp-detail-search"
          autoComplete="off"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          className="h-9 pl-7 text-sm"
        />
      </div>
      <p className="px-1 text-xs text-muted-foreground">
        {countLabel(filtered.length, items.length, trimmed)}
      </p>
      <ScrollArea className="min-h-0 flex-1">
        <ul className="flex flex-col gap-0.5 pr-1">
          {filtered.length === 0 ? (
            <li className="px-3 py-6 text-center text-xs text-muted-foreground">
              {trimmed ? emptyMatchState(trimmed) : emptyState}
            </li>
          ) : (
            filtered.map((item) => {
              const key = getKey(item)
              const isSelected = key === selectedKey
              return (
                <li key={key}>
                  <button
                    type="button"
                    aria-pressed={isSelected}
                    onClick={() => onSelect(key)}
                    className={cn(
                      'flex w-full flex-col gap-1 rounded-md border border-transparent px-3 py-2 text-left transition',
                      isSelected ? 'border-l-2 border-l-primary bg-primary/5' : 'hover:bg-muted/60',
                    )}
                  >
                    {renderItem(item, isSelected)}
                  </button>
                </li>
              )
            })
          )}
        </ul>
        {footerSection}
      </ScrollArea>
    </div>
  )
}
