# P1 Frontend Feature Parity — Design

**Date:** 2026-04-17
**Status:** Draft — pending review
**Depends on:** P1 backend (PR #24, branch `feat/p1-identity-auth-rbac`)

## Goal

Bring the frontend to feature parity with P1 backend: users can register, log in, hold sessions, switch between multiple workspaces, and have every API call correctly scoped and CSRF-protected. RBAC-aware UI (member-vs-admin gating, invite flows) is explicitly deferred to P2.

## Non-Goals (P2 or later)

- Workspace settings page (members list, role changes, invite-by-email)
- Organization UI (creating/leaving orgs, multi-org-per-user)
- Profile / account settings (change password, email verification)
- Admin-only UI gating (buttons hidden by role)
- Workspace rename/delete
- Cross-workspace conversation "you don't have access, switch?" prompt
- Password reset / forgot-password

## Core Decisions (already agreed with stakeholder)

1. **Auto-bootstrap on register**: backend `UserManager.on_after_register` creates a personal Organization + Workspace + Admin Membership. Registered user can hit any workspace-scoped endpoint immediately.
2. **Workspace is in the URL**: all authenticated app routes live under `/w/[wsId]/…`. URL is the single source of truth for active workspace; no localStorage persistence.
3. **One user = one org (M1 assumption)**: registration creates one org; the workspace-create form uses that org implicitly. If user has multiple workspaces they all belong to the same org. Multi-org-per-user is out of scope.
4. **Middleware handles auth-gate only**: does not inspect workspace membership. Membership errors (404/403) surface from API responses and are handled at the page/component layer.

## User Flows

### First-time user
1. Lands on `/` → no `cubeplex_auth` cookie → middleware redirects to `/login`.
2. Clicks "Register" link → `/register` → submits email + password → backend creates user + org + "Personal" workspace + Admin membership, sets `cubeplex_auth` cookie, returns new workspace id.
3. Frontend redirects to `/w/[newWsId]` → welcome screen with input bar.
4. Types a prompt → `POST /conversations` (scoped to new ws) → redirected to `/w/[wsId]/conversations/[conversationId]`.

### Returning user
1. Lands on `/` → has cookie → frontend calls `GET /workspaces`, picks first (or last-used if we cache the id client-side without localStorage — see Open Question O1) → redirects to `/w/[wsId]`.
2. Same as before from here.

### Workspace switching
1. Top-bar has a workspace dropdown showing current workspace + list of all user's workspaces + "New workspace" + "Manage" links.
2. Clicking another workspace navigates to `/w/[otherWsId]` — conversation list/welcome for that workspace. Any open conversation is dropped (URL change).
3. Clicking "New workspace" → `/workspaces` page with name-only form → `POST /workspaces` with user's implicit org_id → redirects to `/w/[newWsId]`.

### Logout
1. Top-bar avatar menu → "Sign out" → `POST /auth/logout` with CSRF token → clears local React state → redirect `/login`.

### Unauthenticated access to protected route
1. Any `/w/*` or `/workspaces` without valid `cubeplex_auth` cookie → middleware 302 to `/login?next=<original_path>`.
2. After successful login, redirect to `next` if present (validated to be a same-origin path), else first workspace.

### API 401 mid-session (token expired)
1. API client detects 401, clears any in-memory auth state, and redirects to `/login?next=<current_path>`. No toast, no attempt to refresh (P1 uses JWT cookies without refresh tokens).

### API 403 (non-member of the workspace)
1. Page-level handler renders "You don't have access to this workspace" with a button to go to `/workspaces`. No silent retry.

### API 404 on a workspace-scoped resource (conversation not in this ws)
1. Conversation page renders "Conversation not found in this workspace" with a "Back to conversations" link.

## Route Structure

```
/                              — root; redirects based on auth state
/login                         — unauthenticated
/register                      — unauthenticated
/workspaces                    — authenticated; lists workspaces + create form
/w/[wsId]                      — authenticated; conversation list + new-chat entry
/w/[wsId]/conversations/[id]   — authenticated; chat page
```

**Layouts:**
- `app/layout.tsx` — root (theme, fonts). Unchanged.
- `app/(auth)/layout.tsx` — centered card layout for login/register.
- `app/(app)/layout.tsx` — authenticated app chrome (top-bar with workspace switcher + avatar). Wraps `/workspaces` and `/w/[wsId]/…`. Reads `wsId` from route params (when present) to drive the switcher.

**Middleware (`middleware.ts` at frontend root):**
- Inspects `cubeplex_auth` cookie presence.
- If absent and path matches `/w/*` or `/workspaces` → 302 `/login?next=<path>`.
- If present and path is `/login` or `/register` → 302 `/`.
- Does not parse workspace id, does not check membership.

## Data & State

### "Current workspace" resolution
- URL `[wsId]` segment is truth.
- API client reads it from a React context populated by `(app)/layout.tsx` from the route param.
- Stores that previously assumed a single global conversation list (`conversationStore`) are scoped implicitly by the workspace id flowing through API calls — the store is cleared on workspace change (wsId prop changes in layout → effect → `store.reset()`).

### Auth state
- Source of truth: `cubeplex_auth` HTTP-only cookie (server-side).
- Client-side: an `authStore` (Zustand) caches the logged-in user object (`{id, email}`) loaded via `GET /auth/me` after login or on first protected-page mount. Used for avatar display and logout logic. Reset on 401 or logout.

### Workspace list
- `workspaceStore` (Zustand) caches the user's workspace list, refreshed on login and after `POST /workspaces`. Used by the top-bar switcher and the `/workspaces` page.

### CSRF token
- `cubeplex_csrf` cookie is readable by client JS. API client reads it on every mutating request and puts it in `X-CSRF-Token`.
- First protected request (e.g. `GET /auth/me`) seeds the cookie. We call this explicitly post-login before any mutating request.

## API Client Architecture

Current `ApiClient` is too thin — only `get`/`post`, no credentials, no headers. It needs:
- `credentials: 'include'` on every fetch so the browser sends cookies.
- Automatic `X-Workspace-Id` injection for workspace-scoped calls (path matches `/api/v1/conversations` / `/api/v1/artifacts`, not `/auth/*` or `/workspaces`).
- Automatic `X-CSRF-Token` injection on non-GET methods.
- Methods: `get`, `post`, `patch`, `delete`, plus `postForm` for `/auth/login` (form-urlencoded).
- Centralized 401 handling: a single observable so the app can redirect to `/login`.

The client becomes mildly stateful — it holds the current `workspaceId` (set by the app-level context). It does not hold the CSRF token (read from cookie each call) or the auth token (the cookie is HTTP-only anyway).

### Breaking changes to existing `core/api/*`
- `createConversation`, `listConversations`, `listMessages`, `listArtifacts`, `deleteConversation`, `renameConversation`, `listArtifactVersions` — unchanged signatures; they rely on the client injecting the workspace header.
- `streamMessages` (`core/api/stream.ts`) — currently calls raw `fetch` with no credentials or headers. Must be updated to include `credentials: 'include'`, `X-Workspace-Id`, `X-CSRF-Token` (POST is mutating).

### New modules
- `core/api/auth.ts` — `register`, `login`, `logout`, `getMe`.
- `core/api/workspaces.ts` — `listWorkspaces`, `createWorkspace`.
- `core/stores/authStore.ts` — user + reset.
- `core/stores/workspaceStore.ts` — list + active + refresh + create.

## Components

New components under `packages/web/components/`:
- `auth/LoginForm.tsx`
- `auth/RegisterForm.tsx`
- `workspace/WorkspaceSwitcher.tsx` — top-bar dropdown
- `workspace/WorkspaceCreateForm.tsx`
- `workspace/WorkspaceList.tsx` — used on `/workspaces`
- `layout/AppTopBar.tsx` — composes switcher + avatar menu
- `layout/AvatarMenu.tsx` — shows email + sign-out
- `shared/ErrorState.tsx` — standardized "not found" / "forbidden" / "offline" panels

## Error Handling

| Source | Trigger | UI |
|---|---|---|
| 401 anywhere | expired / invalid auth | redirect `/login?next=<path>`, no toast |
| 403 on workspace path | non-member | full-page "No access" + back link |
| 404 on conversation | wrong workspace for that conv, or truly missing | full-page "Not found" + back link |
| Network / 5xx | server down | toast "Something went wrong, retry?" |
| Form validation (login/register) | bad email, short password | inline per-field error from backend `detail.reason` |

Form errors use existing shadcn Form components if present; otherwise add the `form`, `input`, `button`, `alert` shadcn primitives once.

## Testing

### Unit (vitest)
- API client: injects `X-Workspace-Id` only on workspace-scoped paths; injects `X-CSRF-Token` on non-GET; always sets `credentials: 'include'`.
- `authStore`, `workspaceStore`: reset behavior, optimistic update on `createWorkspace`.
- Middleware: the auth-gate decision matrix above.

### E2E (Playwright)
- **Register → auto-login → land on `/w/[wsId]` with welcome screen.** Verify top-bar shows workspace name.
- **Login flow**: register via UI, sign out, log back in, see workspace.
- **Workspace switching**: create a second workspace via UI → switcher shows both → switching changes URL and conversation list is empty.
- **Conversation scoped to workspace**: in ws1 create a conversation, switch to ws2, confirm it's absent.
- **Direct URL access to conv in wrong ws**: manually navigate to ws2's URL holding a ws1 conv id → renders "Not found".
- **Unauthenticated access**: visiting `/w/abc` without cookie → redirected to `/login?next=%2Fw%2Fabc`; after login, ends at `/w/abc` (or first ws if `abc` invalid).
- **CSRF enforced on logout**: logout must succeed; verify by intercepting request that `X-CSRF-Token` is set.

## Backend Changes Required

1. **`cubeplex/auth/users.py::UserManager.on_after_register`** — after user create, open a session and create Organization (name = user email local-part + "'s Org"), Workspace (name = "Personal"), and Admin Membership. Transactionally bind to the same session the register route ran in (inject via manager). If this fails, user creation must roll back so we don't leave a user without a workspace.
2. **`cubeplex/api/routes/v1/auth.py::register`** — response changes from `{id, email}` to `{id, email, default_workspace_id}` so the frontend knows where to send the user after auto-login.
3. **Login stays a 204-cookie response.** Frontend calls `GET /workspaces` right after login to pick a destination. No response-shape change to `/auth/login`.
4. **No new endpoints for organizations.** The P1 "create_workspace accepts client-supplied org_id" gap is unblocked for M1 because the frontend always uses the user's own org_id (read from the workspace list: every workspace carries its org_id, and by the one-user-one-org M1 assumption they all match).

**Deferred backend gaps (stay in the P1 trade-off list):**
- `POST /workspaces` still doesn't validate org membership server-side. Frontend only ever passes the user's implicit org_id, so this stays latent; P2 auth work closes it.
- Workspace enumeration 404-before-403 ordering is unchanged.

## Known Trade-offs & Risks

1. **One-user-one-org assumption** leaks into frontend in two places: `createWorkspace` form reuses the first workspace's `org_id`, and the switcher doesn't show org hierarchy. When P2 adds multi-org-per-user, both need revisiting. Call this out with an inline code comment where the assumption is used.
2. **URL-embedded workspace** means bookmarked URLs to conversations work across user sessions but *break* if the conversation is deleted or the user loses membership — both render clean "not found" / "no access" pages (acceptable).
3. **`streamMessages` currently does raw `fetch`** — the new code path must match exactly the semantics the current streaming uses (no compression, SSE parsing). Breaking this breaks the chat.
4. **Route group reorganization** (`(auth)` and `(app)` layouts) is a refactor, not just additions. Before the move, audit: hardcoded `/conversations/…` strings in components, hooks, and Playwright specs; any `router.push('/conversations/…')` calls; any `useParams` readers; backend redirect targets. Plan includes an explicit audit step.
5. **`middleware.ts` runs on Edge**. It must not import server-only DB code. Cookie presence check only.

## Open Questions (flagged for implementation plan)

- **O1**: Should we remember the last-visited workspace across tab close? We said "no localStorage." But a returning user landing on `/` without it means we always redirect them to "first workspace" which may not be the one they last used. Propose: accept this for M1, document, revisit in P2.
- **O2**: Register form — require email confirmation? P1 backend has `is_verified=False` by default but doesn't gate `is_active` on it. Propose: skip email verification entirely for M1; rely on `is_active` being true by default. Document that email verification is a P2 concern.
- **O3**: The welcome page at `/w/[wsId]` currently shows the centered "cubeplex AI 智能体系统" greeting plus input bar, which is today's `/`. Do we want a conversation list in a sidebar here, or keep it minimal? Propose: keep minimal for M1 (no sidebar rework), sidebar is a P2 polish item.

## Implementation Order (for plan)

1. Backend: `on_after_register` hook + register response shape change + test.
2. Frontend core: `ApiClient` refactor (credentials, headers, methods), new `auth.ts` + `workspaces.ts` modules, `authStore`, `workspaceStore`, tests.
3. Frontend web: `middleware.ts`; `(auth)` route group + login/register pages; basic wiring to redirect after success.
4. Frontend web: `(app)` route group + top-bar + workspace switcher; move existing `/` and `/conversations/[id]` under `/w/[wsId]/…`.
5. Frontend web: `/workspaces` list + create page.
6. Error states: 401 / 403 / 404 page-level handlers, redirect-to-login with `next` param.
7. E2E coverage: Playwright suite above.
8. Docs: update `frontend/CLAUDE.md` with auth + workspace model, `backend/CLAUDE.md` with the `on_after_register` behavior.

## Branch Strategy (for plan)

Since P1 backend PR #24 is still open:
- **Option A**: stack on `feat/p1-identity-auth-rbac` (new branch `feat/p1-frontend-feature-parity` based off it). Frontend PR stays in draft until backend merges.
- **Option B**: wait for backend merge, branch off main.

Defer this decision to the implementation plan kickoff; either works.
