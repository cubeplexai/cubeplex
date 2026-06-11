'use client'

import { useCallback, useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { useTranslations } from 'next-intl'

import { TraceFilterBar } from '@/components/admin/traces/TraceFilterBar'
import { TraceListTable } from '@/components/admin/traces/TraceListTable'
import type { TraceFilterValues, TraceSummary } from '@/components/admin/traces/types'
import { AdminTracesDisabledError, listAdminTraces } from '@/lib/api/admin-traces'

function valuesFromSearchParams(sp: URLSearchParams): TraceFilterValues {
  const v: TraceFilterValues = {}
  for (const k of [
    'workspace_id',
    'user_id',
    'conversation_id',
    'run_id',
    'model',
    'start',
    'end',
  ] as const) {
    const val = sp.get(k)
    if (val) v[k] = val
  }
  return v
}

export default function AdminTracesPage() {
  const t = useTranslations('adminTraces')
  const router = useRouter()
  const sp = useSearchParams()
  const [filters, setFilters] = useState<TraceFilterValues>(() =>
    valuesFromSearchParams(new URLSearchParams(sp?.toString() ?? '')),
  )
  const [traces, setTraces] = useState<TraceSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const [disabled, setDisabled] = useState(false)
  const [loading, setLoading] = useState(false)

  const fetchPage = useCallback(async (f: TraceFilterValues) => {
    setLoading(true)
    setError(null)
    setDisabled(false)
    try {
      const res = await listAdminTraces({ ...f, limit: 50 })
      setTraces(res.traces)
    } catch (e: unknown) {
      if (e instanceof AdminTracesDisabledError) {
        setDisabled(true)
      } else {
        setError(e instanceof Error ? e.message : String(e))
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchPage(filters)
  }, [filters, fetchPage])

  const handleChange = (next: TraceFilterValues) => {
    setFilters(next)
    const usp = new URLSearchParams()
    for (const [k, v] of Object.entries(next)) {
      if (v) usp.set(k, String(v))
    }
    router.replace(`/admin/traces${usp.toString() ? `?${usp.toString()}` : ''}`)
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border bg-card px-6 py-4">
        <h1 className="text-lg font-semibold">{t('title')}</h1>
        <p className="text-sm text-muted-foreground">{t('subtitle')}</p>
      </div>
      <TraceFilterBar value={filters} onChange={handleChange} />
      <div className="flex-1 overflow-auto">
        {disabled && <div className="p-6 text-sm text-muted-foreground">{t('disabled')}</div>}
        {!disabled && loading && (
          <div className="p-6 text-sm text-muted-foreground">{t('loading')}</div>
        )}
        {!disabled && error && <div className="p-6 text-sm text-destructive">{error}</div>}
        {!disabled && !loading && !error && traces.length === 0 && (
          <div className="p-6 text-sm text-muted-foreground">{t('empty')}</div>
        )}
        {!disabled && !loading && !error && traces.length > 0 && <TraceListTable traces={traces} />}
      </div>
    </div>
  )
}
