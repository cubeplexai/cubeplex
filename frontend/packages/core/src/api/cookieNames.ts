/**
 * Auth/CSRF cookie names. Defaults match the backend's `auth.cookie_name` /
 * `auth.csrf_cookie_name` defaults. Worktree dev environments override via
 * `NEXT_PUBLIC_AUTH_COOKIE_NAME` / `NEXT_PUBLIC_CSRF_COOKIE_NAME` so each
 * worktree's browser cookies don't collide on `localhost`.
 *
 * Must use literal `process.env.NEXT_PUBLIC_*` access — Next.js / webpack's
 * DefinePlugin only inlines values for static property access, not bracket
 * notation with a variable key. With dynamic access the client bundle reads
 * `undefined` and silently falls back to the defaults, so the worktree's
 * suffixed cookies never get used and CSRF check fails.
 */
function pick(value: string | undefined, fallback: string): string {
  return value && value.length > 0 ? value : fallback
}

export const AUTH_COOKIE_NAME: string =
  typeof process !== 'undefined' && process.env
    ? pick(process.env.NEXT_PUBLIC_AUTH_COOKIE_NAME, 'cubeplex_auth')
    : 'cubeplex_auth'

export const CSRF_COOKIE_NAME: string =
  typeof process !== 'undefined' && process.env
    ? pick(process.env.NEXT_PUBLIC_CSRF_COOKIE_NAME, 'cubeplex_csrf')
    : 'cubeplex_csrf'
