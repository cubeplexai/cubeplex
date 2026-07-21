import { readApiError } from '@/lib/csrf'
import type {
  FilterOption,
  FilterOptionKind,
  TraceDetail,
  TraceFilterValues,
  TraceListResponse,
} from '@/components/admin/traces/types'

function toQuery(filters: TraceFilterValues): string {
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(filters)) {
    if (v === undefined || v === null || v === '') continue
    if (typeof v === 'number' && !Number.isFinite(v)) continue
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
  if (res.status === 401) {
    if (typeof window !== 'undefined') {
      const next = encodeURIComponent(window.location.pathname + window.location.search)
      window.location.assign(`/login?next=${next}`)
    }
    throw new Error('Session expired')
  }
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

export async function getAdminTraceTagValues(tag: string, signal?: AbortSignal): Promise<string[]> {
  const params = new URLSearchParams({ tag })
  const res = await getJson<{ values: string[] }>(
    `/api/v1/admin/traces/tag-values?${params.toString()}`,
    signal,
  )
  return res.values
}

// Dropdown options for workspace/user/conversation, resolved from Postgres
// (org-scoped, prefix-narrowed server-side for user/conversation). `model` is
// NOT served here - use getAdminTraceTagValues for it.
export async function getAdminFilterOptions(
  kind: FilterOptionKind,
  q?: string,
  signal?: AbortSignal,
): Promise<FilterOption[]> {
  const params = new URLSearchParams({ kind })
  if (q) params.set('q', q)
  const res = await getJson<{ options: FilterOption[] }>(
    `/api/v1/admin/traces/filter-options?${params.toString()}`,
    signal,
  )
  return res.options
}

// Batch id -> name resolution for rendering the trace list table (raw IDs
// otherwise). Same endpoint as getAdminFilterOptions, exact-match instead of
// prefix. Missing ids (e.g. a deleted workspace/user) are simply absent from
// the response - callers fall back to the raw id.
export async function getAdminFilterOptionsByIds(
  kind: FilterOptionKind,
  ids: string[],
  signal?: AbortSignal,
): Promise<FilterOption[]> {
  if (ids.length === 0) return []
  const params = new URLSearchParams({ kind })
  for (const id of ids) params.append('ids', id)
  const res = await getJson<{ options: FilterOption[] }>(
    `/api/v1/admin/traces/filter-options?${params.toString()}`,
    signal,
  )
  return res.options
}
