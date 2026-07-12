# MCP Lost-UI Restoration (Interaction Spec)

**Status:** Draft for review
**Author:** xfgong
**Date:** 2026-05-16
**Scope:** Restoration of MCP management UI / endpoints that were
removed in commit `243e6396` (legacy catalog/server/override cleanup)
but should still exist on top of the new four-layer data model
(`template / install / state / grant`).

This spec does NOT change the four-layer data model. It only adds
back surfaces that consume fields already on `MCPConnectorInstall`
(`tools_cache`, `tool_citations`, `last_error`, `discovery_status`)
and one missing route shape (`install_scope` promotion + custom
install creation + refresh + test-connection).

## 1. Problem

The four-layer refactor deleted ~21k lines while moving to the new
data model. Some deletions were correct (legacy `mcp_servers` table,
the `workspace_mcp_overrides` shim). Some were UI/route layers that
have no equivalent on the four-layer model yet, so the regression is
silent: the data is still in the DB, but the user can no longer see
or trigger it.

User-visible regressions:

- **No tool list.** Can't see what tools a connector exposes,
  search them, or inspect their JSON Schema. The data is on
  `install.tools_cache`; nothing renders it.
- **No "Refresh tools" button.** Tools are only re-discovered the
  next time an agent run touches the connector. Admins/users
  can't force a refresh from the UI.
- **No "Test connection" check.** When creating a custom
  connector the admin has no way to verify the URL + auth are
  reachable before saving.
- **No citation mapping editor.** `install.tool_citations` JSON
  is on the model and `tool_citations` is wired into the agent
  runtime (see `cubeplex.middleware.citation`), but the UI to
  set it has been gone since `243e6396`.
- **No custom connector creation.** Catalog-installs require a
  `template_id`, but the data model has always allowed
  `template_id=None`. There is no UI path (and no API path —
  `ws_mcp.py` rejects `template_id is None` at line 132-143)
  to make a one-off custom install.
- **No "Promote to org-wide" flow.** A workspace admin who
  realizes other workspaces would also want a connector has no
  way to lift the install scope from `workspace` to `org`. Old
  flow was `POST /servers/{id}/promote-to-org` (deleted).
- **Discovery errors are buried.** The install has a
  `last_error` field, but the only place it surfaces is a single
  line in the Overview tab's `dl`. There used to be an
  attention-getting `ServerErrorBanner` that surfaced this at
  the top of the detail panel.

## 2. Mental model

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │ What the user wants to do with a connector once it's installed       │
 │                                                                       │
 │  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐               │
 │  │  see tools  │    │  fix errors │    │   tune it   │               │
 │  │ (Tier 1.1)  │    │ (Tier 1.2)  │    │ (Tier 2.4)  │               │
 │  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘               │
 │         ↓                  ↓                   ↓                       │
 │    list+detail+      ServerErrorBanner    citation editor             │
 │    refresh button     +  Refresh           per tool                   │
 │                                                                       │
 │  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐               │
 │  │ test before │    │ add a one- │    │ promote up  │               │
 │  │   saving    │    │off install │    │  the scope  │               │
 │  │ (Tier 1.3)  │    │ (Tier 2.5) │    │ (Tier 2.6) │               │
 │  └──────┬──────┘    └─────┬──────┘    └─────┬──────┘               │
 │         ↓                  ↓                  ↓                       │
 │    test-connection    custom-install     promote dialog               │
 │    button              panel              (ws→org)                    │
 └──────────────────────────────────────────────────────────────────────┘
```

All of these are **read-only consumers of the four-layer model** or
**new write endpoints whose effect is captured by existing four-layer
state** (e.g. promote = update `install.install_scope`).

## 3. The eight features

### 3.1 Tools tab — list, detail, schema view (Tier 1.1)

```
 ┌────────────────────────┬─────────────────────────────────────────┐
 │ ┌────────────────────┐ │  search_repos                            │
 │ │ 🔍 filter tools    │ │  Search GitHub repositories.             │
 │ └────────────────────┘ │                                          │
 │                        │  ┌─[Schema]─[Raw JSON]──────────────┐    │
 │  ▸ search_repos        │  │ query: string  *required          │    │
 │    Search repos…       │  │   Query string. Example: cubeplex  │    │
 │    2 args · 1 required │  │ sort: enum  optional              │    │
 │  ▸ create_issue        │  │   Sort order. One of "stars",     │    │
 │    Create a new issue. │  │   "forks", "updated".             │    │
 │    4 args · 2 required │  │ per_page: integer  optional       │    │
 │  ▸ list_pull_requests  │  │   Default 30. Max 100.            │    │
 │    …                   │  └───────────────────────────────────┘    │
 │                        │                                          │
 │  12 of 23 tools        │                                          │
 └────────────────────────┴─────────────────────────────────────────┘
```

New Tabs tab "Tools" mounted between Overview and Workspaces in
`MCPAdminDetailPanel` (and same shape inside workspace settings
`ConnectorDetail`). Master-detail layout — left rail is filtered
list of `install.tools_cache[]`, right pane is the selected tool's
detail.

- **Search** is client-side over `tool.name + tool.description`.
- **Detail view** has two sub-tabs:
  - **Schema** — pretty-rendered `tool.input_schema` with the
    `SchemaParameterRow` layout (name + type badge + required
    pill + description + nested children for object types).
  - **Raw JSON** — collapsed JSON of the schema, for users who
    want to copy-paste into their own tooling.
  - **Try It** — invoke the tool live; see §3.8.
- Empty state: "Discovery has not run yet — click Refresh tools."
- Filtered-empty state: "No tools match 'foo'."

Data source: `install.tools_cache` (list of `{name, description,
input_schema}` rows that backend discovery writes).

**Required schema additions (the existing API does NOT expose
these fields):**

`MCPConnectorInstallOut` currently returns `tool_count` (an
integer derived from `len(install.tools_cache)`) but not the
list itself, and `MCPEffectiveConnectorOut` only embeds the
install row, so the UI today has no way to read tool metadata
even though the column is populated. This spec extends both:

- Add `tools: list[MCPToolEntry]` to `MCPConnectorInstallOut`
  (and therefore to `MCPEffectiveConnectorOut.install`). The
  shape mirrors the JSON in `tools_cache`:
  `{ name: str, description: str | None, input_schema: dict
    | None }`. Read-only, derived from `install.tools_cache`.
- Add `tool_citations: dict[str, CitationConfigJSON] | None`
  to the same DTOs. Source of truth is
  `install.tool_citations`. Omitted (or `null`) for non-admin
  callers since the citation editor (§3.7) is admin-only.

Why on the existing list endpoints (not a new sub-route): the
Tools tab opens in the same network round trip that already
fetches the effective connector. A separate `GET
/installs/{id}/tools` would mean a double-fetch on every detail
open and force the UI to juggle a second loading state for no
caching gain — the JSON is small enough (~kB) that piggybacking
on the existing fetch is the right tradeoff.

### 3.2 Refresh tools button (Tier 1.2)

```
 ┌──────────────────────────────────────────────────────────────────┐
 │ [⟳ Refresh tools]    ← already in the header, currently just
 │                        re-fetches the list. Spec: actually
 │                        trigger discovery on the backend.
 └──────────────────────────────────────────────────────────────────┘
```

The header already has a "Refresh tools" button in
`MCPAdminDetailPanel.tsx:147-156`, but `handleRefresh` calls
`onRefresh()` which only re-fetches the connector list. This
spec changes the wiring so the button hits a new backend
endpoint that re-runs discovery against the MCP server, then
re-fetches.

New backend route:

```
POST /api/v1/admin/mcp/installs/{install_id}/refresh-discovery   (org admin)
  body: { workspace_id?: str }
POST /api/v1/ws/{ws}/mcp/installs/{install_id}/refresh-discovery (ws admin)
  body: {}
```

Both return the updated `MCPConnectorInstallOut`. Behavior:
- Resolve install. Build the cubepi client per the install's
  effective grant (resolved via the same path the agent runtime
  uses — `MCPEffectiveConnectorService._resolve_grant`). For:
  - Org-policy installs: the org grant (workspace-independent).
  - Workspace-policy installs: the grant for the workspace
    context — `{ws}` from the path on the ws route, or
    `body.workspace_id` on the admin route. Admin route 422s
    with `workspace_id_required_for_scoped_policy` if the
    install's effective policy is workspace or user and the
    body omits it.
  - User-policy installs: the caller's own user grant within
    the workspace context. For the admin route, the admin
    needs both a workspace context AND a user grant THERE —
    if their own user grant in the picked workspace doesn't
    exist, 400 `admin_lacks_user_grant_for_discovery` so the
    admin authorizes via Try It's workspace picker first.
- Call `client.list_tools()`.
- Update `install.tools_cache`, `install.discovery_status`,
  `install.last_error` per the result.
- Return the updated install row.

UX:
- Button shows a spinner.
- On success: toast "Discovered N tools" — the Tools tab
  re-renders with the new list.
- On failure: `last_error` populates and the Error banner
  (§3.6) shows the message.

Authority:
- Admin route: org admin only.
- Workspace route: **workspace admin only** (NOT any member).
  Discovery writes shared install-level state — `tools_cache`,
  `discovery_status`, `last_error`. A member running discovery
  with their own (potentially limited or expired) grant would
  let one user clobber the cache for the whole workspace and
  flip the install into `discovery_status='error'`, which the
  effective-state rules treat as unusable for every other
  member. Restricting to workspace admin keeps the surface
  available without making shared state a per-member soft
  vandalism vector. Members who want a refresh ask their
  workspace admin (or, for org-scope installs, an org admin).
- The button in the UI is hidden from non-admin members and
  shown with a "Workspace admin only" tooltip if a member ever
  reaches it via a deep link.

### 3.3 Test connection (Tier 1.3)

```
 ┌──────────────────────────────────────────────────────────────────┐
 │  Add custom connector                                            │
 │  ─────────────────────────                                       │
 │  Server URL    [ https://example.com/mcp                      ]  │
 │  Transport     ( http )  ( sse )                                 │
 │  Auth method   ( none ) ( static ) ( oauth )                     │
 │  Static token  [ ●●●●●●●●●●●●●●●●●●                          ]   │
 │                                                                  │
 │  [ Test connection ]                                             │
 │      ✓ Reachable · 12 tools discovered                           │
 │                                                                  │
 │  [ Cancel ]    [ Save install ]                                  │
 └──────────────────────────────────────────────────────────────────┘
```

Lives inside the Custom Install panel (§3.5). Optional — admin
can save without testing, but the button surfaces a clear
"this will work" / "this won't work" signal before commit.

New backend route:

```
POST /api/v1/admin/mcp/test-connection
  body: { server_url, transport, auth_method, credential_plaintext?, headers? }
  → { ok, tool_count, error_code?, error_message? }
```

The route does NOT write to the DB. It:
1. Builds a transient cubepi client from the body fields.
2. Calls `client.list_tools()` with a 10-second timeout.
3. Returns `(ok, tool_count)` or `(ok=false, error)`.

Authority: org admin only. Workspace settings has no equivalent
because workspace settings doesn't yet have a custom-install
flow (admins create installs).

### 3.4 Discovery error banner (Tier 2.7)

```
 ┌──────────────────────────────────────────────────────────────────┐
 │ ⚠  Discovery failed · 2 minutes ago                              │
 │     ConnectError: getaddrinfo ENOTFOUND example.invalid          │
 │     Last successful discovery: 12 hours ago.            [⟳ Retry] │
 └──────────────────────────────────────────────────────────────────┘
```

Banner inserted right under the title row when
`install.discovery_status === 'error'`. Reads `install.last_error`
verbatim. The Retry button is a shortcut to §3.2.

Cleanup: today's overview tab's `dl` shows
`discoveryStatus: error` as a tiny key-value row. The banner is
the same data, just more visible. We keep the dl row for
non-error states (lets admins see when last discovery succeeded).

No new backend.

### 3.5 Custom connector creation (Tier 2.5)

```
 ┌──────────────────────────────────────────────────────────────────┐
 │  ←  Add custom connector                                         │
 │                                                                  │
 │  Name           [ My internal MCP                              ] │
 │  Server URL     [ https://mcp.internal.corp/mcp                ] │
 │  Transport      ( http ▼ )                                       │
 │  Auth method    ( static ▼ )                                     │
 │  Credential     [ ●●●●●●●●●●●●●●●●●●●●                         ] │
 │  Policy         ( org / workspace / user )                       │
 │                                                                  │
 │  [ Test connection ]   ✓ Reachable · 7 tools                     │
 │                                                                  │
 │  [ Cancel ]              [ Save install ]                        │
 └──────────────────────────────────────────────────────────────────┘
```

New entry in the admin templates sidebar (under
"Connector templates" section): **+ Add custom connector**. Clicking
opens a panel (replacing the install panel) with the form above.

Backend changes needed:
- `ws_mcp.py:132-143` currently rejects `template_id is None`. Lift
  that to ONLY admin route: `admin_mcp.py POST /installs` accepts
  `template_id: None` (custom), `ws_mcp.py POST /installs` keeps
  the `template_id is required` guard (workspace admins can only
  install from the catalog; custom is org admin only).
- Add a new service method
  `MCPConnectorInstallService.create_custom_install_for_org`
  alongside the existing `create_from_template_for_org`. The
  existing template method dereferences `template.id`,
  `template.name`, `template.server_url`,
  `template.supported_auth_methods`, etc. — there is no custom
  branch today, so relaxing the route alone leaves the service
  layer with no path to build the install row. The new method
  accepts the same shape the route sends:
  `(name, server_url, transport, auth_method,
   default_credential_policy, headers, auto_enable)` and writes
  the install with `template_id=None`. Install uniqueness is
  enforced by the existing partial unique indexes
  (`uq_install_org_url_active_org` /
  `uq_install_ws_url_active_ws` on
  `(org_id [, workspace_id], server_url_hash)` filtered by
  `install_state='active'`) — the install model has no `slug`
  column, so there is nothing to synthesize. Workspace state
  fan-out reuses the existing distribution helper.

Schema additions:
- `AdminCreateInstallIn`: when `template_id is None`, require
  `name`, `server_url`, `transport`. `headers`, `default_credential_policy`
  optional. Cross-field validator.
- Per `auth_method='static'` + `default_credential_policy='org'`
  ONLY, the endpoint accepts an optional `credential_plaintext`
  on the same POST so the org-scope static grant is created in
  one round trip instead of two. For `workspace`/`user` policy
  the body alone is insufficient (workspace_id and possibly
  user_id are missing from this admin POST), so the schema
  validator rejects `credential_plaintext` with 422
  `credential_plaintext_only_valid_for_org_policy` and the admin
  uses the existing two-step flow (create install → create
  grant via the workspace-scoped grant endpoint, which has the
  right scope fields). For OAuth, the user still goes through
  the OAuth pop-up after install creation as today.

The custom install reuses the action band for credential
authorization just like a catalog install (§install→auth handoff
spec). The only difference is no template name in the title.

Auto-enable distribution is supported (the existing
`auto_enable: { mode: 'all' | 'selected' | 'none' }` body shape
on admin install).

### 3.6 Promote workspace → org (Tier 2.6)

```
 ┌──────────────────────────────────────────────────────────────────┐
 │  Promote "My internal MCP" to org-wide?                          │
 │  ────────────────────────────────────────                        │
 │  This install will become available to all workspaces in your    │
 │  org. The existing grant stays attached to your workspace; you   │
 │  can leave it or convert to an org-scope grant later.            │
 │                                                                  │
 │  Distribute to                                                   │
 │  ( ) All current and future workspaces                           │
 │  (●) Only this workspace (the others can opt in)                 │
 │                                                                  │
 │  [ Cancel ]                                  [ Promote install ] │
 └──────────────────────────────────────────────────────────────────┘
```

A new entry in the admin/workspace detail panel's overflow menu
(next to Uninstall): **Promote to org-wide**. Only visible when:
- The caller is an org admin (org-admin role on the install's
  org). Workspace settings must therefore compute and pass
  `isOrgAdmin` honestly — the install→auth-handoff spec defaulted
  it to `false` because that page had no org-admin context for
  the action band, but the Promote action genuinely needs the
  real value. New helper hook `useOrgAdminFlag(orgId)` reads from
  `useAuthStore` / `GET /api/v1/auth/me` (which already returns
  the caller's org memberships and roles). Workspace settings'
  detail panel passes the result into the menu's visibility
  gate.
- `install.install_scope === 'workspace'`.

New backend route:

```
POST /api/v1/admin/mcp/installs/{install_id}/promote-to-org
  body: { distribution: { mode: "all" | "selected" | "none",
                          workspace_ids?: list[str] } }
  → MCPConnectorInstallOut
```

Behavior:
- Validate install exists, is in caller's org, currently
  `install_scope == 'workspace'`, `install_state == 'active'`.
- Update `install.install_scope = 'org'`, `install.workspace_id =
  None`.
- Apply distribution per `auto_enable`. Reuses the same per-
  workspace upsert as `create_from_template_for_org`, BUT the
  source workspace (the install's pre-promote `workspace_id`)
  is **excluded** from the fan-out write — its existing state
  row is preserved untouched, including any override on
  `credential_policy` or `enabled` (matches §6.4 promise).
  Modes:
  - `mode='all'` → upsert state rows in every OTHER workspace
    in the org (skip source). Set
    `install.auto_enroll_new_workspaces = true` so workspaces
    created LATER also get the state row via
    `enroll_workspace_in_org_wide_mcp()` on workspace creation.
  - `mode='selected'` → upsert in the listed ids, MINUS the
    source if it appears in the list. Set
    `install.auto_enroll_new_workspaces = false` — the admin
    explicitly picked a subset, future workspaces must NOT be
    auto-included; an admin who later wants to add another
    workspace toggles its state row from the existing
    workspace-state PATCH endpoint.
  - `mode='none'` → no fan-out at all; only the source's
    pre-existing state row stays. Same as `selected`: set
    `install.auto_enroll_new_workspaces = false`.
- Existing grant for the workspace stays attached at scope
  `workspace`. The admin can later create an org grant (existing
  endpoint).
- Return the updated install.

Edge cases:
- Caller is workspace admin but NOT org admin → 403. The promote
  button must be hidden in that case to avoid the dead end.
- Concurrent promote (two admins) → the unique partial index on
  (`org_id, server_url_hash, install_scope='org'`) catches the
  duplicate; second caller gets a clean 409 with
  `code='org_install_already_exists'`.

### 3.7 Citation mapping editor (Tier 2.4)

```
 ┌─────────────────────────┬─────────────────────────────────────────┐
 │  Tools                  │  search_repos                            │
 │  ─────                  │  ──────────                              │
 │  ▸ search_repos    ✓    │  Citation source                         │
 │  ▸ create_issue   —    │  ( web ) ( document ) ( api )            │
 │  ▸ list_pull_req…  ✓    │                                          │
 │                         │  Content type                            │
 │                         │  ( json ) ( text ) ( html )              │
 │                         │                                          │
 │                         │  Content field                           │
 │                         │  [ items[].body                        ] │
 │                         │                                          │
 │                         │  Field mapping                           │
 │                         │  title    [ items[].title              ] │
 │                         │  url      [ items[].html_url           ] │
 │                         │  snippet  [ items[].description        ] │
 │                         │                                          │
 │                         │     ✓ Mapped ·  [Copy from peer ▾]       │
 │                         │                                          │
 │                         │  [ Reset ]              [ Save mapping ] │
 └─────────────────────────┴─────────────────────────────────────────┘
```

New tab "Citations" between "Tools" and "Workspaces" in
`MCPAdminDetailPanel`, hidden from non-admin workspace member view.
Lists every tool from `install.tools_cache`. Selecting a tool shows
its citation config — read from `install.tool_citations[<tool_name>]`.

Backend changes:
- Add `PUT /api/v1/admin/mcp/installs/{install_id}/tool-citations`
  body: `{ tool_name: str, config: CitationConfigJSON | null }`.
  Sets / clears one tool's mapping. Returns the updated install.
- Existing `tool_citations: dict[str, dict]` JSON field is the
  source of truth; the route is a thin upsert into that dict.

UX rules:
- "Mapped" checkmark on a tool name in the left rail iff there's
  a non-null config for that tool.
- "Copy from peer" pulls the existing config of another install
  that has the same `tool.name`. Peers come from the same org's
  active installs. Helpful when a team has a Notion-style
  connector configured and wants to reuse the mapping shape on a
  different deployment of the same template.
- "Reset" sends `config: null` (clears the row).
- This editor lives ONLY in the admin detail panel. Workspace
  settings users see citations via the agent at runtime but can't
  edit them.

### 3.8 "Try it" view (Tier 3.10)

```
 ┌──────────────────────────────────────────────────────────────────┐
 │  Try it · search_repos                                           │
 │  ────────────────────                                            │
 │                                                                  │
 │  Arguments                                                       │
 │  query   * [ cubeplex                                          ]  │
 │  sort      [ stars ▼ ]                                           │
 │  per_page  [ 10                                                ] │
 │                                                                  │
 │  [ ▶ Invoke ]                                                    │
 │                                                                  │
 │  Result · 320 ms                                                 │
 │  ┌──────────────────────────────────────────────────────────┐    │
 │  │ { "items": [ { "name": "xfgong/cubeplex", "stargazers": …│    │
 │  │ ...                                                       │    │
 │  └──────────────────────────────────────────────────────────┘    │
 │                                                                  │
 │  [ Copy result ]   [ Clear ]                                     │
 └──────────────────────────────────────────────────────────────────┘
```

Third sub-tab inside the Tools detail pane (alongside Schema +
Raw JSON). Lets the user invoke the tool from the UI for debugging
the connector.

New backend routes (one per surface — admin page has no workspace
path parameter, so the workspace-scoped route alone leaves the
admin Try It with no callable endpoint):

```
POST /api/v1/admin/mcp/installs/{install_id}/tools/{tool_name}/invoke
  body: { workspace_id?: str, arguments: dict }
  → { ok, result?, error?, duration_ms }

POST /api/v1/ws/{ws}/mcp/installs/{install_id}/tools/{tool_name}/invoke
  body: { arguments: dict }
  → { ok, result?, error?, duration_ms }
```

Behavior:
- Build the cubepi client per the install + the effective grant
  resolved from the install's policy via
  `MCPEffectiveConnectorService._resolve_grant`. This is the
  SAME resolution the agent runtime uses, with NO cross-scope
  fallback:
  - Org-policy install → uses the org grant. If missing, 400
    `connector_not_usable` (`reason='missing_org_grant'`).
  - Workspace-policy install → uses the (install, workspace)
    grant. If missing, 400 `connector_not_usable`.
  - User-policy install → uses the (install, workspace, user)
    grant for the calling user. If missing, 400
    `connector_not_usable` (`reason='user_needs_connection'`).
  Falling back across scopes would change the credential
  boundary vs. agent runtime — a user-policy install would
  silently use an org or workspace grant the agent itself
  would never accept.
- For the **admin route**: the admin viewing an `install_scope =
  'org'` install needs a workspace context for any grant lookup
  that is keyed on workspace (i.e. `workspace`- or `user`-policy
  installs). The body's `workspace_id` is REQUIRED for those
  cases — auto-picking the admin's "first membership" would be
  non-deterministic for an admin in multiple workspaces and could
  resolve to a workspace with no grant when a different one has
  one. So:
  - Org-policy install → `workspace_id` ignored (not needed; the
    org grant is workspace-independent).
  - Workspace-policy or user-policy install → `workspace_id` is
    REQUIRED on the body. 422
    `workspace_id_required_for_scoped_policy` if missing. The
    admin UI provides a workspace picker on the Try It panel
    when the effective policy is workspace or user.
  - For `install_scope = 'workspace'` installs the admin route
    404s — admins should call the workspace route directly.
- 10-second timeout on the underlying invoke.
- Returns `result` JSON (whatever the tool returned) or `error`
  string.
- Audit logged: `mcp.tool.invoked` with `(install_id, tool_name,
  workspace_id, user_id)` so the surface has a trace.

Authority:
- Admin route: org admin only.
- Workspace route: any workspace member with a grant that makes
  the connector `usable`. If the connector is not `usable`, the
  Invoke button is disabled with tooltip "Authorize the
  connector first."

Rate-limit: 30 invocations / minute / user (slowapi key on user
id). Try It is for debugging, not a substitute for the agent.

## 4. Caller authority matrix

| Feature | Org admin (admin page) | Workspace admin (ws settings) | Workspace member (ws settings) |
| --- | --- | --- | --- |
| 3.1 Tools tab — view | yes | yes | yes |
| 3.2 Refresh tools | yes | yes | no (admin only — §3.2) |
| 3.3 Test connection | yes | n/a (no custom install flow) | n/a |
| 3.4 Error banner | yes | yes | yes |
| 3.5 Custom install | yes | no | no |
| 3.6 Promote ws → org | yes | yes IF org admin too (§3.6) | no |
| 3.7 Citation editor | yes | no | no |
| 3.8 Try it | yes | yes | yes (rate-limited) |

The admin-only features (3.3, 3.5, 3.6, 3.7) are gated on
`isOrgAdmin === true` (caller has `OrgRole.OWNER | ADMIN` on the
install's org). For 3.6 Promote specifically, the workspace
settings page must compute `isOrgAdmin` honestly from
`GET /api/v1/auth/me` so an org admin viewing a workspace-scope
install in workspace settings can still reach Promote (which is
the natural surface for it — the admin list only returns
org-scope installs). The action band's `isOrgAdmin=false` shortcut
on workspace settings (from the install→auth-handoff spec) does
NOT generalize here.

## 5. Back-end contract

| Endpoint | New / repurposed | Authority |
| --- | --- | --- |
| `POST /admin/mcp/installs/{id}/refresh-discovery` | New | org admin |
| `POST /ws/{ws}/mcp/installs/{id}/refresh-discovery` | New | ws admin |
| `POST /admin/mcp/test-connection` | New | org admin |
| `POST /admin/mcp/installs` with `template_id=None` | Repurposed shape | org admin |
| `POST /admin/mcp/installs/{id}/promote-to-org` | New | org admin |
| `PUT /admin/mcp/installs/{id}/tool-citations` | New | org admin |
| `POST /admin/mcp/installs/{id}/tools/{tool}/invoke` | New | org admin, rate-limited |
| `POST /ws/{ws}/mcp/installs/{id}/tools/{tool}/invoke` | New | ws member, rate-limited |

All new endpoints return the standard error envelope (422 / 400
/ 404 / 409 with `{ "code": "..." }`).

## 6. Edge cases

1. **Refresh discovery while a previous discovery is in flight.**
   We use an advisory lock keyed on `install_id`. Second call
   returns 409 `discovery_in_progress`. Frontend disables the
   button while one is pending and re-enables on response.

2. **Test connection with credential_plaintext but auth_method='none'.**
   422 — body validation rejects, mirroring the existing
   credential_policy cross-field check.

3. **Custom install with a server_url that collides with an
   existing org-scope install.** The unique partial index
   (`org_id, server_url_hash`) catches it. 409
   `org_install_already_exists`.

4. **Promote where the workspace state row's `credential_policy`
   has been overridden to `user`.** Distribution honors the
   existing override — only the other workspaces' fan-out state
   rows use the install's `default_credential_policy`. The
   originating workspace's row is left untouched.

5. **Citation editor — tool removed from tools_cache after a
   discovery refresh.** The existing config for that tool name
   in `tool_citations` is preserved (not auto-pruned) so a
   subsequent re-add of the tool keeps the user's mapping. UI
   shows a "Tool currently missing" note next to such configs.

6. **Try It while the grant is expired.** Backend's existing
   `OAuthTokenManager.get_access_token` rotates first; if
   refresh fails, the invoke returns
   `error='credential_expired'` and the action band's
   `grant_expired` state surfaces on the next list refresh.

7. **Discovery race + tools_cache.** If two refresh calls race
   and the advisory lock is bypassed (e.g. infra glitch), the
   newer write wins. tools_cache is a JSON list, not append-only;
   the older result loses cleanly.

## 7. What this spec is NOT

- Not a redesign of the four-layer data model. All seven features
  are read-only consumers or single-row writes to existing
  columns.
- Not a server "hero header" redesign (Tier 3.8 — out of scope
  per user direction).
- Not a re-add of the legacy server / catalog / override stack.
  Anywhere this spec uses the word "server" or "MCP server" it
  means the live remote MCP server addressable by `install.server_url`,
  not the deleted `mcp_servers` table.
- Not a multi-tool invocation playground. Try It is one tool at
  a time, no chaining.

## 8. Future work (deferred)

- Visual hero header (`ServerHero` equivalent) with template logo
  + docs link.
- Tool chaining playground.
- Org-level citation defaults that workspaces inherit
  (currently per-install only).
- Bulk re-discovery across all installs (cron-style admin tool).
