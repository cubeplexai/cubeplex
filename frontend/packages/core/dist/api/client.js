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
 *     (CSRF_COOKIE_NAME — defaults to "cubebox_csrf"; per-worktree override via
 *     NEXT_PUBLIC_CSRF_COOKIE_NAME).
 *
 * 401 observable: any response with status 401 fires all registered
 * onUnauthorized callbacks. Login 400s do NOT fire.
 */
import { CSRF_COOKIE_NAME } from './cookieNames';
const WS_NEUTRAL_PREFIXES = ['/api/v1/auth/', '/api/v1/workspaces', '/api/v1/admin'];
const SCOPED_ROOT = '/api/v1/';
function isWorkspaceNeutral(path) {
    return WS_NEUTRAL_PREFIXES.some((p) => path === p || path.startsWith(p + '/') || path.startsWith(p + '?') || path.startsWith(p));
}
function injectWorkspace(path, wsId) {
    if (!path.startsWith(SCOPED_ROOT) || isWorkspaceNeutral(path))
        return path;
    if (path.startsWith(`${SCOPED_ROOT}ws/`))
        return path;
    return `${SCOPED_ROOT}ws/${wsId}/${path.slice(SCOPED_ROOT.length)}`;
}
function readCookie(name) {
    if (typeof document === 'undefined')
        return '';
    const match = document.cookie.split('; ').find((c) => c.startsWith(`${name}=`));
    return match ? decodeURIComponent(match.slice(name.length + 1)) : '';
}
export function createApiClient(baseUrl) {
    let workspaceId = null;
    let locale = readCookie('NEXT_LOCALE') || null;
    const unauthorizedHandlers = new Set();
    const resolvePath = (path) => workspaceId ? injectWorkspace(path, workspaceId) : path;
    const buildHeaders = (method, base) => {
        const headers = { ...base };
        if (locale)
            headers['Accept-Language'] = locale;
        if (method !== 'GET') {
            const csrf = readCookie(CSRF_COOKIE_NAME);
            if (csrf)
                headers['X-CSRF-Token'] = csrf;
        }
        return headers;
    };
    const doFetch = async (path, init) => {
        const res = await fetch(`${baseUrl}${resolvePath(path)}`, {
            ...init,
            credentials: 'include',
        });
        // 401 surfaces everywhere EXCEPT on initial auth/login (which returns 400 for
        // bad creds — 401 from login means cookies are malformed, still valid to fire).
        if (res.status === 401) {
            for (const h of unauthorizedHandlers)
                h();
        }
        return res;
    };
    const client = {
        baseUrl,
        get workspaceId() {
            return workspaceId;
        },
        setWorkspaceId(id) {
            workspaceId = id;
        },
        get locale() {
            return locale;
        },
        setLocale(l) {
            locale = l;
        },
        resolvePath,
        get(path) {
            return doFetch(path, {
                method: 'GET',
                headers: buildHeaders('GET', {}),
            });
        },
        post(path, body) {
            return doFetch(path, {
                method: 'POST',
                headers: buildHeaders('POST', { 'Content-Type': 'application/json' }),
                body: JSON.stringify(body),
            });
        },
        postRaw(path, body, headers) {
            return doFetch(path, {
                method: 'POST',
                body: JSON.stringify(body),
                headers: buildHeaders('POST', { 'Content-Type': 'application/json', ...(headers ?? {}) }),
            });
        },
        postForm(path, form) {
            const body = new URLSearchParams(form).toString();
            return doFetch(path, {
                method: 'POST',
                headers: buildHeaders('POST', {
                    'Content-Type': 'application/x-www-form-urlencoded',
                }),
                body,
            });
        },
        put(path, body) {
            return doFetch(path, {
                method: 'PUT',
                headers: buildHeaders('PUT', { 'Content-Type': 'application/json' }),
                body: JSON.stringify(body),
            });
        },
        patch(path, body) {
            return doFetch(path, {
                method: 'PATCH',
                headers: buildHeaders('PATCH', { 'Content-Type': 'application/json' }),
                body: JSON.stringify(body),
            });
        },
        del(path) {
            return doFetch(path, {
                method: 'DELETE',
                headers: buildHeaders('DELETE', {}),
            });
        },
        onUnauthorized(handler) {
            unauthorizedHandlers.add(handler);
            return () => unauthorizedHandlers.delete(handler);
        },
    };
    return client;
}
/**
 * ApiError — preserves the structured `{code, message}` error envelope the
 * backend returns under `detail` so callers can branch on the stable `code`.
 *
 * Falls back to plain `Error` semantics when the body is not JSON.
 */
export class ApiError extends Error {
    constructor(message, status, code, detail) {
        super(message);
        Object.defineProperty(this, "status", {
            enumerable: true,
            configurable: true,
            writable: true,
            value: void 0
        });
        Object.defineProperty(this, "code", {
            enumerable: true,
            configurable: true,
            writable: true,
            value: void 0
        });
        Object.defineProperty(this, "detail", {
            enumerable: true,
            configurable: true,
            writable: true,
            value: void 0
        });
        this.name = 'ApiError';
        this.status = status;
        this.code = code;
        this.detail = detail;
    }
}
export async function toApiError(res) {
    const contentType = res.headers.get('content-type');
    if (contentType?.includes('application/json')) {
        let data;
        try {
            data = (await res.json());
        }
        catch {
            return new ApiError(`HTTP ${res.status}: ${res.statusText}`, res.status, null, null);
        }
        let code = null;
        let message = null;
        let detailFallback;
        if (typeof data.detail === 'string') {
            detailFallback = data.detail;
        }
        else if (data.detail && typeof data.detail === 'object') {
            code = data.detail.code ?? null;
            message = data.detail.message ?? null;
            detailFallback = data.detail.reason;
        }
        const finalMessage = data.message || message || detailFallback || `HTTP ${res.status}`;
        return new ApiError(finalMessage, res.status, code, data.detail ?? null);
    }
    return new ApiError(`HTTP ${res.status}: ${res.statusText}`, res.status, null, null);
}
//# sourceMappingURL=client.js.map