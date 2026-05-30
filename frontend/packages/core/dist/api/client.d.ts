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
export interface ApiClient {
    baseUrl: string;
    workspaceId: string | null;
    setWorkspaceId(id: string | null): void;
    locale: string | null;
    setLocale(locale: string | null): void;
    /** Rewrite a path by injecting the workspace segment when applicable. */
    resolvePath(path: string): string;
    get(path: string): Promise<Response>;
    post(path: string, body: unknown): Promise<Response>;
    postRaw(path: string, body: unknown, headers?: Record<string, string>): Promise<Response>;
    postForm(path: string, form: Record<string, string>): Promise<Response>;
    put(path: string, body: unknown): Promise<Response>;
    patch(path: string, body: unknown): Promise<Response>;
    del(path: string): Promise<Response>;
    onUnauthorized(handler: () => void): () => void;
}
export declare function createApiClient(baseUrl: string): ApiClient;
/**
 * ApiError — preserves the structured `{code, message}` error envelope the
 * backend returns under `detail` so callers can branch on the stable `code`.
 *
 * Falls back to plain `Error` semantics when the body is not JSON.
 */
export declare class ApiError extends Error {
    status: number;
    code: string | null;
    detail: unknown;
    constructor(message: string, status: number, code: string | null, detail: unknown);
}
export declare function toApiError(res: Response): Promise<ApiError>;
//# sourceMappingURL=client.d.ts.map