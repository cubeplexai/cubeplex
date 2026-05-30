# Sandbox Env Vault UI

**Date:** 2026-05-31
**Status:** Approved

## What

Two new standalone pages for managing sandbox environment variables (the env vault introduced in PR #137). These pages let org admins and workspace users view, add, rotate, and delete their scoped env entries. Integration into existing pages (admin sandbox policy, workspace sandbox status) is deferred to a later pass.

## Pages

| Page | Route | Scope managed |
|---|---|---|
| Workspace Env | `/w/[wsId]/sandbox-env` | `workspace` (admin only) + `user` (all members) |
| Admin Env | `/admin/sandbox-env` | `org` |

Both pages share the same component structure; they differ only in which API endpoints they call and which scopes they expose.

## Data model (backend, read-only reference)

`EnvEntryOut` fields relevant to the UI:

```ts
id: string
env_name: string           // e.g. "GITHUB_TOKEN"
is_secret: boolean         // secret (masked, needs hosts) vs plain text
scope: 'org' | 'workspace' | 'user'
workspace_id: string | null
user_id: string | null
hosts: string[] | null     // host patterns for substitution targeting
status: string             // 'active' | 'expired' etc.
warnings: string[]         // hosts that conflict with the org sandbox policy
```

Secret values are never returned by the API. `header_names` exists on the model but is not exposed in V1 of this UI.

## Layout

### Workspace page (`/w/[wsId]/sandbox-env`)

The page is role-aware. All members can manage their own user-scope entries; only workspace admins can manage workspace-scope entries.

**For workspace admins:** single merged table with all entries from both `/workspace` and `/me` (fetched in parallel, merged and sorted by `env_name`). Two add buttons: **+ Workspace secret** and **+ Personal secret**.

**For non-admin members:** same table layout but only user-scope entries are shown (only `/me` is fetched). Only **+ Personal secret** button is shown. Workspace-scope rows are not visible.

Table columns:

| Column | Notes |
|---|---|
| NAME | monospace, env var name |
| SCOPE | badge: `ws` (violet, admins only) / `me` (sky) |
| TYPE | `secret` or `plain` |
| HOSTS | comma-joined host patterns, or `—` for plain |
| WARNINGS | warning icon + tooltip listing conflicting hosts; hidden when `warnings` is empty |
| ACTIONS | `rotate` (secrets only) · `delete` |

Empty state: "No environment variables yet. Add a secret or plain value to inject it into your sandbox."

### Admin page (`/admin/sandbox-env`)

Same table layout, no SCOPE column (everything is `org`). Single **+ Add secret** button. Nav label: `Sandbox env` under the existing `Sandbox` entry in `AdminSubNav`.

Sidebar nav entry added after the existing `sandbox` entry:
```
{ href: '/admin/sandbox-env', label: t('sandboxEnv'), icon: KeyRound }
```

## Add / Edit modal

Opens on `+ Add` buttons and on `rotate`. Fields:

```
NAME     [monospace text input, required, max 128 chars]
         Validation: /^[A-Z_][A-Z0-9_]*$/ enforced client-side with inline error

SCOPE    [select: Workspace | Personal]
         Only shown on the workspace page for admins; pre-filled from which button was clicked.
         Non-admins never see this field (always Personal).
         Hidden on admin page (always org).

TYPE     [radio: Secret ● Plain ○]
         Switching clears the value field.

VALUE    [password input when Secret; text input when Plain]
         Secret: required, never pre-filled.
         Plain: required, max 4096 chars.

HOSTS    [tag input, one pattern per tag]
         Shown and required when TYPE = Secret.
         Hidden when TYPE = Plain.
         Each tag validated against host pattern rules on blur (exact FQDN or *.domain.tld).
```

`header_names` is not exposed in V1.

**Rotate flow:** clicking `rotate` opens the same modal with NAME pre-filled and locked, SCOPE/TYPE hidden (can't change), VALUE empty and required. Calls `PATCH .../rotate` on submit.

**Submit behavior:** on success, close modal and refresh the list. On API error, show the error message inline above the footer buttons (same pattern as MCP credential grant errors).

## API endpoints used

### Workspace page

```
GET    /api/v1/ws/{wsId}/sandbox-env/workspace           → EnvEntryListOut  (workspace scope)
GET    /api/v1/ws/{wsId}/sandbox-env/me                  → EnvEntryListOut  (user scope)
POST   /api/v1/ws/{wsId}/sandbox-env/workspace           → EnvEntryOut
POST   /api/v1/ws/{wsId}/sandbox-env/me                  → EnvEntryOut
PATCH  /api/v1/ws/{wsId}/sandbox-env/workspace/{id}      → EnvEntryOut  (rotate secret value)
PATCH  /api/v1/ws/{wsId}/sandbox-env/me/{id}             → EnvEntryOut  (rotate secret value)
DELETE /api/v1/ws/{wsId}/sandbox-env/workspace/{id}
DELETE /api/v1/ws/{wsId}/sandbox-env/me/{id}
```

Workspace admins fire both GET calls in parallel and merge the results into one table, sorted by `env_name`. Non-admin members only call `GET /me`.

The page reads the current user's role from the existing workspace context (same source used by `McpPanel` and other settings pages).

### Admin page

```
GET    /api/v1/admin/sandbox-env          → EnvEntryListOut
POST   /api/v1/admin/sandbox-env          → EnvEntryOut
PATCH  /api/v1/admin/sandbox-env/{id}     → EnvEntryOut  (rotate secret value)
DELETE /api/v1/admin/sandbox-env/{id}
```

## Frontend file structure

```
frontend/packages/core/src/api/sandboxEnv.ts          ← API client functions
frontend/packages/core/src/types/sandboxEnv.ts         ← EnvEntryOut, CreateEnvIn types

frontend/packages/web/app/(app)/w/[wsId]/sandbox-env/
  page.tsx                                              ← workspace page
  _components/
    EnvTable.tsx                                        ← shared table (workspace + admin)
    EnvModal.tsx                                        ← add/rotate modal
    WarningCell.tsx                                     ← warning icon + tooltip

frontend/packages/web/app/admin/sandbox-env/
  page.tsx                                              ← admin page
```

`EnvTable` and `EnvModal` are shared between both pages via props:
- `mode: 'org' | 'workspace-admin' | 'workspace-member'` — controls which columns, scope badge, and buttons appear
- `wsId?: string` — passed only from workspace page
- `entries`, `onAdd`, `onRotate`, `onDelete` callbacks

## Navigation wiring

**Admin sidebar** (`AdminSubNav.tsx`): add entry after `sandbox`:
```ts
{ href: '/admin/sandbox-env', label: t('sandboxEnv'), icon: KeyRound }
```

**Workspace sidebar** (`Sidebar.tsx`): add entry after `sandbox`:
```ts
{ href: `/w/${wsId}/sandbox-env`, labelKey: 'sandboxEnv', icon: KeyRound }
```

i18n keys added to both `adminNav` and workspace nav translation namespaces.

## Warnings display

When `entry.warnings` is non-empty, the WARNINGS cell shows an amber `AlertTriangle` icon. Hovering shows a tooltip:

> "Blocked by network policy: api.example.com"

This matches the existing `CredentialConflictBanner` logic in the admin sandbox policy page, but scoped to the row level.

## Out of scope (V1)

- `header_names` field
- Plain env var editing (plain values can be deleted and re-added; no edit-in-place)
- Integration into the existing `/admin/sandbox` or `/w/[wsId]/sandbox` pages
- User-scope management from the admin page
