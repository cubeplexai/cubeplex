# MCP Admin vs Workspace Views — Separation Spec

**Status:** Draft for review
**Author:** xfgong
**Date:** 2026-05-17
**Scope:** Frontend UX rules and the backend data contract changes needed
to support them. Two MCP management pages (`/admin/mcp` and the workspace
MCP settings) currently share concepts they shouldn't, and don't share
concepts they should. This spec defines what each page is allowed to
talk about, then enumerates the read/write surface changes that flow
from that.

## 1. Problem

Today both pages talk to the same backend endpoints
(`wsListEffectiveConnectors`, `wsListTemplates`, `wsCreateInstall`,
`adminListInstalls`, …) and the frontend pages each cherry-pick what
they render. Two concrete leaks fall out:

1. **The admin page leaks workspace lens.** It picks
   `workspaces[0]?.id` as `lensWsId`, fetches the workspace effective
   list, and falls back to "synthesize a stub row" when an org install
   has no state row in that lens. The synthesized row's
   `workspace_state` field then drives a `wsEnabled` / `wsDisabled`
   badge in the detail panel — but an admin standing on the org admin
   page has no business seeing "this workspace says this install is
   disabled." That status is per-workspace, the admin's view is
   per-org.

2. **The workspace page mis-categorizes org installs.** Anything that
   comes back from `wsListEffectiveConnectors` is rendered under
   "Connector installs" today, even when the row exists only because
   the org admin fanned out a state row with `enabled=false`
   (`enablement_source='admin_auto'` or the row was later flipped
   off). From the workspace operator's point of view that's not an
   install — they never opted in. It should be in the "available to
   add" bucket alongside templates, with an action that creates /
   enables the state row.

Both leaks come from the same root cause: the two pages share a list
endpoint whose semantics ("things present in this workspace's lens")
fit neither audience exactly. We need to give each audience the list
that matches its question, and stop synthesizing one page's view from
the other's data.

## 2. Mental model

The two surfaces answer two different questions:

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │ Org admin (/admin/mcp): "What MCP connectors does this org have,    │
 │   and is each one's org-level state healthy?"                       │
 │                                                                     │
 │ Workspace admin (workspace MCP settings): "What MCP connectors can  │
 │   this workspace actually use, and what's left to enable here?"     │
 └──────────────────────────────────────────────────────────────────────┘
```

Mapped against the four-layer model:

| Underlying row | Admin page shows it as | Workspace page shows it as |
|---|---|---|
| Workspace-scope install (any workspace) | **Not shown.** Workspace-scope installs are private to the workspace that owns them; admins find them by visiting that workspace's settings page. | **Installed** (only on the owning workspace's page) |
| Org install + state row `enabled=true` | **Installed (org)** | **Installed** |
| Org install + state row `enabled=false` | **Installed (org)**, per-workspace count rolled into the Workspaces tab — no per-lens badge in the top row | **Available** ↓ |
| Org install + no state row | **Installed (org)**, ditto | **Available** — single bucket, single button |
| Template not installed at org level yet | **Available — install** (admin can org-install it) | **Available** ↑ |

**Workspace "Available" collapses three flavors into one row style.** The
operator doesn't need to know whether the connector is currently an
org install with a disabled state row, an org install with no state
row, or a template never installed at org level — those are
implementation details. All three render the same way, with one
"Connect" button that does the right thing per the row's hidden
`source` field (see §3.2). Picking a single verb avoids the
"is this an Install button or an Enable button?" UX problem
entirely.

Two collapses fall out of that table:

- The admin top row has no per-workspace status of any kind. Per-workspace
  detail belongs in the existing **Workspaces tab** (which already
  exists on org-scope installs) and nowhere else.
- The workspace page collapses "no state row" and "state row but
  disabled" into one **Available** bucket. The state-row distinction
  is an implementation detail the operator doesn't need.

## 3. Backend contract

Two new endpoints + one tweak. Names are illustrative; service layer
should land on whatever lines up with existing repo methods.

### 3.1 `GET /api/v1/admin/mcp/connectors` (new)

Replaces `adminListInstalls` from the admin page's perspective. Returns
one row per org install (workspace-scope installs are excluded — they
belong to whichever workspace owns them, the admin page won't list
them). Each row carries the **install + per-org effective state**, no
workspace lens:

```ts
type AdminOrgConnector = {
  install: MCPConnectorInstall    // org-scope only
  template: MCPConnectorTemplate | null
  org_effective: {
    usable: boolean
    reason: AdminReason   // 'usable' | 'missing_org_grant' |
                          //   'pending_oauth' | 'grant_expired' |
                          //   'discovery_failed'
    credential_availability: 'available' | 'missing' | 'not_required'
  }
  workspace_distribution: {
    enabled_count: number     // how many ws have enabled=true
    disabled_count: number    // ws state row exists but enabled=false
    eligible_count: number    // total ws in org (so the UI can show "5/12")
    auto_enroll_new_workspaces: boolean
  }
}
```

The `org_effective` block reuses the logic already in
`_derive_admin_org_effective`. The `workspace_distribution` block is a
lightweight aggregate computed from
`MCPWorkspaceConnectorState`; the UI uses it as a hint on the row, and
the existing Workspaces tab still pulls the per-row detail on demand.

### 3.2 `GET /api/v1/ws/{ws}/mcp/available` (new)

Returns connectors that this workspace can install or enable but
hasn't yet. One row per org install with `enabled != true` for this
workspace, plus one row per template not installed at the org level
(workspace can install as workspace-scope):

```ts
type WsAvailable = {
  source: 'org_install' | 'template'
  install: MCPConnectorInstall | null    // present iff source='org_install'
  template: MCPConnectorTemplate         // always present
  reason: 'no_state_row' | 'state_disabled' | 'not_installed_at_org'
}
```

The workspace UI renders all three reasons identically — same row
style, same button text ("Connect"). The frontend picks the right
write API per the row's `source`:

- `source='org_install'` → `PATCH /ws/{ws}/mcp/connectors/{install_id}/state` with `enabled=true` (upsert the state row).
- `source='template'` → existing `POST /ws/{ws}/mcp/installs` workspace-scope install.

The workspace operator never sees this distinction; both produce the
same post-Connect outcome (row moves to "Installed", auth band guides
them through credential provisioning if needed).

### 3.3 `GET /api/v1/ws/{ws}/mcp/connectors` (tightened)

This endpoint stays, but its semantics tighten: returns only rows
that this workspace **has opted into** — workspace-scope installs
this workspace owns, plus org installs with `state_row.enabled=true`.
The current behavior (returning org installs with disabled state
rows) goes to `/available` instead.

In `compute_effective_state` that means rule 4 changes from
"workspace_state_present and workspace_enabled" gates `usable` to
"workspace_state_present and workspace_enabled" gates *visibility on
this endpoint entirely* — disabled rows just don't come back.

## 4. Frontend rules

### 4.1 Admin page (`/admin/mcp`)

- Single fetch: `GET /admin/mcp/connectors`. No `wsListEffectiveConnectors`,
  no `lensWsId`, no `synthesizeStubEffective`.
- List columns: name, scope (always "org" — the column may turn into
  a Provider badge), auth method, **org grant status**, discovery
  status, "N / M workspaces enabled" (from `workspace_distribution`),
  Auto-enroll toggle indicator.
- Detail panel:
  - **Removes** the `wsEnabled` / `wsDisabled` row from the overview
    `dl`. Removes the `MCPWorkspaceConnectorState` lens entirely.
  - **Keeps** the existing **Workspaces tab** for per-workspace
    detail. The tab is the only place per-ws state appears on the
    admin page.
  - Tools / Try It surface stays, but the lens picker (used today
    when the install's effective policy is workspace/user) becomes
    explicit — admin picks a workspace to run "Try It" as, instead
    of inheriting the page's `lensWsId`.
- Templates section: shows templates not installed at org level — call
  `GET /admin/mcp/templates` (already exists). Install button creates
  an org-scope install (current behavior, already correct).

### 4.2 Workspace page (`workspace-settings/McpPanel.tsx`)

Two sections, both flat lists:

- **Installed** — fed by `/ws/{ws}/mcp/connectors` (tightened). Shows
  exactly what this workspace opted into and can run.
- **Available** — fed by `/ws/{ws}/mcp/available`. All rows render
  identically with a single **Connect** button. The frontend routes
  the click per the row's `source` field (PATCH state for org
  installs, POST install for templates) — the operator never sees
  that distinction.

The current divider between "Installs" and "Templates" goes away;
"Available" is the single bucket of "things you could turn on."

Workspace settings has no concept of org distribution, promote-to-org,
or auto-enroll. Those stay admin-only.

### 4.3 Shared subcomponents

Per the **Scope-isolated pages** rule (AGENTS.md), pages don't share
files; modules do. Today two modules cross the scope boundary with
`mode`-style props and need to split — the rest are already clean
and keep their current shape.

**Refactor — split per scope:**

- **`<ToolsPanel>` + `<TryItView>`** take `surface: 'admin' | 'ws'` and
  branch on it to pick `adminInvokeTool` vs `wsInvokeTool`, plus
  conditionally render an admin-only workspace lens picker. Split:
  - Extract a `<TryItForm>` that takes `onRun: (args) => Promise<Result>` as
    a callback. Form rendering / arg coercion / error display stay
    inside.
  - `<AdminTryItView>` composes `<TryItForm>` + admin workspace picker
    + lens precedence (`requiresWorkspacePicker` / `adminAuthMethod`
    logic) and passes `adminInvokeTool` as `onRun`.
  - `<WsTryItView>` composes `<TryItForm>` only, passes `wsInvokeTool`
    as `onRun`. No picker, no lens math.
  - `<ToolsPanel>` likewise becomes two thin variants — list +
    `<AdminTryItView>` or list + `<WsTryItView>`.

- **`<AuthActionBand>`** takes `callerRole: 'admin' | 'member'` +
  `isOrgAdmin: boolean` and branches on `isOrgAdmin` for the disconnect
  menu and button copy. Split similarly:
  - `<AuthBandFrame>` renders the visual band states (ready /
    needs-action / awaiting-others / error) given a pre-computed
    `AuthBandState`. No role logic, no API calls.
  - `<AdminAuthBand>` and `<WsAuthBand>` each own their state
    derivation + write actions (`adminCreateOrgGrant` /
    `adminOrgGrantOAuthStart` vs `wsCreateMyGrant` / `wsCreateWorkspaceGrant`
    / `wsMyGrantOAuthStart` / `wsWorkspaceGrantOAuthStart`). Each
    composes `<AuthBandFrame>` with its own button labels.
  - `effectiveAuthState.ts` stays — it's a pure function, taking role
    as input is fine (rule applies to routes and pages, not pure
    functions one layer down).

**Stay shared as-is** (no scope-specific branching):

- `<ServerErrorBanner>` — pure presentation.
- `<MCPCitationsTab>` / `<MCPCitationEditor>` — operate on
  install data; same call sites on both pages.
- `<MasterDetailList>`, `<JsonView>`, `<SchemaView>` — pure UI.

**Admin-only modules** (workspace pages don't import these):

- `<MCPTemplateInstallPanel>`, `<MCPCustomCreatePanel>` — install
  creation flows.
- `<MCPWorkspacesTab>` — per-workspace distribution view.
- `<AuthMethodSwitcher>` (currently inside `MCPAdminDetailPanel.tsx`)
  — only admins switch auth method.
- `<MCPPromoteDialog>` — promote-to-org.

The workspace install path (`POST /ws/{ws}/mcp/installs`) is invoked
inline from the workspace page's "Available" list — no separate
panel.

## 5. Migration / out-of-scope

- Existing org installs with disabled state rows just move buckets in
  the workspace UI (no data migration). The tightened
  `/ws/{ws}/mcp/connectors` filter takes effect immediately on
  deploy.
- Existing workspace-scope installs are unaffected.
- Backend `compute_effective_state` itself doesn't change — only the
  filtering in `list_for_workspace_user` changes. The pure function
  is still the contract for the runtime path
  (`runtime_specs_for_workspace`).
- Out of scope: agent runtime (`runtime_specs_for_workspace` should
  keep returning workspace-enabled installs only, which it already
  does); promote-to-org dialog (already admin-only); MCP discovery /
  citation editor / Try It logic.

## 6. Open questions

1. **"Enable for this workspace" on a workspace-overridden policy
   install**: when the org install has `default_credential_policy='user'`
   and the workspace previously overrode it to `'workspace'`, does the
   re-enable preserve the override or reset to default? Default
   answer: preserve (the override row stays, just flip `enabled`).
2. ~~Workspace-scope install discoverability from admin~~ — **resolved:
   no.** Workspace-scope installs are workspace property; admins find
   them via the workspace settings page. If a future audit need
   surfaces, a separate `/admin/mcp/workspace-installs` read-only view
   can be added without revisiting this spec.
3. **Naming**: "Installed" vs "Active" vs "Enabled" on the workspace
   page. Spec uses "Installed" throughout for the top section and
   "Connect" as the verb for Available rows. Final copy may shift
   ("Connected" / "Active" / etc.) once Tools tab + auth band states
   are wired together; revisit during implementation if a clearer
   word jumps out.
