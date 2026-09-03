# Frontend Auth, CSRF & SSE

**Read before modifying:** login and registration, onboarding, proxy middleware, workspace URL routing, SSE proxy routes, CSRF handling, or deployment-mode UI.

## Routes and session gating

- `(auth)/{login,register}` contains unauthenticated screens.
- `(setup)/onboarding` completes first-workspace onboarding.
- `(app)/{workspaces,w/[wsId]/...}` contains authenticated screens.
- `/` redirects an authenticated user to a workspace and everyone else to `/login`.

`proxy.ts` checks the configurable auth-cookie name. It redirects unauthenticated requests for `/w/*`, `/workspaces`, `/admin`, and `/onboarding` to login; it redirects an already authenticated visitor away from login and registration.

`authStore.loadMe()` loads `GET /api/v1/auth/me`. When the returned user has `needs_onboarding`, the app layout sends them to `/onboarding`. The onboarding form calls `POST /api/v1/onboarding` and then navigates to its returned workspace ID.

## Active workspace

The `[wsId]` URL segment is the single source of truth. `useWorkspaceContext()` reads it, and a page's `ApiClient` calls `client.setWorkspaceId(wsId)`. The client rewrites scoped routes:

```
/api/v1/conversations/...  →  /api/v1/ws/{wsId}/conversations/...
```

Auth, onboarding, system, and other explicitly workspace-neutral paths are not rewritten. For browser-direct loads such as images, iframes, links, or pdf.js, use the artifact preview URL builders or `client.resolvePath(...)`.

## CSRF

CSRF is double-submit. `ApiClient` reads the configurable `cubeplex_csrf` cookie and sends it as `X-CSRF-Token` on non-GET calls. Server-side proxy route handlers must forward the request cookie and CSRF header to the backend.

## SSE and long-running proxies

The conversation and run-stream route handlers under `app/api/v1/ws/[wsId]/` forward `cookie`, `X-CSRF-Token`, and `x-user-id`. Workspace scope is always in the URL path, not a header.

Next rewrites buffer SSE when compression is enabled, so keep `compress: false`. The global `/api/*` rewrite has an approximately 30-second proxy timeout. Endpoints that can exceed it, such as sandbox browser live view, need a filesystem route handler under `app/api/...` rather than a longer global rewrite timeout.

## Deployment info

`useDeploymentMode()` reads the public `GET /api/v1/system/info` response. It provides deployment mode, version, sandbox availability, and password policy; the app currently uses it for UI configuration such as sandbox-only controls. It does not represent a system setup state.
