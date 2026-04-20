/**
 * ApiClient — wraps fetch with credentials, workspace-path prefixing,
 * CSRF header injection, and a 401 observable.
 *
 * Path-based rules:
 *   - credentials: 'include' on every call (so cookies flow).
 *   - When workspaceId is set, paths are rewritten:
 *       /api/v1/<scoped>...  ->  /api/v1/ws/<wsId>/<scoped>...
 *     Paths starting with /api/v1/auth/ or /api/v1/workspaces are left alone
 *     (workspace-neutral).
 *   - X-CSRF-Token is injected on non-GET methods, read from document.cookie
 *     (cubebox_csrf).
 *
 * 401 observable: any response with status 401 fires all registered
 * onUnauthorized callbacks. Login 400s do NOT fire.
 */

export interface ApiClient {
  baseUrl: string
  workspaceId: string | null
  setWorkspaceId(id: string | null): void
  /** Rewrite a path by injecting the workspace segment when applicable. */
  resolvePath(path: string): string
  get(path: string): Promise<Response>
  post(path: string, body: unknown): Promise<Response>
  postForm(path: string, form: Record<string, string>): Promise<Response>
  patch(path: string, body: unknown): Promise<Response>
  del(path: string): Promise<Response>
  onUnauthorized(handler: () => void): () => void
}

const WS_NEUTRAL_PREFIXES = ['/api/v1/auth/', '/api/v1/workspaces']
const SCOPED_ROOT = '/api/v1/'

function isWorkspaceNeutral(path: string): boolean {
  return WS_NEUTRAL_PREFIXES.some(
    (p) => path === p || path.startsWith(p + '/') || path.startsWith(p + '?') || path.startsWith(p)
  )
}

function injectWorkspace(path: string, wsId: string): string {
  if (!path.startsWith(SCOPED_ROOT) || isWorkspaceNeutral(path)) return path
  if (path.startsWith(`${SCOPED_ROOT}ws/`)) return path
  return `${SCOPED_ROOT}ws/${wsId}/${path.slice(SCOPED_ROOT.length)}`
}

function readCookie(name: string): string {
  if (typeof document === 'undefined') return ''
  const match = document.cookie.split('; ').find((c) => c.startsWith(`${name}=`))
  return match ? decodeURIComponent(match.slice(name.length + 1)) : ''
}

export function createApiClient(baseUrl: string): ApiClient {
  let workspaceId: string | null = null
  const unauthorizedHandlers = new Set<() => void>()

  const resolvePath = (path: string): string =>
    workspaceId ? injectWorkspace(path, workspaceId) : path

  const buildHeaders = (method: string, base: Record<string, string>) => {
    const headers: Record<string, string> = { ...base }
    if (method !== 'GET') {
      const csrf = readCookie('cubebox_csrf')
      if (csrf) headers['X-CSRF-Token'] = csrf
    }
    return headers
  }

  const doFetch = async (path: string, init: RequestInit): Promise<Response> => {
    const res = await fetch(`${baseUrl}${resolvePath(path)}`, {
      ...init,
      credentials: 'include',
    })
    // 401 surfaces everywhere EXCEPT on initial auth/login (which returns 400 for
    // bad creds — 401 from login means cookies are malformed, still valid to fire).
    if (res.status === 401) {
      for (const h of unauthorizedHandlers) h()
    }
    return res
  }

  const client: ApiClient = {
    baseUrl,
    get workspaceId() {
      return workspaceId
    },
    setWorkspaceId(id) {
      workspaceId = id
    },
    resolvePath,
    get(path) {
      return doFetch(path, {
        method: 'GET',
        headers: buildHeaders('GET', {}),
      })
    },
    post(path, body) {
      return doFetch(path, {
        method: 'POST',
        headers: buildHeaders('POST', { 'Content-Type': 'application/json' }),
        body: JSON.stringify(body),
      })
    },
    postForm(path, form) {
      const body = new URLSearchParams(form).toString()
      return doFetch(path, {
        method: 'POST',
        headers: buildHeaders('POST', {
          'Content-Type': 'application/x-www-form-urlencoded',
        }),
        body,
      })
    },
    patch(path, body) {
      return doFetch(path, {
        method: 'PATCH',
        headers: buildHeaders('PATCH', { 'Content-Type': 'application/json' }),
        body: JSON.stringify(body),
      })
    },
    del(path) {
      return doFetch(path, {
        method: 'DELETE',
        headers: buildHeaders('DELETE', {}),
      })
    },
    onUnauthorized(handler) {
      unauthorizedHandlers.add(handler)
      return () => unauthorizedHandlers.delete(handler)
    },
  }
  return client
}

export async function toApiError(res: Response): Promise<Error> {
  const contentType = res.headers.get('content-type')
  if (contentType?.includes('application/json')) {
    const data = (await res.json()) as { message?: string; detail?: string | { reason?: string } }
    const detail =
      typeof data.detail === 'string'
        ? data.detail
        : (data.detail as { reason?: string } | undefined)?.reason
    return new Error(data.message || detail || `HTTP ${res.status}`)
  }
  return new Error(`HTTP ${res.status}: ${res.statusText}`)
}
