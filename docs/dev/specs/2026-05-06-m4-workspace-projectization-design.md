# M4 · Workspace Projectization Design

**Date:** 2026-05-06  
**Milestone:** M4 (Batch 2, W2–W3)  
**Status:** Draft

## Overview

Turn each cubeplex Workspace into a self-contained, configured agent context. Every workspace carries its own system prompt, skill set, and MCP connector set. All conversations in a workspace share that configuration automatically — no per-conversation setup required.

Knowledge base is explicitly out of scope for M4 (depends on M7 knowledge-base milestone).

---

## Product Model

A Workspace **is** the agent config. There is no separate "agent" object. When a user enters a workspace and starts a conversation, the agent behavior is fully determined by that workspace's settings.

- **Persona**: optional system prompt appended after the global `BASE_SYSTEM_PROMPT`. Defines persona, constraints, or domain focus for every conversation in this workspace. Grouped under "Workspace Settings" in the UI alongside future per-workspace settings (model selection, etc.).
- **Skills**: per-workspace set of skills. Members can enable/disable org-installed skills and create workspace-private skills that exist only within this workspace.
- **MCP Connectors**: per-workspace set of MCP servers. Members can toggle org-wide servers and create workspace-private servers that are not shared with the org.

---

## UI Design

### Entry Point

A settings button in the conversation sidebar footer (col 1). Clicking it switches the sidebar into "settings mode" without navigating away from the current page.

### 3-Column Layout (Settings Mode)

| Column | Width | Content |
|--------|-------|---------|
| Col 1 | ~200px | Conversation sidebar, with settings nav appended below the conversation list. Three top-level items: **Workspace Settings** (group), **Skills**, **MCP Connectors**. No back button — closing settings is done by clicking the settings icon again or navigating to a conversation. |
| Col 2 | ~220px | List panel for the selected top-level item. For "Workspace Settings": sub-items list (Persona, Model, …). For Skills and MCP: scrollable item list with inline toggles and a "+ 新建" button. |
| Col 3 | flex:1 | Detail / edit panel for the selected item. |

### Workspace Settings Group

"Workspace Settings" is a top-level nav group in col 1. Selecting it shows a sub-item list in col 2:

- **Persona** — col 3 shows a resizable textarea editor pre-filled with the saved persona prompt, with Reset and Save buttons.
- **Model** — placeholder for future per-workspace model selection (out of scope for M4; shown as a nav item with a "即将推出" badge).

### Skills Tab

Col 2 lists all skills available to this workspace in two sections: org-installed skills (with per-workspace enable/disable toggle) and workspace-private skills. A "+ 新建 Skill" button at the top allows members to create a workspace-private skill (upload a zip or define inline) without going through the org catalog. Selecting a row loads the skill detail in col 3: enable/disable toggle, description, version, source, tags.

### MCP Connectors Tab

Col 2 lists connectors in two sections — "组织共享" (org-wide servers with a binding toggle) and "Workspace 私有" (servers owned by this workspace). A "+ 新建" button allows members to add a workspace-private MCP server directly. Col 3 shows server detail: endpoint, transport type, credential binding UI, and enable/disable toggle.

---

## Backend Architecture

### Gap 1: Persona Not Wired to Runtime

**Current state:** `run_manager.py` calls `create_cubeplex_agent(system_prompt=BASE_SYSTEM_PROMPT)`. `AgentConfig.system_prompt` is never read.

**Fix:** In `run_manager.py`, before calling `create_cubeplex_agent`, load the workspace's `AgentConfig` and build the effective system prompt:

```
effective_prompt = BASE_SYSTEM_PROMPT
if agent_config.system_prompt:
    effective_prompt = BASE_SYSTEM_PROMPT + "\n\n" + agent_config.system_prompt
```

`AgentConfig` has a 1:1 relationship with `Workspace` (unique constraint on `workspace_id`). Going forward, workspace creation will auto-create an empty `AgentConfig` row. Existing workspaces without one are handled by the backfill migration described in Data Model Changes.

### Gap 2: No Settings CRUD API

New route: `GET/PUT /api/v1/ws/{workspace_id}/settings/agent`

Returns and accepts the workspace persona:

```json
{
  "system_prompt": "You are a research assistant..."
}
```

`skill_ids` and `mcp_server_ids` fields on `AgentConfig` are dead code — the real mechanism is the binding tables. These fields are not exposed or populated.

### Gap 3: Skill Binding Management Requires Org Admin

**Current state:** Enabling/disabling a skill in a workspace is an admin-only operation (`admin_skills.py`). Workspace members cannot manage their own workspace's skill set.

**Fix:** New workspace-scoped skill endpoints, accessible to workspace members (no org-admin required):

- `GET /api/v1/ws/{workspace_id}/settings/skills` — list org-installed skills (with per-workspace enabled state) and workspace-private skills
- `PATCH /api/v1/ws/{workspace_id}/settings/skills/{skill_id}` — set `enabled: true/false` for an org-installed skill in this workspace
- `POST /api/v1/ws/{workspace_id}/settings/skills` — create a workspace-private skill (not in the org catalog)
- `DELETE /api/v1/ws/{workspace_id}/settings/skills/{skill_id}` — delete a workspace-private skill

Org-installed skill enable/disable operates on `WorkspaceSkillBinding` rows. Workspace-private skills use `OrgSkillInstall` rows with a new nullable `workspace_id` FK column — when set, the install is private to that workspace (not visible org-wide). This requires a schema migration (see Data Model Changes). The org-admin routes in `admin_skills.py` remain unchanged.

Similarly for MCP:

- `GET /api/v1/ws/{workspace_id}/settings/mcp` — list org-wide servers (with binding state) + workspace-private servers
- `PATCH /api/v1/ws/{workspace_id}/settings/mcp/{server_id}` — toggle org-wide server binding for this workspace
- Workspace-private server CRUD (`POST/PUT/DELETE`) already exists in `ws_mcp.py` — no changes needed there

### Dead Code: `AgentConfig.skill_ids` / `mcp_server_ids`

These JSON fields on `AgentConfig` have no effect on runtime. The actual skill and MCP sets are determined by `WorkspaceSkillBinding` and `WorkspaceMCPBinding` respectively. M4 does not populate these fields, and no migration is needed — they remain in the schema but are ignored.

---

## Permission Model

All new settings endpoints use the existing workspace membership check (`require_workspace_member`). No new roles are introduced. Any workspace member can read and write the workspace agent config.

If finer-grained access control is needed (e.g., only workspace owners can change settings), that is a post-M4 concern.

---

## Data Model Changes

One schema migration is required for workspace-private skills:

- Add `workspace_id` nullable FK column to `org_skill_installs`. When NULL → org-wide install; when set → workspace-private install visible only in that workspace.
- Convert the `uq_org_skill_install` unique constraint on `(org_id, skill_id)` to a partial unique index covering only rows where `workspace_id IS NULL`. Add a separate unique index on `(org_id, workspace_id, skill_id)` for workspace-private rows.
- Data backfill: create an empty `AgentConfig` row for any existing workspace that lacks one.

All other tables (`workspace_skill_binding`, `workspace_mcp_binding`, `mcp_server`) already support the required operations unchanged.

---

## Frontend

### New Routes

`/w/[wsId]/settings` — the 3-column settings shell. Sub-routes or query params control which tab is active:
- `/w/[wsId]/settings?tab=workspace&sub=persona`
- `/w/[wsId]/settings?tab=skills`
- `/w/[wsId]/settings?tab=mcp`

The settings shell is a new page at `app/(app)/w/[wsId]/settings/page.tsx`. It renders inside the existing app layout (which includes the conversation sidebar). In settings mode, the sidebar renders the settings nav items in place of (or below) the conversation list.

### Core Package Changes

New API methods in `@cubeplex/core`:
- `getAgentConfig(wsId)` / `updateAgentConfig(wsId, patch)` — persona read/write
- `listWorkspaceSkills(wsId)` / `toggleWorkspaceSkill(wsId, skillId, enabled)` / `createWorkspaceSkill(wsId, payload)` / `deleteWorkspaceSkill(wsId, skillId)`
- `listWorkspaceMCP(wsId)` / `toggleWorkspaceMCP(wsId, serverId, enabled)`

New Zustand store: `useWorkspaceSettingsStore` — holds agent config + skill list + MCP list for the current workspace. Loaded on settings page mount.

---

## Out of Scope (M4)

- Knowledge base / file indexing (depends on M7)
- Per-model selection per workspace (model management is M2, UI wiring is post-M4)
- Skill publishing / marketplace (separate milestone)
- Fine-grained settings permissions (workspace owner vs member distinction)
- Workspace-private MCP server creation UI is in scope (the backend already supports it via `ws_mcp.py`; M4 adds the settings UI entry point)
