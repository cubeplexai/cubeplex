'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { useTranslations } from 'next-intl'

import { TraceFilterBar } from '@/components/admin/traces/TraceFilterBar'
import { TraceListTable } from '@/components/admin/traces/TraceListTable'
import { Button } from '@/components/ui/button'
import {
  DEFAULT_LIMIT,
  DEFAULT_TIME_RANGE_PRESET,
  type FilterOptionKind,
  type TimeRangePreset,
  type TraceFilterValues,
  type TraceSummary,
} from '@/components/admin/traces/types'
import {
  AdminTracesDisabledError,
  getAdminFilterOptionsByIds,
  listAdminTraces,
} from '@/lib/api/admin-traces'

// Tempo hard-caps search ranges at 168h on this deployment (see backend
// list_traces) - keep every preset comfortably under that.
const PRESET_MS: Record<'1h' | '1d' | '7d', number> = {
  '1h': 60 * 60 * 1000,
  '1d': 24 * 60 * 60 * 1000,
  '7d': 7 * 24 * 60 * 60 * 1000,
}

function isPreset(v: string | null): v is TimeRangePreset {
  return v === '1h' || v === '1d' || v === '7d' || v === 'custom'
}

function valuesFromSearchParams(sp: URLSearchParams): TraceFilterValues {
  const v: TraceFilterValues = {}
  for (const k of ['workspace_id', 'user_id', 'conversation_id', 'run_id', 'model'] as const) {
    const val = sp.get(k)
    if (val) v[k] = val
  }
  // Custom start/end only round-trip through the URL in custom mode -
  // presets are relative-to-now and would go stale as frozen absolute
  // timestamps in a shared/bookmarked URL.
  if (isPreset(sp.get('range')) && sp.get('range') === 'custom') {
    const start = sp.get('start')
    const end = sp.get('end')
    if (start) v.start = start
    if (end) v.end = end
  }
  const limitParam = sp.get('limit')
  v.limit = limitParam && !Number.isNaN(Number(limitParam)) ? Number(limitParam) : DEFAULT_LIMIT
  return v
}

function presetFromSearchParams(sp: URLSearchParams): TimeRangePreset {
  const p = sp.get('range')
  return isPreset(p) ? p : DEFAULT_TIME_RANGE_PRESET
}

// Resolves whichever of `ids` aren't already in `cache`, merging results in.
// Shared by the trace-list-driven resolution (Unit 4) and the filter-value
// resolution below (so a deep-linked ?workspace_id=... shows a name in the
// combobox even when the filtered result set is empty).
async function resolveMissingNames(
  kind: FilterOptionKind,
  ids: (string | null | undefined)[],
  cache: Record<string, string>,
  setCache: (updater: (prev: Record<string, string>) => Record<string, string>) => void,
) {
  const missing = Array.from(new Set(ids.filter((id): id is string => !!id && !(id in cache))))
  if (missing.length === 0) return
  try {
    const opts = await getAdminFilterOptionsByIds(kind, missing)
    if (opts.length === 0) return
    setCache((prev) => {
      const next = { ...prev }
      for (const o of opts) next[o.id] = o.name
      return next
    })
  } catch {
    // Best-effort: the table/combobox fall back to raw ids on failure.
  }
}

export default function AdminTracesPage() {
  const t = useTranslations('adminTraces')
  const router = useRouter()
  const sp = useSearchParams()
  const initialSp = new URLSearchParams(sp?.toString() ?? '')

  const [filters, setFilters] = useState<TraceFilterValues>(() => valuesFromSearchParams(initialSp))
  const [preset, setPreset] = useState<TimeRangePreset>(() => presetFromSearchParams(initialSp))
  const [traces, setTraces] = useState<TraceSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const [disabled, setDisabled] = useState(false)
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  // id -> name caches for the table, resolved in batches as new traces load.
  // Never evicted - they only grow across "load more" pages. Falls back to
  // the raw id when a lookup misses (e.g. a deleted workspace/user).
  const [workspaceNames, setWorkspaceNames] = useState<Record<string, string>>({})
  const [userNames, setUserNames] = useState<Record<string, string>>({})
  const [conversationNames, setConversationNames] = useState<Record<string, string>>({})
  const abortRef = useRef<AbortController | null>(null)
  const tracesRef = useRef<TraceSummary[]>([])
  // The range actually sent to the backend for the currently-loaded page(s):
  // fixed `start`, shrinking `end` on each "load more". Independent of
  // `filters.start`/`end`, which only reflect user input in `custom` mode.
  const rangeAnchorRef = useRef<{ start: string; end: string } | null>(null)

  const effectiveRange = useCallback(
    (f: TraceFilterValues): { start: string; end: string } => {
      if (preset === 'custom') {
        const end = f.end ? new Date(f.end) : new Date()
        const start = f.start ? new Date(f.start) : new Date(end.getTime() - PRESET_MS['1h'])
        return { start: start.toISOString(), end: end.toISOString() }
      }
      const end = new Date()
      const start = new Date(end.getTime() - PRESET_MS[preset])
      return { start: start.toISOString(), end: end.toISOString() }
    },
    [preset],
  )

  const runFetch = useCallback(
    async (
      f: TraceFilterValues,
      range: { start: string; end: string },
      mode: 'replace' | 'more',
    ) => {
      abortRef.current?.abort()
      const ctrl = new AbortController()
      abortRef.current = ctrl
      rangeAnchorRef.current = range
      if (mode === 'replace') setLoading(true)
      else setLoadingMore(true)
      setError(null)
      setDisabled(false)
      try {
        const res = await listAdminTraces({ ...f, start: range.start, end: range.end }, ctrl.signal)
        if (ctrl.signal.aborted) return
        const limit = f.limit ?? DEFAULT_LIMIT
        setHasMore(res.traces.length === limit)
        const next = mode === 'replace' ? res.traces : [...tracesRef.current, ...res.traces]
        tracesRef.current = next
        setTraces(next)
      } catch (e: unknown) {
        if (ctrl.signal.aborted) return
        if (e instanceof AdminTracesDisabledError) {
          setDisabled(true)
        } else {
          setError(e instanceof Error ? e.message : String(e))
        }
      } finally {
        if (!ctrl.signal.aborted) {
          setLoading(false)
          setLoadingMore(false)
        }
      }
    },
    [],
  )

  useEffect(() => {
    const timer = setTimeout(() => {
      void runFetch(filters, effectiveRange(filters), 'replace')
    }, 300)
    return () => clearTimeout(timer)
  }, [filters, preset, effectiveRange, runFetch])

  useEffect(() => {
    void resolveMissingNames(
      'workspace',
      traces.map((tr) => tr.workspace_id),
      workspaceNames,
      setWorkspaceNames,
    )
    void resolveMissingNames(
      'user',
      traces.map((tr) => tr.user_id),
      userNames,
      setUserNames,
    )
    void resolveMissingNames(
      'conversation',
      traces.map((tr) => tr.conversation_id),
      conversationNames,
      setConversationNames,
    )
    // Intentionally keyed on `traces` only: the caches are read for their
    // current value but must not retrigger this effect when they grow.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [traces])

  // Independent of the trace-list resolution above: a deep-linked filter
  // (e.g. ?workspace_id=...) should show a name in the combobox even when
  // the filtered result set is empty (so the trace-list effect never sees
  // that id).
  useEffect(() => {
    void resolveMissingNames('workspace', [filters.workspace_id], workspaceNames, setWorkspaceNames)
    void resolveMissingNames('user', [filters.user_id], userNames, setUserNames)
    void resolveMissingNames(
      'conversation',
      [filters.conversation_id],
      conversationNames,
      setConversationNames,
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.workspace_id, filters.user_id, filters.conversation_id])

  const loadMore = () => {
    const anchor = rangeAnchorRef.current
    const last = tracesRef.current[tracesRef.current.length - 1]
    if (!anchor || !last) return
    const nextEnd = new Date(new Date(last.start_time).getTime() - 1).toISOString()
    void runFetch(filters, { start: anchor.start, end: nextEnd }, 'more')
  }

  const syncUrl = (nextFilters: TraceFilterValues, nextPreset: TimeRangePreset) => {
    const usp = new URLSearchParams()
    for (const [k, v] of Object.entries(nextFilters)) {
      if (k === 'start' || k === 'end') continue
      if (
        v !== undefined &&
        v !== null &&
        v !== '' &&
        (typeof v !== 'number' || Number.isFinite(v))
      )
        usp.set(k, String(v))
    }
    if (nextPreset !== DEFAULT_TIME_RANGE_PRESET) usp.set('range', nextPreset)
    if (nextPreset === 'custom') {
      if (nextFilters.start) usp.set('start', nextFilters.start)
      if (nextFilters.end) usp.set('end', nextFilters.end)
    }
    router.replace(`/admin/traces${usp.toString() ? `?${usp.toString()}` : ''}`)
  }

  const handleFiltersChange = (next: TraceFilterValues) => {
    setFilters(next)
    syncUrl(next, preset)
  }

  const handlePresetChange = (next: TimeRangePreset) => {
    setPreset(next)
    syncUrl(filters, next)
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border bg-card px-6 py-4">
        <h1 className="text-lg font-semibold">{t('title')}</h1>
        <p className="text-sm text-muted-foreground">{t('subtitle')}</p>
      </div>
      <TraceFilterBar
        value={filters}
        onChange={handleFiltersChange}
        preset={preset}
        onPresetChange={handlePresetChange}
        workspaceLabel={filters.workspace_id ? workspaceNames[filters.workspace_id] : undefined}
        userLabel={filters.user_id ? userNames[filters.user_id] : undefined}
        conversationLabel={
          filters.conversation_id ? conversationNames[filters.conversation_id] : undefined
        }
      />
      <div className="flex-1 overflow-auto">
        {disabled && <div className="p-6 text-sm text-muted-foreground">{t('disabled')}</div>}
        {!disabled && loading && (
          <div className="p-6 text-sm text-muted-foreground">{t('loading')}</div>
        )}
        {!disabled && error && <div className="p-6 text-sm text-destructive">{error}</div>}
        {!disabled && !loading && !error && traces.length === 0 && (
          <div className="p-6 text-sm text-muted-foreground">{t('empty')}</div>
        )}
        {!disabled && !loading && !error && traces.length > 0 && (
          <>
            <TraceListTable
              traces={traces}
              workspaceNames={workspaceNames}
              userNames={userNames}
              conversationNames={conversationNames}
            />
            {hasMore && (
              <div className="flex justify-center p-4">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={loadMore}
                  disabled={loadingMore}
                >
                  {loadingMore ? t('loadingMore') : t('loadMore')}
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
