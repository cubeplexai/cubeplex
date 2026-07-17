# MCP Install → Authentication Handoff (Interaction Spec)

**Status:** Draft for review
**Author:** xfgong
**Date:** 2026-05-16
**Scope:** Interaction logic only (front-end UX + the contract it requires from
the back-end). Implementation details for OAuth start / callback wiring are
deferred to a separate plan.

## 1. Problem

The four-layer refactor (`template / install / state / grant`) split install
creation from credential authorization on purpose, but the UI was shipped with
only the install half wired. Today, clicking **Install** (admin or workspace
view) creates a row with `auth_status='pending'` and stops. There is no OAuth
window, no static-credential form, and no nudge telling the user something is
left to do. The connector silently fails the next time it is invoked.

This spec defines the interaction logic that bridges the gap: how the UI guides
a user from "install created" to "ready to use" for every combination of
**caller role × `credential_policy` × `auth_method`**.

It does **not** redesign the catalog, install panel, or status pills — those
landed in the four-layer UI cut and stay. It adds one new surface (the
**Authentication action band**) inside the existing detail panel, and one new
front-end utility (the **OAuth pop-up controller**).

## 2. Mental model

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │ Connector lifecycle from the UI's point of view                      │
 │                                                                       │
 │   (a) pick template  →  (b) install  →  (c) authorize  →  (d) ready  │
 │                                                                       │
 │   "Install" is step (b).                                              │
 │   "Authorize" is step (c) — a separate, resumable step.               │
 │   Users must understand they're in step (c) and what action ends it.  │
 └──────────────────────────────────────────────────────────────────────┘
```

Three guiding rules carried over from the four-layer design:

- **Install and grant are independent records.** A user can re-authorize
  without re-installing; an admin can rotate a grant without disturbing the
  install row.
- **The required grant scope is decided by the install + workspace state, not
  by the caller.** The UI never asks the user to choose between "org grant"
  vs. "user grant" — the back-end returns `required_grant_scope`, and the UI
  surfaces only the action band that matches the caller's authority over that
  scope.
- **`auth_status` on the install is not the source of truth for "usable."**
  The effective-state DTO's `credential_availability` + `usable` + `reason`
  are. The action band consumes those fields.

## 3. The Authentication action band

A horizontal band rendered **inside the existing detail panel**, immediately
under the title row of `MCPAdminDetailPanel` (admin view) and
`McpPanel`'s `ConnectorDetail` (workspace settings view). It is the **only**
new component this spec introduces.

The band has five mutually exclusive states. Exactly one is visible at any
time. Picking the right state is a pure function of `MCPEffectiveConnector`
(via `connector.required_grant_scope`, `connector.credential_availability`,
`connector.install.auth_method`, `connector.install.auth_status`) **plus** the
caller's role on the workspace (`role: 'admin' | 'member'`) and, for the admin
console, the org-admin bit. The function is centralized so both surfaces agree.

### 3.1 State `ready` — credential is satisfied

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │  ✓  Ready  ·  credential from <source>                               │
 │                                                       [Disconnect ▾] │
 └──────────────────────────────────────────────────────────────────────┘
```

- Shown when `connector.usable === true`. Two sub-cases:
  - `credential_availability === 'available'` AND `credential_source !=
    null` → render "credential from `<source>`" where `<source>` is one of
    `Org grant`, `Workspace grant`, `My grant` (mapped from the
    backend's `credential_source` value `org` / `workspace` / `user`; the
    backend already maps internal `"none"` to JSON `null` in
    `ws_mcp.py::_dto_to_effective_out`).
  - `credential_availability === 'not_required'` (i.e.
    `install.auth_method === 'none'`) → render "No credential required"
    instead. `credential_source` is `null` on this path; do NOT predicate
    `ready` on its non-nullness, otherwise the no-auth happy path is
    excluded.
- The `Disconnect` menu offers `Remove org grant`, `Remove workspace grant`,
  `Remove my grant` depending on which scopes the caller may revoke. A
  member who only owns a `me` grant sees a single `Remove my grant` button —
  not a menu. When `credential_availability === 'not_required'`, the
  Disconnect control is omitted entirely (there is no grant to revoke).

### 3.2 State `needs-action` — caller must authorize

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │  ⚠  Needs your credential  ·  <reason copy>                          │
 │                                                                       │
 │  [ Connect with <Provider> ]            (oauth)                       │
 │  ── or ──                                                             │
 │  Static token                                                         │
 │  [ ••••••••••••••••••••••••••••••••••• ]  [ Save credential ]        │
 └──────────────────────────────────────────────────────────────────────┘
```

- Shown when `connector.reason` is one of the **auth-related** tokens
  emitted by `backend/cubeplex/mcp/effective.py` —
  `pending_oauth | missing_org_grant | missing_workspace_grant |
  user_needs_connection | grant_expired` — AND the caller has authority
  to create the grant at `required_grant_scope` (see §4). Do NOT key the
  predicate solely on `credential_availability === 'missing'`: that field
  is also `missing` for non-auth blockers (`not_installed`,
  `install_uninstalled`, `template_deprecated`,
  `not_enabled_in_workspace`, `discovery_failed`), and the action band
  has no copy for those — they belong to other surfaces:
  - `not_enabled_in_workspace` is handled by the existing workspace
    enable/disable toggle (`McpPanel.ConnectorDetail`), not this band.
  - `discovery_failed` surfaces as an inline error message in the
    detail panel's existing "tools" area, not the action band; the
    fix is to retry discovery, not to (re-)authorize.
  - `not_installed`, `install_uninstalled`, `template_deprecated`
    cannot reach a state where the action band is shown — the panel
    they would render in is the install panel or an "uninstalled"
    placeholder, both outside this band's scope.
- `<reason copy>` translates `connector.reason` (full `MCPEffectiveReason`
  literal for reference: `usable | not_installed | not_enabled_in_workspace
  | install_uninstalled | template_deprecated | pending_oauth |
  missing_org_grant | missing_workspace_grant | user_needs_connection |
  grant_expired | discovery_failed`):
  - `pending_oauth` → "Authorization is pending — finish connecting
    to start using this." (Backend emits `pending_oauth` whenever
    `auth_status='pending'` AND no grant exists, which is the state
    of every fresh org/workspace OAuth install before anyone has
    clicked Connect — so do NOT phrase this as "was started but not
    finished" or the copy lies on the post-install state.)
  - `missing_org_grant` (caller is org admin) → "No org credential on file
    yet."
  - `missing_workspace_grant` (caller is workspace admin) → "No workspace
    credential on file yet."
  - `user_needs_connection` → "Connect your account to start using this."
  - `grant_expired` → "The previous authorization expired."

  The `missing_org_grant` / `missing_workspace_grant` reasons also reach
  callers without authority — those are routed to the `awaiting-others`
  band in §3.3 with their own copy, not this band.
- Auth method controls which controls appear:
  - `install.auth_method='oauth'` → only the **Connect with `<Provider>`**
    primary button.
  - `install.auth_method='static'` → only the static token input + Save.
  - Templates that declare both in `supported_auth_methods` but the install
    was created with one specific method → only that one. (Switching auth
    method requires uninstalling + reinstalling; that's an existing
    constraint and not in scope here.)

### 3.3 State `awaiting-others` — caller cannot fix it

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │  ⏳  Awaiting <who>                                                   │
 │      <who> needs to authorize this connector before it can run.      │
 │                                                            [ Notify ] │
 └──────────────────────────────────────────────────────────────────────┘
```

- Shown when **all three** hold:
  1. `connector.reason ∈ { missing_org_grant, missing_workspace_grant,
     pending_oauth, grant_expired }` — the auth-blocker subset of
     `MCPEffectiveReason`. Do NOT key on `credential_availability ===
     'missing'` alone: that field also fires for `not_installed`,
     `install_uninstalled`, `template_deprecated`,
     `not_enabled_in_workspace`, and `discovery_failed`, none of which
     are "another actor needs to authorize" situations. Those non-auth
     reasons must be filtered out before the authority check so they
     reach their proper surfaces (workspace toggle, install panel,
     discovery error card) rather than this band with empty copy.
  2. `connector.required_grant_scope ∈ { org, workspace }` — user-scope
     grants always belong to the caller and never block on someone
     else, so `awaiting-others` cannot apply when the required scope is
     `user`.
  3. The caller does **not** have authority over `required_grant_scope`
     (see §4: non-admin viewing an org-policy install, or non-admin
     viewing a workspace-policy install).
- Reason → copy (the `<who>` is derived from `required_grant_scope`,
  not from the reason token, because `pending_oauth` and `grant_expired`
  can fire under either policy):
  - `required_grant_scope === 'org'` → `<who> = "your organization
    admin"`. The reason token (`missing_org_grant` / `pending_oauth` /
    `grant_expired`) determines the second sentence:
    - `missing_org_grant` → "Your org admin hasn't authorized this
      yet."
    - `pending_oauth` → "Your org admin hasn't connected this yet."
      (Same caveat as §3.2 — `pending_oauth` is the backend's signal
      for "an authorization is required and has not landed," not
      strictly "an authorization was started.")
    - `grant_expired` → "The org's authorization expired and needs
      to be renewed."
  - `required_grant_scope === 'workspace'` → `<who> = "your workspace
    admin"`. Same three reason variants with "org" → "workspace" in
    the second sentence.

  `user_needs_connection` cannot reach this band: it is by construction
  the caller's own grant (rule 7 with `credential_policy='user'` only
  fires for the calling user). It belongs in `needs-action` (§3.2).
- `[Notify]` is a stub for v1 — disabled with tooltip "Coming soon." Listed
  in the spec for layout completeness; not in MVP scope.

### 3.4 State `oauth-in-flight` — transient

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │  ⏳  Waiting for authorization in the new window…                     │
 │      Finish the sign-in there; we'll pick it up automatically.       │
 │                                                              [Cancel] │
 └──────────────────────────────────────────────────────────────────────┘
```

- Replaces the `needs-action` band only after the OAuth pop-up has been
  opened.
- `[Cancel]` aborts: closes the pop-up if still open, drops the local
  pending state, returns to `needs-action`. The OAuth state token on the
  server remains valid until its natural expiry (idempotent, no server call
  on cancel — keeps the UX cheap and avoids racing with a slow callback).
- 90-second client-side timeout (rationale in §5.5). On timeout, transitions
  to a recoverable error band with retry button. 90 s, not 30 s: the OAuth
  AS often shows MFA prompts that legitimately take a minute.

### 3.5 State `error` — recoverable failure

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │  ✗  Could not save credential                                         │
 │      <short error reason from server / pop-up return>                │
 │                                                              [Retry] │
 └──────────────────────────────────────────────────────────────────────┘
```

- Reached from `oauth-in-flight` (return page reports `status=error`, or
  client timeout fires) or from a failed static `Save credential` POST.
- `[Retry]` transitions back to `needs-action` (the OAuth state token is
  short-lived enough — minutes — that re-triggering `oauth/start` is the
  correct path; we don't try to reuse the previous state token).

## 4. Caller authority matrix

This decides which scope's action band the caller sees. It is a pure function
of the install's `required_grant_scope` and the caller's role. The back-end
remains the authority — these rules just decide what's worth showing in the
UI.

| `required_grant_scope` | Admin console view (org admin) | Workspace settings — workspace admin | Workspace settings — workspace member |
| --- | --- | --- | --- |
| `org` | `needs-action` (caller acts) | `awaiting-others` ("org admin") | `awaiting-others` ("org admin") |
| `workspace` | `needs-action` (caller acts on the lens workspace) | `needs-action` (caller acts) | `awaiting-others` ("workspace admin") |
| `user` | `needs-action` (the admin's own user grant) | `needs-action` (member's own user grant) | `needs-action` (member's own user grant) |
| `none` | `ready` immediately | `ready` immediately | `ready` immediately |

Notes on the admin console row:

- The admin page already has a **workspace lens** (`lensWsId` in
  `app/admin/mcp/page.tsx`). For `required=workspace`, the action band acts on
  that lens — it surfaces and creates the workspace grant for whichever
  workspace the admin is currently viewing. The API call still goes to the
  workspace-scoped endpoint (`/api/v1/ws/{lensWsId}/mcp/installs/{id}/grants/workspace[…]`),
  not to an admin alias — there is no admin alias for workspace grants and
  this spec does not add one.
- An admin viewing an org-policy install still sees the `org` action band on
  the org-wide install row when `required_grant_scope === 'org'`.
  **This row bypasses the workspace-state lens entirely.** The org
  grant is authored at install/org level — there is no workspace it
  "belongs to," so the action band must NOT consult the lens
  workspace's effective DTO (which would surface
  `not_enabled_in_workspace` for any org install created with
  `auto_enable.mode='none'`, hiding the org Connect action behind a
  blocker that's irrelevant to org-grant authoring). The admin
  page therefore derives the org row's action band from the install
  row and the org-scope grant directly:
  Decision order (first match wins):

  1. `install.auth_method == 'none'` → `(usable=true, reason='usable')`.
  2. Org-scope grant exists AND `grant_status == 'valid'` →
     `(usable=true, reason='usable')`.
  3. Org-scope grant exists AND `grant_status == 'expired'` AND no
     refresh credential available (for OAuth) →
     `(usable=false, reason='grant_expired')`.
  4. No org-scope grant exists AND `install.auth_method == 'oauth'`
     AND `install.auth_status == 'pending'` →
     `(usable=false, reason='pending_oauth')`. Mirrors
     `compute_effective_state` rule 6.
  5. No org-scope grant exists, otherwise →
     `(usable=false, reason='missing_org_grant')`. This branch
     deliberately covers the static-org case where
     `install.auth_status` stays `'pending'` until the first grant
     lands (the static grant flow does not touch `auth_status`),
     AND the OAuth-org rotation case where `auth_status='authorized'`
     but the grant has been disconnected. The earlier draft of this
     section gated `missing_org_grant` on `auth_status='authorized'`,
     which left fresh static org installs falling through to
     `usable` and hid the token input — the bug codex flagged in
     review round 11.

  Concretely: today `list_admin_installs` returns raw install rows
  with no effective state. The admin page needs either a new admin
  effective DTO (org-row specific, computed from install + org grant,
  no workspace lens) or a small derivation helper on the front end.
  Either is acceptable; the spec only constrains the inputs and the
  reason-token output.
- `required_grant_scope` is a **single** value resolved by the backend from
  one effective policy: `workspace_state.credential_policy` when present,
  otherwise `install.default_credential_policy` (see
  `backend/cubeplex/mcp/effective.py` rules 3-4). There is no simultaneous
  multi-scope-missing case to disambiguate — exactly one scope is required
  per (install, workspace) pair. If a workspace overrides an org-policy
  install down to `user`, the band displayed on the admin's lens row for
  that workspace becomes the `user` (per-admin) variant, not `org`.

## 5. End-to-end interaction flows

### 5.1 Workspace admin installs a `user`-policy connector, members authorize (e.g. Linear)

Install creation is admin-only (`backend/cubeplex/api/routes/v1/ws_mcp.py::
create_workspace_install` depends on `require_admin`), but the `user`-policy
means each member runs their own OAuth flow against their own grant. This is
the two-actor flow.

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │ Actor A — workspace admin: install                                 │
 │  Template list → click "Connect" on Linear                          │
 │   ↓                                                                 │
 │  POST /api/v1/ws/{ws}/mcp/installs        (require_admin)           │
 │     { template_id, install_scope: "workspace",                      │
 │       auth_method: "oauth", default_credential_policy: "user" }     │
 │   ↓                                                                 │
 │  list reload + auto-select the new install (existing behavior)      │
 │                                                                     │
 │  Admin's own detail panel now renders the same `needs-action`       │
 │  band as any member would see — the admin can authorize their own   │
 │  user grant immediately if they want to use the connector too.      │
 │                                                                     │
 │ Actor B — workspace member (and admin, separately): authorize       │
 │  Open the connector in workspace settings                           │
 │  Detail panel renders with action band in state `needs-action`     │
 │  (because the member's own user grant is missing — reason           │
 │   `user_needs_connection`)                                          │
 │   ↓ click "Connect with Linear"                                     │
 │   ↓                                                                 │
 │  POST /api/v1/ws/{ws}/mcp/installs/{id}/grants/me/oauth/start       │
 │     (require_member — any workspace member may do this)             │
 │     → { authorize_url, state, expires_at }                          │
 │   ↓                                                                 │
 │  controller navigates the already-open popup to authorize_url       │
 │  Band transitions to `oauth-in-flight`                              │
 │                                                                     │
 │ Step 3 — pop-up returns                                             │
 │  AS → /api/v1/oauth/mcp/callback?state=…&code=…                     │
 │  Backend exchanges code, upserts MCPCredentialGrant(scope='user',   │
 │  user_id=<the authorizing member>), redirects 302 to                │
 │  /oauth/mcp/return?install_id=…&status=ok&state=…                   │
 │   ↓                                                                 │
 │  /oauth/mcp/return posts a typed message and closes itself          │
 │   ↓                                                                 │
 │  Parent receives message → re-fetches the effective connector      │
 │  Band transitions to `ready`                                        │
 └─────────────────────────────────────────────────────────────────────┘
```

**New UI rule introduced by this spec:** the front end MUST hide the
template list section in `McpPanel.tsx` (the "Connect to a new template"
sub-pane) from workspace members who are not admins. Today the section is
rendered to all members, so a non-admin who clicks the template-row
"Connect" button hits the install POST and gets rejected by
`require_admin`. The action band concept assumes members only see
connectors an admin has already installed and their only authoring action
is creating their own user grant; this UI rule keeps that invariant.

### 5.2 Workspace admin installs a `workspace`-policy connector

Same as 5.1 with two differences:

- `oauth/start` goes to `…/grants/workspace/oauth/start`.
- The `Disconnect` menu in the `ready` state offers `Remove workspace grant`
  (the admin can't independently rotate a `user` grant they don't own).

### 5.3 Org admin installs an org-policy connector with auto-distribution

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │ Step 1 — install                                                    │
 │  Admin → Templates → "Connect" on GitHub Enterprise                 │
 │   ↓                                                                 │
 │  POST /api/v1/admin/mcp/installs                                    │
 │     { template_id, install_scope: "org",                            │
 │       auth_method: "oauth", default_credential_policy: "org",       │
 │       auto_enable: { mode: "all" } }                                │
 │   ↓                                                                 │
 │  list reload + auto-select; detail panel opens.                     │
 │                                                                     │
 │ Step 2 — authorize once at the org level                            │
 │  Action band `needs-action` (caller authority = org admin)         │
 │   ↓ "Connect with GitHub"                                          │
 │  POST /api/v1/admin/mcp/installs/{id}/grants/org/oauth/start        │
 │  → { authorize_url, state, expires_at }                             │
 │  → controller navigates the popup to authorize_url                  │
 │                                                                     │
 │ Step 3 — callback writes one org-scope grant                        │
 │  → grant_scope='org', workspace_id=null, user_id=null               │
 │  → install.auth_status='authorized'                                 │
 │  → 302 /oauth/mcp/return?install_id=…&status=ok&state=…             │
 │   ↓ return page postMessage → parent refresh                       │
 │  Detail panel → `ready` from the org admin's view AND from every    │
 │  workspace member's view, because credential_source='org'.          │
 └─────────────────────────────────────────────────────────────────────┘
```

For an `auto_enable.mode: "selected"` install, the flow is identical — the
distribution payload only controls workspace fan-out (the
`workspace_connector_state` rows), not credential acquisition.

### 5.4 Static token install (e.g. an API-key-only connector)

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │ Step 1 — install with auth_method='static'                          │
 │  (same POST shape as 5.1, just auth_method swapped)                 │
 │                                                                     │
 │ Step 2 — submit token inline                                        │
 │  Action band `needs-action` shows token input + Save               │
 │   ↓                                                                 │
 │  POST /api/v1/ws/{ws}/mcp/installs/{id}/grants/me                   │
 │     { credential_plaintext: "<token>", name: null }                 │
 │     (or .../grants/workspace, .../admin/.../grants/org per scope)   │
 │   ↓                                                                 │
 │  re-fetch effective connector → band transitions to `ready`         │
 └─────────────────────────────────────────────────────────────────────┘
```

The token input is **never persisted in client state across renders.**
Submission clears it; rerendering the band after refresh shows the empty
input again. A successful Save also clears any `error` banner.

### 5.5 OAuth pop-up controller (front-end utility)

A single TypeScript helper exported from `@cubeplex/core` named
`runOAuthFlow`. Inputs: the `oauth/start` URL (preformed per scope) and an
`ApiClient`. Outputs: a Promise that resolves to `'ok' | 'cancelled' | 'error'`
plus optional `reason`.

```
 runOAuthFlow (called synchronously from the user-activation click):
 ─────────────────────────────────────────────────────────────────────
   1. Open a blank popup synchronously, BEFORE any await:
        const target = `mcp-oauth-${crypto.randomUUID()}`;
        const child = window.open('about:blank', target,
                                  'width=620,height=760');
      If `child === null` → resolve('error', 'popup_blocked')
      and return immediately. The synchronous open is required
      because browsers gate window.open on the user-activation
      token, which is consumed by the click handler and lost the
      moment we `await` anything. Awaiting `oauth/start` first
      causes legitimate clicks to be reported as `popup_blocked`
      even with popups allowed.

      The target name MUST be per-flow unique (UUID suffix above),
      not a fixed string like `'mcp-oauth'`. With a fixed name,
      starting a second OAuth flow before the first finishes
      causes window.open to reuse the existing popup — the second
      controller navigates the first controller's popup away
      from its authorize URL, and the first controller times out
      or reports cancelled. The strict per-flow `state` filter
      assumes each popup completes its own redirect chain; a
      shared window breaks that assumption.
   2. Open a BroadcastChannel named `cubeplex-mcp-oauth`.
   3. await POST <oauth/start URL>; receive { authorize_url, state }.
      - On network/server error → child.close(); resolve('error',
        'start_failed').
   4. child.location.href = authorize_url;
      (navigates the already-open blank popup to the AS).
   5. Race:
        - message on the channel where `state === <our state>`
          (strict equality, no sentinels — see §5.6 for why this is
          safe across multiple parallel in-flight flows).
        - 90s setTimeout
        - 1s polling: child.closed && no message yet → 'cancelled'
   6. On `ok` message → resolve('ok').
   7. On `cancelled` message (user denied at the AS) → resolve('cancelled').
   8. On `error` message → resolve('error', reason).
   9. On timeout → child.close(); resolve('error', 'timeout').
  10. Always: close the BroadcastChannel.
```

The 90 s timeout reasoning: AS providers vary, but MFA + consent screens of
~60 s are common. 30 s is too aggressive (false-positive errors on slow
MFA), 5 min is too long (a closed pop-up should fail fast). 90 s sits in the
middle; we can revise after telemetry.

The 1 s `child.closed` polling is the only way to detect "user closed the
pop-up without going through" — `window.close` does not fire a message.

`BroadcastChannel` is used instead of `window.opener.postMessage` because
the return page may have been re-parented (sandbox redirects, security
policies); the channel works regardless of opener chain as long as both
pages are same-origin. Same-origin is guaranteed because the return page is
served by our own Next.js app at `/oauth/mcp/return`.

### 5.6 The return page (`/oauth/mcp/return`)

Already referenced by the back-end stub (`mcp_oauth.py:30-32`). This spec
fixes its contract:

- Query params:
  - `status` — `ok | error | cancelled` (required).
  - `state` — the OAuth state token (required). The callback ALWAYS
    knows its state, even on error/denial paths, because the state
    token is recovered from the request that the AS redirected to us
    (or from our own ticket cookie on AS-initiated errors). Marking
    this required eliminates the failure mode where an error redirect
    omits state and the popup's BroadcastChannel filter discards the
    message — the user would otherwise see a `timeout` instead of the
    real server reason.
  - `install_id` — optional. Present on `ok` paths; may be empty on
    AS-side errors where the back end did not finish state-token
    decoding. Consumers must not assume non-empty.
  - `reason` — optional. Short machine-friendly token (e.g.
    `state_mismatch`, `callback_not_wired`, `grant_write_failed`,
    `user_denied`).

- The callback MUST always include the real state value. The state
  token is recoverable on every legitimate path — from the AS's
  redirect query, or from our own ticket cookie. The only case
  where state is genuinely unrecoverable is a hostile or stray
  navigation directly to `/oauth/mcp/return` with no AS context.
  In that case the return page MUST NOT broadcast on the channel
  and MUST NOT auto-close: render a static "Sign-in failed,
  please close this window and retry from the connector page."
  fallback. Any in-flight controller in another tab will time out
  at 90 s on its own; we accept that delay as the cost of avoiding
  a sentinel that would either mis-resolve parallel flows (if the
  parent accepts it) or get masked by the `child.closed` poll (if
  the parent ignores it).

- Behavior:
  1. Post a typed message on `BroadcastChannel('cubeplex-mcp-oauth')`:
     `{ kind: 'mcp.oauth.return', status, install_id, reason, state }`.
  2. After a 250 ms grace period (lets the channel deliver in slow tabs),
     call `window.close()`.
  3. If `window.close()` fails because the page wasn't opened by script
     (rare, defensive), render a one-line "You can close this window."
     fallback so the user isn't stuck on a blank tab.

## 6. Back-end contract this UI requires

For completeness; the actual wiring lands in a separate plan.

- `POST .../grants/<scope>/oauth/start` returns 200 with
  `{ authorize_url: string, state: string, expires_at: ISO8601 }`.
  Currently 501 in `admin_mcp.py:373-389`, `ws_mcp.py:396-414` and
  `ws_mcp.py:499-517`.

- `GET /api/v1/oauth/mcp/callback` exchanges the code, upserts the grant,
  302s to `/oauth/mcp/return?install_id=…&status=…&state=…[&reason=…]`.
  Currently a hard-coded error stub in `mcp_oauth.py:42-58`.

- `POST .../grants/<scope>` accepts `{ credential_plaintext, name? }` and
  returns the grant status. Already implemented; the UI just needs to call
  it from the action band.

- `DELETE .../grants/<scope>` returns 204. Already implemented; the action
  band's `Disconnect` menu calls it.

- The effective-connector DTO (`MCPEffectiveConnectorOut`) must populate
  `reason` whenever `usable=false` — the action band's copy keys off of it.
  Most paths already do; the spec audits this in the implementation plan.

## 7. Edge cases

1. **Install row created with `auth_method='none'`.** Band immediately renders
   `ready`. No action required. Confirmed by §4 row 4.

2. **User dismisses the OAuth pop-up by clicking outside / closing the tab.**
   Detected by the `child.closed` poll (§5.5 step 4). Falls into
   `cancelled`, not `error`; band returns to `needs-action`. No server
   round-trip on this path.

3. **Browser blocks the pop-up.** `window.open` returns `null`. The helper
   detects this and resolves `('error', 'popup_blocked')`. The band copy
   for this `reason` tells the user to allow pop-ups for this site, with a
   `[Retry]` that re-attempts after they've changed the setting.

4. **State token expired between `oauth/start` and the callback.** Back-end
   sets `status=error&reason=state_expired`. Band shows `error` with
   "Authorization timed out, please retry."

5. **Org admin starts org OAuth, but another admin already authorized in
   parallel.** Callback's grant upsert is idempotent (uniqueness on `(install_id,
   grant_scope, workspace_id, user_id)`). Whichever finishes second writes a
   newer credential; both action bands transition to `ready` on the next
   effective-connector fetch. We deliberately don't try to lock the start
   path: the AS is the bottleneck.

6. **User finishes OAuth in pop-up but the parent tab was closed.** The
   grant is still persisted server-side. When the user reopens the tab and
   loads the connector list, the action band renders `ready` from the
   effective state. No data loss.

7. **`required_grant_scope` changes between `start` and `callback`** (an
   admin flipped the install's `default_credential_policy` mid-flow). The
   callback writes the grant at whatever scope the state token committed
   to; effective-state recomputes on next fetch and may now show
   `needs-action` for a *different* scope. The previous flow's success is
   not wasted — that grant still exists and may be referenced again later.

8. **A workspace member sees an `awaiting-others` band; admin authorizes
   from another tab.** The band does not auto-refresh; the user must reload
   the list (or the detail panel). Live updates are out of scope for v1 —
   see §9.

## 8. What this spec is NOT

- Not a redesign of the install panel, status pills, or workspace lens. All
  three keep their existing visual treatment.
- Not a redesign of the four-layer data model. The DTOs are taken as given.
- Not a notification system. The `Notify` button in `awaiting-others` is a
  layout placeholder.
- Not a live-update mechanism. No SSE / websocket subscriptions on the
  detail panel. Refresh is by user action (reload, navigate, switch tabs in
  the panel).
- Not a "skip auth" or "test-only" mode. Every install with
  `auth_method != 'none'` requires a grant before the agent can call its
  tools.

## 9. Future work (deferred)

- Auto-refresh action band when a same-org admin completes the grant from
  another session (likely via a workspace-scoped SSE event).
- `Notify` button: opens a message to org/workspace admin via an in-app
  inbox.
- Telemetry on `runOAuthFlow` outcomes to validate the 90 s timeout
  threshold.
- Multi-account hint in the OAuth pop-up controller: prefill the AS with a
  `login_hint` derived from the user's email when the template advertises
  support (`oauth_login_hint_supported` template flag — not in this round).

## 10. Open questions

None blocking. The two judgement calls — putting the auth band inside the
detail panel rather than spawning a modal, and the 90 s OAuth pop-up timeout
— are taken as decisions of this spec. If telemetry or review feedback
contradicts them, they can be revisited without changing the data contract
between front-end and back-end.
