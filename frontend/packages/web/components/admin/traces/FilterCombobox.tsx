'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import { XIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface FilterComboboxOption {
  /** Filter value stored in the URL (an entity ID, or a model name). */
  value: string
  /** Human-readable label shown in the dropdown and input. */
  label: string
}

interface Props {
  label: string
  value: string | undefined
  onChange: (next: string | undefined) => void
  /**
   * Fetch options for a query string. In `list` mode this is called once with
   * '' on mount and the result is filtered client-side; in `typeahead` mode it
   * is called (debounced) on every keystroke so the server narrows by prefix.
   */
  loadOptions: (q: string, signal: AbortSignal) => Promise<FilterComboboxOption[]>
  mode: 'list' | 'typeahead'
  placeholder?: string
  /**
   * Resolved label for a deep-linked `value` (e.g. from a shared/bookmarked
   * URL), fetched asynchronously by the parent. Swaps the input's display
   * text from the raw id to this label once it arrives - but only while the
   * input still shows exactly `value` untouched, so it never clobbers a
   * selection or in-progress typing that happened in the meantime.
   */
  initialLabel?: string
}

export function FilterCombobox({
  label,
  value,
  onChange,
  loadOptions,
  mode,
  placeholder,
  initialLabel,
}: Props) {
  const t = useTranslations('adminTraces.filters')
  // inputValue is the editable text in the input. It is initialized from the
  // external value (a raw id when deep-linked from the URL) and thereafter
  // driven only by user actions (type/select/clear) - so a deep-linked id
  // shows as-is until the user picks an option, or `initialLabel` resolves,
  // whichever comes first.
  const [inputValue, setInputValue] = useState(value ?? '')

  useEffect(() => {
    if (initialLabel && value !== undefined && inputValue === value) {
      setInputValue(initialLabel)
    }
    // Only re-run when the resolved label itself changes; re-checking on
    // every inputValue change would fight the user's own typing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialLabel])
  const [options, setOptions] = useState<FilterComboboxOption[]>([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [highlight, setHighlight] = useState(-1)
  const abortRef = useRef<AbortController | null>(null)
  const rootRef = useRef<HTMLDivElement | null>(null)
  const listLoadedRef = useRef(false)

  const runLoad = useCallback(
    async (q: string) => {
      abortRef.current?.abort()
      const ctrl = new AbortController()
      abortRef.current = ctrl
      setLoading(true)
      try {
        const opts = await loadOptions(q, ctrl.signal)
        if (!ctrl.signal.aborted) setOptions(opts)
      } catch {
        // Leave the previous options in place; a failed fetch keeps the field
        // usable as free-text (e.g. paste an ID).
      } finally {
        if (!ctrl.signal.aborted) setLoading(false)
      }
    },
    [loadOptions],
  )

  // list mode: fetch once on mount so the dropdown is populated.
  useEffect(() => {
    if (mode !== 'list' || listLoadedRef.current) return
    listLoadedRef.current = true
    void runLoad('')
  }, [mode, runLoad])

  // typeahead: debounced server fetch on input change while open.
  useEffect(() => {
    if (mode !== 'typeahead' || !open) return
    const handle = setTimeout(() => {
      void runLoad(inputValue)
    }, 200)
    return () => clearTimeout(handle)
  }, [inputValue, open, mode, runLoad])

  // click-outside to close
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const displayOptions = useMemo(() => {
    if (mode === 'list') {
      const q = inputValue.toLowerCase()
      return q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options
    }
    return options
  }, [options, inputValue, mode])

  // Clamp stale highlight (e.g. after the visible set shrank) at use instead of
  // resetting via an effect.
  const safeHighlight = highlight >= 0 && highlight < displayOptions.length ? highlight : -1

  const select = (opt: FilterComboboxOption) => {
    setInputValue(opt.label)
    onChange(opt.value)
    setHighlight(-1)
    setOpen(false)
  }

  const clear = () => {
    setInputValue('')
    onChange(undefined)
    setHighlight(-1)
    setOpen(false)
  }

  // Enter picks the highlighted item, else the first visible match, else commits
  // the typed text as the filter value (the paste-an-ID fallback).
  const commitFreeText = () => {
    const trimmed = inputValue.trim()
    onChange(trimmed === '' ? undefined : trimmed)
    setOpen(false)
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (!open) setOpen(true)
      setHighlight((h) => Math.min(Math.max(h, -1) + 1, displayOptions.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlight((h) => Math.max(h - 1, -1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const idx = safeHighlight >= 0 ? safeHighlight : displayOptions.length > 0 ? 0 : -1
      if (idx >= 0) select(displayOptions[idx])
      else commitFreeText()
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <div ref={rootRef} className="relative flex flex-col gap-1 text-xs text-muted-foreground">
      <span>{label}</span>
      <div className="relative">
        <input
          type="text"
          value={inputValue}
          placeholder={placeholder}
          onChange={(e) => {
            setInputValue(e.target.value)
            if (!open) setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          className="h-8 w-44 rounded border border-border bg-card px-2 py-1 pr-7 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
        />
        {inputValue && (
          <button
            type="button"
            onClick={clear}
            aria-label={t('clear')}
            className="absolute right-1 top-1/2 flex size-5 -translate-y-1/2 items-center justify-center text-muted-foreground hover:text-foreground"
          >
            <XIcon className="size-3.5" />
          </button>
        )}
      </div>
      {open && (
        <div className="absolute top-full left-0 z-50 mt-1 max-h-60 w-44 overflow-auto rounded border border-border bg-popover p-1 text-foreground shadow-md">
          {loading && (
            <div className="px-2 py-1 text-xs text-muted-foreground">{t('loadingOptions')}</div>
          )}
          {!loading && displayOptions.length === 0 && (
            <div className="px-2 py-1 text-xs text-muted-foreground">{t('noOptions')}</div>
          )}
          {!loading &&
            displayOptions.map((o, i) => (
              <button
                type="button"
                key={o.value}
                onClick={() => select(o)}
                onMouseEnter={() => setHighlight(i)}
                className={cn(
                  'block w-full truncate rounded px-2 py-1 text-left text-sm',
                  i === safeHighlight ? 'bg-accent text-accent-foreground' : 'text-foreground',
                )}
                title={o.label}
              >
                {o.label}
              </button>
            ))}
        </div>
      )}
    </div>
  )
}
