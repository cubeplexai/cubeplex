'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Check, ChevronDown, MessageSquare, X } from 'lucide-react'
import { createApiClient, listTopics } from '@cubebox/core'
import type { Topic } from '@cubebox/core'
import { cn } from '@/lib/utils'
import { topicDisplayTitle } from '@/lib/topicTitle'

interface TopicPickerProps {
  /** Workspace whose topics should be listed; required so the API client
   *  resolves /api/v1/topics to the workspace-scoped path. */
  wsId: string
  /** Currently-selected topic id, or ``null`` for "no topic" (standalone). */
  value: string | null
  onChange: (topicId: string | null) => void
  /** When true (default), shows a clear button to reset to ``null``. */
  clearable?: boolean
  /** Disable interaction (used while submitting). */
  disabled?: boolean
  /** Label shown when no topic is selected. */
  placeholder?: string
  /** ARIA label / id for the trigger button. */
  id?: string
  'aria-labelledby'?: string
}

/**
 * Workspace topic picker.
 *
 * Lists topics from `/api/v1/topics`, lets the user pick one or clear back to
 * "no topic". Renders inline (no popover library) so the dialog scroll context
 * keeps working. Topics are not paginated in the API; a short search filter is
 * provided for workspaces with many topics.
 */
export function TopicPicker({
  wsId,
  value,
  onChange,
  clearable = true,
  disabled,
  placeholder = 'No topic — standalone conversation',
  id,
  'aria-labelledby': ariaLabelledBy,
}: TopicPickerProps) {
  const tTopics = useTranslations('topics')
  const emptyTitle = tTopics('newGroupChat')
  const [topics, setTopics] = useState<Topic[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const [filter, setFilter] = useState('')

  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  useEffect(() => {
    let cancelled = false
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true)
    setError(null)
    listTopics(client)
      .then((data) => {
        if (!cancelled) setTopics(data.items)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load topics')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [client])

  const selected = topics.find((t) => t.id === value) ?? null
  const selectedLabel = selected ? topicDisplayTitle(selected.title, emptyTitle) : null

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase()
    if (!q) return topics
    return topics.filter((t) => topicDisplayTitle(t.title, emptyTitle).toLowerCase().includes(q))
  }, [topics, filter, emptyTitle])

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <button
          type="button"
          id={id}
          aria-labelledby={ariaLabelledBy}
          aria-haspopup="listbox"
          aria-expanded={open}
          disabled={disabled}
          onClick={() => setOpen((o) => !o)}
          className={cn(
            'flex h-8 min-w-0 flex-1 items-center justify-between gap-2 rounded-md border border-input',
            'bg-transparent px-2.5 text-sm transition-colors',
            'hover:border-border focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40',
            'disabled:cursor-not-allowed disabled:opacity-50',
            open && 'border-ring ring-2 ring-ring/40',
          )}
          data-testid="topic-picker-trigger"
        >
          <span className="flex min-w-0 items-center gap-1.5">
            <MessageSquare className="size-3.5 shrink-0 text-muted-foreground" />
            <span
              className={cn('truncate', selected ? 'text-foreground' : 'text-muted-foreground')}
            >
              {selected ? selectedLabel : placeholder}
            </span>
          </span>
          <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
        </button>
        {clearable && value !== null && (
          <button
            type="button"
            aria-label="Clear topic"
            onClick={() => onChange(null)}
            disabled={disabled}
            className="grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
            data-testid="topic-picker-clear"
          >
            <X className="size-3.5" />
          </button>
        )}
      </div>

      {open && (
        <div
          className="rounded-md border border-border bg-popover p-1 shadow-md"
          role="listbox"
          data-testid="topic-picker-list"
        >
          {topics.length > 8 && (
            <input
              type="text"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Search topics…"
              className="mb-1 w-full rounded-sm border-0 bg-muted/40 px-2 py-1 text-xs outline-none focus:bg-muted"
            />
          )}
          <div className="max-h-48 overflow-y-auto">
            {clearable && (
              <button
                type="button"
                role="option"
                aria-selected={value === null}
                onClick={() => {
                  onChange(null)
                  setOpen(false)
                }}
                className={cn(
                  'flex w-full items-center justify-between rounded-sm px-2 py-1.5 text-left text-xs',
                  'hover:bg-accent/60',
                  value === null && 'bg-accent/40',
                )}
              >
                <span className="text-muted-foreground italic">No topic</span>
                {value === null && <Check className="size-3.5 text-primary" />}
              </button>
            )}
            {loading && <div className="px-2 py-2 text-xs text-muted-foreground">Loading…</div>}
            {error && <div className="px-2 py-2 text-xs text-destructive">{error}</div>}
            {!loading &&
              !error &&
              filtered.map((topic) => (
                <button
                  key={topic.id}
                  type="button"
                  role="option"
                  aria-selected={value === topic.id}
                  onClick={() => {
                    onChange(topic.id)
                    setOpen(false)
                  }}
                  className={cn(
                    'flex w-full items-center justify-between gap-2 rounded-sm px-2 py-1.5 text-left text-xs',
                    'hover:bg-accent/60',
                    value === topic.id && 'bg-accent/40',
                  )}
                  data-testid={`topic-option-${topic.id}`}
                >
                  <span className="truncate">{topicDisplayTitle(topic.title, emptyTitle)}</span>
                  {value === topic.id && <Check className="size-3.5 shrink-0 text-primary" />}
                </button>
              ))}
            {!loading && !error && filtered.length === 0 && (
              <div className="px-2 py-2 text-xs text-muted-foreground">
                {topics.length === 0 ? 'No topics yet' : 'No matches'}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
