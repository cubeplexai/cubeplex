# MCP Admin Overhaul Design

Date: 2026-05-12
Status: Approved
Branch: feat/mcp-optimize

## Problem

The MCP management UI has several product-logic and interaction-design issues:

1. **Credential and install are coupled** — org admin authenticates a catalog connector (e.g. Notion via OAuth) and it immediately becomes visible to all workspaces. There is no separate "distribute" step, and no per-workspace credential control.
2. **Three disconnected management surfaces** — `/admin/mcp` (catalog grid + advanced collapsible), `/w/[wsId]/integrations/mcp` (workspace catalog + servers), and workspace settings MCP tab. Overlapping data, different layouts, no clear navigation hierarchy.
3. **MCP and Skills UI patterns diverge** — Skills uses master-detail layout with sidebar list, toolbar with search/filter, and detail panel with tabs. MCP uses catalog grid + drawer + separate detail pages. Inconsistent mental model for admins managing both.
4. **Numerous interaction bugs** — success message fires on drawer close without install, `window.confirm()` for delete, no loading states on credential save, stale test results, raw IDs shown to users, inconsistent empty states.

## Design Decisions

### Credential-Install Separation

| Decision | Choice |
|----------|--------|
| Install model | Register + Per-workspace credential |
| Credential flow | Both paths: admin push shared credential, workspace self-serve |
| Shared credential behavior | Shared by default, workspace can override with own credential |
| Override semantics | Overriding detaches from shared; admin rotation no longer affects that workspace |

### UI Architecture

| Decision | Choice |
|----------|--------|
| Layout pattern | Unified master-detail (matching Skills) with richer sidebar cards |
| Management surfaces | Two: Admin page + enhanced Workspace Settings tab. Kill `/integrations/mcp` route. |
| Admin detail tabs | Overview (with org credential status) + Tools + Workspaces (enable/disable only) |
| Add connector flow | Inline in detail panel (no page navigation). Catalog: auth tabs in panel. Custom: form in panel. |

## Architecture

### Core Model: Register → Authenticate → Distribute

```
┌─────────────────────────────────────────────────────────┐
│  Admin registers connector at org level                  │
│  (catalog install or custom server)                      │
│  + completes org-level authentication                    │
│                                                          │
│  State: MCPServer row exists, authed=true/false          │
│  Visibility: NO workspace can see it yet                 │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│  Admin distributes to workspaces via Workspaces tab      │
│  (explicit enable per workspace)                         │
│                                                          │
│  Each enabled workspace:                                 │
│  - Gets WorkspaceMCPOverride(enabled=True) row           │
│  - Uses org credential by default (no credential row)    │
│  - Can override with own credential later                │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│  Workspace member sees connector in Settings MCP tab     │
│  - If using org credential: shows "Using org credential" │
│  - If user-scope: member self-serves OAuth/API key       │
│  - Workspace admin can override org credential with own  │
└─────────────────────────────────────────────────────────┘
```

### Override Logic Reversal

**Current**: org install → default visible to all workspaces. `WorkspaceMCPOverride(enabled=False)` explicitly disables.

**New**: org install → default NOT visible. `WorkspaceMCPOverride(enabled=True)` explicitly enables.

- No override row = not visible (currently = visible)
- `enabled=True` row = enabled for this workspace
- Delete row = disable

`list_for_member` workspace_visible calculation inverts: currently "no disable row = visible", changes to "has enable row = visible".

### Credential Resolution Order

When the runtime resolves a credential for a workspace using an org connector:

1. Check `WorkspaceMCPCredential` row for this (workspace, server) — if exists, use it (workspace override)
2. Check `UserMCPCredential` row for this (user, server) — if exists, use it (user-scope self-serve)
3. Fall back to `MCPServer.credential_id` (org credential, shared reference)
4. If none found and auth_method != "none" → connector is in "needs setup" state for this workspace

### Credential Source States

Each workspace's relationship to a connector's credential can be:

| State | Meaning | How determined |
|-------|---------|---------------|
| `org` | Using shared org credential | No WorkspaceMCPCredential row, org credential exists |
| `own` | Workspace overrode with own credential | WorkspaceMCPCredential row exists |
| `needs_setup` | Enabled but no usable credential | No credential at any level, auth_method != "none" |
| n/a | auth_method = "none", no credential needed | — |

## Frontend

### Surface 1: Admin MCP Page (`/admin/mcp`)

Full master-detail layout matching Skills page structure.

#### Page Layout

```
┌─────────────────────────────────────────────────────────┐
│  Header: "MCP Connectors" + subtitle                     │
├─────────────────────────────────────────────────────────┤
│  Toolbar: [Search] [All|Installed|Available|Custom] [+Add]│
├────────────────┬────────────────────────────────────────┤
│  Sidebar        │  Detail Panel                          │
│  (360px)        │                                        │
│                 │  [Header: name + badges + actions]     │
│  MCPConnector   │                                        │
│  Card           │  [Overview] [Tools (12)] [Workspaces]  │
│  Card (active)  │  ─────────────────────────────────     │
│  Card           │                                        │
│  Card           │  Tab content                           │
│  ...            │                                        │
│                 │                                        │
│  + Add custom   │                                        │
└────────────────┴────────────────────────────────────────┘
```

#### Sidebar Cards

Rich cards matching Skills SkillCard pattern:

- Connector icon (from metadata, fallback globe)
- Name (bold) + provider (secondary text)
- Status chip: "Installed" (green) / "Available" (muted) / "Auth expired" (amber)
- Transport badge + workspace count badge (for installed connectors)
- Active state: primary border + subtle background (matching SkillCard)

#### Toolbar

Matching Skills SkillsToolbar:

- Search input with icon
- Filter pills: All / Installed / Available / Custom
- "+ Add Custom" button (right-aligned)

#### Detail Panel States

**No selection**: centered muted text "Select a connector" (matching Skills).

**Uninstalled catalog connector selected**: registration/install form inline:
- Connector description + docs link
- Auth method tabs (OAuth / Static / None) based on `supported_auth_methods`
- Submit installs at org level, panel transitions to normal detail view

**Installed connector selected**: normal detail view with 3 tabs.

**"+ Add Custom" clicked**: custom server registration form inline:
- Name, URL, transport, auth method/scope, credential input, Test Connection button
- Submit creates server, sidebar refreshes, new server auto-selected

#### Overview Tab

- Info rows: URL, transport, auth method, description (read-only)
- Org Credential card: auth status (authenticated / not authed / expired), auth method, action button (Re-authorize / Provide API key / Rotate)

#### Tools Tab

- Accordion list (unchanged from current MCPToolsTable)
- Count in tab label

#### Workspaces Tab

- Table: Workspace name + Enabled toggle + Credential source label
- Credential source is display-only: "Using org credential" / "Own credential" / empty
- Enable/disable with inline confirm (matching Skills WorkspaceBindingsTable pattern)
- No credential management actions — those happen in workspace settings

### Surface 2: Workspace Settings MCP Tab (`/w/[wsId]/settings`)

Enhanced version of current McpPanel. Same master-detail layout.

#### Sidebar

Two groups with section headers:

**ORG CONNECTORS** — connectors distributed to this workspace by admin:
- Card: icon + name + enabled status + credential state ("Active" / "Needs credential" / "Using org credential")
- Toggle switch on card for enable/disable (workspace admin only)

**WORKSPACE PRIVATE** — connectors registered by this workspace:
- Card: icon + name + status
- "+ Add connector" button (workspace admin only, rendered as dashed border card)

#### Detail Panel for Org Connector

- Read-only info: name, URL, transport, tool count, description
- Enable/disable toggle (workspace admin)
- Credential section:
  - Using org credential: shows label + "Override with own credential" button
  - Needs self-serve: OAuth authorize button or API key input form
  - Has own credential: shows status + "Clear" button (reverts to org credential)
- No Workspaces tab (that's the admin perspective)

#### Detail Panel for Workspace-Private Connector

- Editable fields: name, URL, credential (workspace admin)
- Tabs: Overview + Tools (no Workspaces tab)
- Actions: Refresh Tools, Delete (inline confirm), Promote to Org (visible only if user is org admin)

#### Permission Matrix

| Action | Member | Workspace Admin | Org Admin |
|--------|--------|-----------------|-----------|
| View connectors | Yes | Yes | Yes |
| Self-serve user credential | Yes | Yes | Yes |
| Enable/disable org connector | No | Yes | Yes |
| Override org credential | No | Yes | Yes |
| Register private connector | No | Yes | Yes |
| Delete private connector | No | Yes | Yes |
| Promote to org | No | No | Yes |

## Backend Changes

### Override Logic Reversal

`MCPCatalogService.list_for_member`:

```python
# BEFORE: visible = no disable row
workspace_visible = org_install.id not in disabled_server_ids

# AFTER: visible = has enable row
enabled_server_ids = {row.mcp_server_id for row in ws_overrides if row.enabled is True}
workspace_visible = org_install is not None and org_install.id in enabled_server_ids
```

### install_for_org No Longer Auto-Distributes

After `install_for_org` completes, server row exists and may be authed, but zero `WorkspaceMCPOverride` rows exist → invisible to all workspaces. Admin must use `PUT /admin/mcp/servers/{id}/overrides` to enable workspaces.

### Credential Source in Workspace MCP List

The workspace settings MCP list API needs to return a `credential_source` field per connector:

```python
# For each org connector visible to this workspace:
ws_cred = await ws_cred_repo.get(workspace_id, server.id)
if ws_cred is not None:
    credential_source = "own"
elif server.credential_id is not None:
    credential_source = "org"
elif server.auth_method == "none":
    credential_source = None
else:
    credential_source = "needs_setup"
```

### Data Migration

No schema migration needed — `WorkspaceMCPOverride` table structure is unchanged. Only logic inversion.

If production data exists with the old semantics (no row = visible), a data migration flips existing state: for each org-wide server, create `WorkspaceMCPOverride(enabled=True)` for every workspace that does NOT have a `enabled=False` row. Then delete all `enabled=False` rows (they become the new "no row = not visible" default).

Current stage: no production data, so just change the logic.

### API Endpoint Changes

| Endpoint | Change |
|----------|--------|
| `PUT /admin/mcp/servers/{id}/overrides` | Semantics unchanged, but default flips |
| `GET /api/v1/ws/{wsId}/mcp/catalog` | `workspace_visible` calculation inverts |
| `GET /api/v1/ws/{wsId}/settings/mcp` | Add `credential_source` field to response |
| Workspace credential PUT/DELETE | Unchanged, already supports override |

No new tables or columns required.

## UI Fixes (Bundled)

All interaction issues from the audit, resolved by the new architecture or explicit fixes:

| Issue | Resolution |
|-------|-----------|
| Success message fires on drawer close | Drawer gone. Install success reflected by sidebar state change. |
| `window.confirm()` for delete | Inline confirm pattern (matching Skills OrgInstallActions) |
| Detail page loading/error = plain text | Centered muted text in panel (matching Skills SkillDetailPanel) |
| No back navigation from detail pages | Not needed — master-detail sidebar always visible |
| No search/filter in catalog | Toolbar with search + filter pills |
| Credential Panel save/clear no loading | Add loading + disabled state |
| Override Grid shows raw workspace ID | Remove, show only workspace name |
| Inconsistent empty states | All use centered icon + title + description card |
| Test Connection result not cleared on form change | Clear on any form value change |
| Custom headers field not exposed | Add to custom server form (collapsible advanced section) |
| 3 disconnected surfaces | Consolidated to 2 |
| Workspace Lens selector confusion | Gone — admin page no longer workspace-scoped |
| OAuth "coming soon" on custom form but works in catalog | Remove "coming soon" label. Custom servers support same auth methods. |

## Files to Create/Modify

### Delete

- `frontend/packages/web/app/(app)/w/[wsId]/integrations/mcp/page.tsx`
- `frontend/packages/web/app/(app)/w/[wsId]/integrations/mcp/[id]/page.tsx`
- `frontend/packages/web/app/(app)/w/[wsId]/integrations/mcp/new/page.tsx`
- `frontend/packages/web/app/admin/mcp/new/page.tsx`
- `frontend/packages/web/app/admin/mcp/[id]/page.tsx`
- `frontend/packages/web/components/mcp/catalog/MCPCatalogGrid.tsx`
- `frontend/packages/web/components/mcp/catalog/MCPCatalogCard.tsx`
- `frontend/packages/web/components/mcp/catalog/MCPInstallDrawer.tsx`
- `frontend/packages/web/components/mcp/catalog/MCPStaticForm.tsx`
- `frontend/packages/web/components/mcp/catalog/StatusChip.tsx`
- `frontend/packages/web/components/mcp/catalog/index.ts`

### Major Rewrite

- `frontend/packages/web/app/admin/mcp/page.tsx` — catalog grid → master-detail
- `frontend/packages/web/components/workspace-settings/McpPanel.tsx` — enhanced with full detail panel
- `frontend/packages/web/components/mcp/MCPServerDetail.tsx` — adapt to inline panel
- `frontend/packages/web/components/mcp/MCPServerForm.tsx` — inline form, remove OAuth "coming soon"
- `frontend/packages/web/components/mcp/MCPOverrideGrid.tsx` — add credential source display, invert logic
- `frontend/packages/web/components/mcp/MCPCredentialPanel.tsx` — add override flow (org → own → clear)
- `frontend/packages/core/src/stores/mcpStore.ts` — remove catalog grid state, align with new flow
- `frontend/packages/core/src/stores/workspaceMcpStore.ts` — remove, merge needed parts into mcpStore
- `frontend/packages/core/src/types/mcp.ts` — add credential_source, update DTOs

### New Components

- `frontend/packages/web/components/mcp/MCPConnectorCard.tsx` — rich sidebar card
- `frontend/packages/web/components/mcp/MCPToolbar.tsx` — search + filter pills
- `frontend/packages/web/components/mcp/MCPConnectorList.tsx` — sidebar list with sections
- `frontend/packages/web/components/mcp/MCPInstallForm.tsx` — inline catalog install (auth tabs)

### Backend

- `backend/cubebox/services/mcp_catalog.py` — invert override logic in `list_for_member`
- `backend/cubebox/services/mcp.py` — add credential_source to workspace settings response
- `backend/cubebox/api/routes/v1/ws_mcp.py` — credential_source in list response
- `backend/cubebox/api/routes/v1/admin_mcp.py` — no behavior change, override semantics flip

### Tests

- Update E2E: `frontend/packages/web/__tests__/e2e/mcp/admin-mcp.spec.ts`
- Update E2E: `frontend/packages/web/__tests__/e2e/mcp/ws-mcp.spec.ts`
- Backend E2E for override logic inversion
