'use client'

import { use, useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { ArrowLeft } from 'lucide-react'

import { Button } from '@/components/ui/button'
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

// Each chat span already carries its own token usage; sum them across the
// whole tree for a trace-level total instead of adding a backend field.
function sumTokens(node: SpanNode): { input: number; output: number } {
  let input = node.llm?.tokens.input ?? 0
  let output = node.llm?.tokens.output ?? 0
  for (const c of node.children) {
    const child = sumTokens(c)
    input += child.input
    output += child.output
  }
  return { input, output }
}

export default function AdminTraceDetailPage({ params }: { params: Promise<{ traceId: string }> }) {
  const { traceId } = use(params)
  const t = useTranslations('adminTraces')
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
  const totalTokens = useMemo(() => (detail ? sumTokens(detail.root) : null), [detail])

  const onSelect = useCallback(
    (id: string) => {
      setSelectedSpanId(id)
      const next = new URLSearchParams(sp?.toString() ?? '')
      next.set('span', id)
      router.replace(`?${next.toString()}`)
    },
    [router, sp],
  )

  // router.back() returns to the list with whatever filters/preset/page it
  // had (it's the same history entry, just with a `span=` query update on
  // top) - falls back to a fresh /admin/traces when there's nothing to go
  // back to (e.g. this URL was opened directly, not navigated to in-app).
  const handleBack = useCallback(() => {
    if (typeof window !== 'undefined' && window.history.length > 1) {
      router.back()
    } else {
      router.push('/admin/traces')
    }
  }, [router])

  if (error) return <div className="p-6 text-sm text-destructive">{error}</div>
  if (!detail) return <div className="p-6 text-sm text-muted-foreground">…</div>

  return (
    <div className="grid h-full grid-cols-[420px_1fr] overflow-hidden">
      <div className="overflow-y-auto border-r border-border">
        <div className="border-b border-border px-3 py-2 text-xs text-muted-foreground">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="mb-1.5 -ml-2 gap-1.5 px-2"
            onClick={handleBack}
          >
            <ArrowLeft className="size-3.5" />
            {t('backToList')}
          </Button>
          <div className="font-mono">{detail.summary.trace_id}</div>
          <div>
            {detail.summary.duration_ms} ms · {detail.summary.span_count} spans
            {totalTokens && (totalTokens.input > 0 || totalTokens.output > 0)
              ? ` · ${totalTokens.input.toLocaleString()} in / ${totalTokens.output.toLocaleString()} out`
              : ''}
          </div>
        </div>
        <SpanTree root={detail.root} selectedSpanId={selectedSpanId} onSelect={onSelect} />
      </div>
      <div className="overflow-y-auto">{selected ? <SpanDetail node={selected} /> : null}</div>
    </div>
  )
}
