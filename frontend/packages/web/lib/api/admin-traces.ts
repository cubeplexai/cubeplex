import { readApiError } from '@/lib/csrf'
import type {
  TraceDetail,
  TraceFilterValues,
  TraceListResponse,
} from '@/components/admin/traces/types'

function toQuery(filters: TraceFilterValues): string {
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(filters)) {
    if (v === undefined || v === null || v === '') continue
    params.set(k, String(v))
  }
  return params.toString()
}

// Normalize datetime-local inputs (no timezone offset) to full UTC ISO strings
// so the backend always receives a timezone-aware value.
function normalizeFilters(f: TraceFilterValues): TraceFilterValues {
  const out: TraceFilterValues = { ...f }
  for (const k of ['start', 'end'] as const) {
    const v = out[k]
    if (v) {
      const d = new Date(v)
      if (!Number.isNaN(d.getTime())) out[k] = d.toISOString()
    }
  }
  return out
}

export class AdminTracesDisabledError extends Error {
  constructor() {
    super('Admin trace viewer is not configured for this deployment.')
    this.name = 'AdminTracesDisabledError'
  }
}

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { credentials: 'include', signal })
  if (res.status === 503) throw new AdminTracesDisabledError()
  if (!res.ok) throw new Error(await readApiError(res))
  return (await res.json()) as T
}

export async function listAdminTraces(
  filters: TraceFilterValues,
  signal?: AbortSignal,
): Promise<TraceListResponse> {
  const qs = toQuery(normalizeFilters(filters))
  return getJson<TraceListResponse>(`/api/v1/admin/traces${qs ? `?${qs}` : ''}`, signal)
}

export async function getAdminTraceDetail(traceId: string): Promise<TraceDetail> {
  return getJson<TraceDetail>(`/api/v1/admin/traces/${encodeURIComponent(traceId)}`)
}

export async function getAdminTraceTagValues(tag: string): Promise<string[]> {
  const params = new URLSearchParams({ tag })
  const res = await getJson<{ values: string[] }>(
    `/api/v1/admin/traces/tag-values?${params.toString()}`,
  )
  return res.values
}
