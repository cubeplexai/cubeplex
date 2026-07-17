# Frontend Auth, CSRF, SSE & Deployment Mode

**Read before modifying:** login/register flows, proxy middleware, workspace
URL routing, SSE proxy route, CSRF handling, deployment-mode UI surfaces.

## Route Structure

- `(auth)/{login,register}` — unauthenticated pages.
- `(app)/{workspaces, w/[wsId]/...}` — authenticated pages.
- `/` — server redirect: logged in → first workspace; else → `/login`.

## Proxy Middleware (`proxy.ts`)

Checks for the `cubeplex_auth` cookie:

- Unauthenticated hits to `/w/*` or `/workspaces` → redirect to
  `/login?next=<path>`.
- Logged-in hits to `/login` or `/register` → redirect to `/`.

## Active Workspace

The URL segment `[wsId]` is the **single source of truth**.
`useWorkspaceContext()` (in the `(app)` tree) reads it.

The `ApiClient` instance each page creates via `createApiClient('')`
calls `client.setWorkspaceId(wsId)`, which automatically rewrites scoped
paths:

```
/api/v1/conversations/...  →  /api/v1/ws/{wsId}/conversations/...
```

Paths under `/api/v1/auth/` and `/api/v1/workspaces` are
workspace-neutral and not rewritten.

For browser-direct loads (`<img>`, `<iframe>`, `<a href>`, pdf.js), use
the URL builders in
`components/panel/artifact/previewUtils.ts`
(`buildPreviewUrl`, `buildDownloadUrl`), or call
`client.resolvePath(...)`.

## CSRF

Double-submit pattern. `ApiClient` reads `cubeplex_csrf` from
`document.cookie` and adds `X-CSRF-Token` on every non-GET. The backend
seeds the cookie on login.

## Stores

- `authStore` — `{id, email}` of the current user, or `null`. Populated
  by `loadMe` on `(app)` mount.
- `workspaceStore` — list of the user's workspaces +
  `create(client, name)` (currently reuses the first workspace's
  `org_id`; **M1 assumption: one user = one org**).

**Known one-user-one-org assumption:** `workspaceStore.create` reads
`workspaces[0].org_id`. When multi-org-per-user ships (P2), this must
take an explicit org id.

## SSE Proxy

The Next.js route handler at
`app/api/v1/ws/[wsId]/conversations/[id]/messages/route.ts` forwards
`cookie`, `X-CSRF-Token`, and `x-user-id` to the backend. Workspace
scoping rides in the URL path, **not a header**.

**Gotcha:** Next.js rewrite buffers SSE if `compress` is on. Keep
`compress: false`.

## Deployment Mode (M9)

The backend exposes `GET /api/v1/system/info` (public, pre-login)
returning `{deployment_mode, version, needs_org_setup}`. The
`useDeploymentMode()` hook in `@cubeplex/core` reads it.

### `single_tenant` (OSS default)

- One shared org for the whole deployment.
- First registrant becomes a **pending owner**; an extra `/setup` step
  (route group `(setup)/setup`) collects org name + slug.
- Subsequent users join the singleton org as members.

### `multi_tenant` (Cloud SaaS)

- Per-user org auto-created on register (current behavior).

### Frontend Contract

- `MeResult` carries `needs_org_setup?: boolean`.
- `(app)/layout.tsx` redirects pending owners (and any user with
  `needs_org_setup === true`) to `/setup`.
- Any UI surface that lets a user create another org or switch between
  orgs **must** be hidden when `mode === 'single_tenant'`. M9 itself
  adds no such surfaces; future work landing org chrome must respect
  this contract.
