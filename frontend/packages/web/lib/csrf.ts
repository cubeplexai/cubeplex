import { CSRF_COOKIE_NAME } from '@cubeplex/core'

/** Read the CSRF token cookie set by the backend on login. */
export function readCsrfToken(): string {
  if (typeof document === 'undefined') return ''
  const prefix = `${CSRF_COOKIE_NAME}=`
  const match = document.cookie.split('; ').find((c) => c.startsWith(prefix))
  return match ? decodeURIComponent(match.slice(prefix.length)) : ''
}

/** Build standard headers for a JSON-bodied admin request. */
export function jsonHeaders(): HeadersInit {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const csrf = readCsrfToken()
  if (csrf) headers['X-CSRF-Token'] = csrf
  return headers
}

/** Build headers for a non-JSON (multipart / DELETE) admin request. */
export function csrfHeaders(): HeadersInit {
  const headers: Record<string, string> = {}
  const csrf = readCsrfToken()
  if (csrf) headers['X-CSRF-Token'] = csrf
  return headers
}

/** Extract a human-readable error message from an admin endpoint error response. */
export async function readApiError(res: Response): Promise<string> {
  try {
    const data = (await res.json()) as {
      message?: string
      detail?: string | { reason?: string; code?: string; field?: string }
    }
    if (typeof data.detail === 'string') return data.detail
    if (data.detail && typeof data.detail === 'object') {
      const code = data.detail.code ? `${data.detail.code}: ` : ''
      const field = data.detail.field ? `field "${data.detail.field}": ` : ''
      const reason = data.detail.reason ?? ''
      if (code || field || reason) return `${code}${field}${reason}`.trim()
    }
    if (data.message) return data.message
  } catch {
    /* fallthrough */
  }
  return `HTTP ${res.status}`
}
