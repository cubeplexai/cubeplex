'use client'

import { useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { Search } from 'lucide-react'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useConversationSearch } from '@/hooks/useConversationSearch'
import { SearchResultRow } from '@/components/sidebar/SearchResultRow'

interface Props {
  wsId: string | null
}

export function ConversationSearch({ wsId }: Props): React.ReactElement {
  const t = useTranslations('sidebar.search')
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const router = useRouter()
  const { loading, error, results } = useConversationSearch(q, wsId)

  useEffect(() => {
    function onKey(e: KeyboardEvent): void {
      if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== 'k') return
      // Spec §10.3: only register ⌘K when no other input has focus, so
      // typing ⌘K inside the chat composer or a markdown editor doesn't
      // steal focus.
      const ae = document.activeElement as HTMLElement | null
      if (ae) {
        const tag = ae.tagName
        if (tag === 'INPUT' || tag === 'TEXTAREA' || ae.isContentEditable) {
          return
        }
      }
      e.preventDefault()
      setOpen(true)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    if (open) {
      const handle = window.setTimeout(() => inputRef.current?.focus(), 0)
      return () => window.clearTimeout(handle)
    }
  }, [open])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setActive(0)
  }, [results])

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>): void {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((a) => Math.min(results.length - 1, a + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((a) => Math.max(0, a - 1))
    } else if (e.key === 'Enter') {
      const r = results[active]
      if (!r || !wsId) return
      router.push(
        `/w/${wsId}/conversations/${r.conversation_id}${
          r.matched_message_seq ? `#msg-${r.matched_message_seq}` : ''
        }`,
      )
      setOpen(false)
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        aria-label={t('open')}
        className="ml-auto p-1 rounded hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"
      >
        <Search className="size-3.5" />
      </PopoverTrigger>
      <PopoverContent
        side="right"
        align="start"
        sideOffset={8}
        className="w-80 p-0 max-h-[60vh] overflow-hidden flex flex-col"
      >
        <div className="border-b border-border p-2">
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={t('placeholder')}
            className="w-full bg-transparent text-xs outline-none placeholder:text-faint"
            aria-label={t('placeholder')}
          />
        </div>
        <div className="flex-1 overflow-y-auto py-1">
          {loading && <p className="px-3 py-2 text-2xs text-faint">{t('loading')}</p>}
          {!loading && error && <p className="px-3 py-2 text-2xs text-faint">{t('unavailable')}</p>}
          {!loading && !error && q.trim().length > 0 && results.length === 0 && (
            <p className="px-3 py-2 text-2xs text-faint">{t('noMatches')}</p>
          )}
          {results.length > 0 && wsId && (
            <ul className="space-y-0.5">
              {results.map((r, i) => (
                <SearchResultRow
                  key={r.conversation_id}
                  result={r}
                  wsId={wsId}
                  active={i === active}
                  onPick={() => setOpen(false)}
                />
              ))}
            </ul>
          )}
        </div>
      </PopoverContent>
    </Popover>
  )
}
