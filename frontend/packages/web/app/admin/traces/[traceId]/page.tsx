'use client'

import { use, useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'

import { SpanDetail } from '@/components/admin/traces/SpanDetail'
import { SpanTree } from '@/components/admin/traces/SpanTree'
import type { SpanNode, TraceDetail } from '@/components/admin/traces/types'
import { getAdminTraceDetail } from '@/lib/api/admin-traces'

function findSpan(root: SpanNode, id: string): SpanNode | null {
  if (root.span_id === id) return root
  for (const c of root.children) {
    const hit = findSpan(c, id)
    if (hit) return hit
  }
  return null
}

export default function AdminTraceDetailPage({ params }: { params: Promise<{ traceId: string }> }) {
  const { traceId } = use(params)
  const router = useRouter()
  const sp = useSearchParams()
  const [detail, setDetail] = useState<TraceDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Seed from URL query param; updated only via onSelect (not from URL changes).
  const [selectedSpanId, setSelectedSpanId] = useState<string>(() => sp?.get('span') ?? '')

  // Fetch only when traceId changes, not on every span selection.
  // selectedSpanId is intentionally excluded from the dep array — the effect
  // only needs to re-run when the trace itself changes (traceId), not when
  // the user clicks a different row within the same trace.
  useEffect(() => {
    let cancelled = false
    getAdminTraceDetail(traceId)
      .then((d) => {
        if (cancelled) return
        setDetail(d)
        // Default to the root span only when no span was pre-selected via URL.
        setSelectedSpanId((prev) => prev || d.root.span_id)
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String(e))
      })
    return () => {
      cancelled = true
    }
  }, [traceId])

  const selected = useMemo(
    () => (detail && selectedSpanId ? findSpan(detail.root, selectedSpanId) : null),
    [detail, selectedSpanId],
  )

  const onSelect = useCallback(
    (id: string) => {
      setSelectedSpanId(id)
      const next = new URLSearchParams(sp?.toString() ?? '')
      next.set('span', id)
      router.replace(`?${next.toString()}`)
    },
    [router, sp],
  )

  if (error) return <div className="p-6 text-sm text-destructive">{error}</div>
  if (!detail) return <div className="p-6 text-sm text-muted-foreground">…</div>

  return (
    <div className="grid h-full grid-cols-[420px_1fr] overflow-hidden">
      <div className="overflow-y-auto border-r border-border">
        <div className="border-b border-border px-3 py-2 text-xs text-muted-foreground">
          <div className="font-mono">{detail.summary.trace_id}</div>
          <div>
            {detail.summary.duration_ms} ms · {detail.summary.span_count} spans
          </div>
        </div>
        <SpanTree root={detail.root} selectedSpanId={selectedSpanId} onSelect={onSelect} />
      </div>
      <div className="overflow-y-auto">{selected ? <SpanDetail node={selected} /> : null}</div>
    </div>
  )
}
