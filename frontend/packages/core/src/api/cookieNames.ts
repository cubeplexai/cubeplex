/**
 * Auth/CSRF cookie names. Defaults match the backend's `auth.cookie_name` /
 * `auth.csrf_cookie_name` defaults. Worktree dev environments override via
 * `NEXT_PUBLIC_AUTH_COOKIE_NAME` / `NEXT_PUBLIC_CSRF_COOKIE_NAME` so each
 * worktree's browser cookies don't collide on `localhost`.
 *
 * Resolved at module-eval time. Next.js inlines `process.env.NEXT_PUBLIC_*` at
 * build time for client bundles; for server code (route handlers, middleware,
 * server components) it reads the live env. In test runners the env is
 * unset, so the defaults apply.
 */
function readEnv(name: string): string | undefined {
  if (typeof process === 'undefined' || !process.env) return undefined
  const v = process.env[name]
  return v && v.length > 0 ? v : undefined
}

export const AUTH_COOKIE_NAME: string = readEnv('NEXT_PUBLIC_AUTH_COOKIE_NAME') ?? 'cubebox_auth'

export const CSRF_COOKIE_NAME: string = readEnv('NEXT_PUBLIC_CSRF_COOKIE_NAME') ?? 'cubebox_csrf'
