# M4 · Workspace Projectization Design

**Date:** 2026-05-06  
**Milestone:** M4 (Batch 2, W2–W3)  
**Status:** Draft

## Overview

Turn each cubebox Workspace into a self-contained, configured agent context. Every workspace carries its own system prompt, skill set, and MCP connector set. All conversations in a workspace share that configuration automatically — no per-conversation setup required.

Knowledge base is explicitly out of scope for M4 (depends on M7 knowledge-base milestone).

---

## Product Model

A Workspace **is** the agent config. There is no separate "agent" object. When a user enters a workspace and starts a conversation, the agent behavior is fully determined by that workspace's settings.

- **System Prompt**: optional text appended after the global `BASE_SYSTEM_PROMPT`. Defines persona, constraints, or domain focus for every conversation in this workspace.
- **Skills**: per-workspace subset of org-installed skills. Members can enable/disable individual skills.
- **MCP Connectors**: per-workspace subset of org-wide MCP servers, plus workspace-private servers. Members can toggle which servers are available to the agent.

---

## UI Design

### Entry Point

A settings button in the conversation sidebar footer (col 1). Clicking it switches the sidebar into "settings mode" without navigating away from the current page.

### 3-Column Layout (Settings Mode)

| Column | Width | Content |
|--------|-------|---------|
| Col 1 | ~200px | Conversation sidebar, with settings nav items appended below the conversation list: **System Prompt**, **Skills**, **MCP Connectors**. Footer shows "← 返回对话" button. |
| Col 2 | ~220px | List panel for the selected category. Skills and MCP each show a scrollable list with inline toggle switches. System Prompt has no list — col 2 is not rendered and col 3 expands to fill the remaining width. |
| Col 3 | flex:1 | Detail / edit panel. For Skills: skill detail with per-workspace enable toggle, description, metadata. For MCP: server detail with credential status. For System Prompt: textarea editor with Save / Reset. |

### System Prompt Tab

Col 3 shows a full-width editor: a resizable textarea pre-filled with the saved prompt, a character/token count hint, Reset and Save buttons. Col 2 is not used.

### Skills Tab

Col 2 lists all org-installed skills with a search box and a count badge ("3 / 5 已启用"). Each row shows icon, name, short description, and a toggle switch. Selecting a row loads the skill detail in col 3: workspace-specific enable/disable toggle, full description, version, source, tags, and how many other org workspaces have it enabled.

### MCP Connectors Tab

Col 2 lists connectors in two sections — "组织共享" (org-wide servers bound to this workspace) and "Workspace 私有" (servers owned by this workspace). Each row has icon, name, transport type/URL, credential status badge, and a toggle. Col 3 shows server detail: endpoint, auth type, credential binding UI (add/revoke per-workspace credential), and enable/disable toggle.

---

## Backend Architecture

### Gap 1: System Prompt Not Wired to Runtime

**Current state:** `run_manager.py` calls `create_cubebox_agent(system_prompt=BASE_SYSTEM_PROMPT)`. `AgentConfig.system_prompt` is never read.

**Fix:** In `run_manager.py`, before calling `create_cubebox_agent`, load the workspace's `AgentConfig` and build the effective system prompt:

```
effective_prompt = BASE_SYSTEM_PROMPT
if agent_config.system_prompt:
    effective_prompt = BASE_SYSTEM_PROMPT + "\n\n" + agent_config.system_prompt
```

`AgentConfig` has a 1:1 relationship with `Workspace` (unique constraint on `workspace_id`). Going forward, workspace creation will auto-create an empty `AgentConfig` row. Existing workspaces without one are handled by the backfill migration described in Data Model Changes.

### Gap 2: No Settings CRUD API

New route group: `GET/PUT /api/v1/ws/{workspace_id}/settings/agent`

Returns and accepts the full agent config for the workspace:

```json
{
  "system_prompt": "You are a research assistant...",
}
```

`skill_ids` and `mcp_server_ids` fields on `AgentConfig` are dead code — the real mechanism is the binding tables. These fields are not exposed or populated.

### Gap 3: Skill Binding Management Requires Org Admin

**Current state:** Enabling/disabling a skill in a workspace is an admin-only operation (`admin_skills.py`). Workspace members cannot manage their own workspace's skill set.

**Fix:** New workspace-scoped skill binding endpoints, accessible to workspace members (no org-admin required):

- `GET /api/v1/ws/{workspace_id}/settings/skills` — list all org-installed skills with their enabled state for this workspace
- `PATCH /api/v1/ws/{workspace_id}/settings/skills/{skill_id}` — set `enabled: true/false` for this workspace

These operate on `WorkspaceSkillBinding` rows. The org-admin routes in `admin_skills.py` remain unchanged for bulk/administrative operations.

Similarly for MCP:

- `GET /api/v1/ws/{workspace_id}/settings/mcp` — list org-wide servers (with binding state) + workspace-private servers
- `PATCH /api/v1/ws/{workspace_id}/settings/mcp/{server_id}` — toggle org-wide server binding for this workspace
- Workspace-private server CRUD already exists in `ws_mcp.py` — no changes needed there

### Dead Code: `AgentConfig.skill_ids` / `mcp_server_ids`

These JSON fields on `AgentConfig` have no effect on runtime. The actual skill and MCP sets are determined by `WorkspaceSkillBinding` and `WorkspaceMCPBinding` respectively. M4 does not populate these fields, and no migration is needed — they remain in the schema but are ignored.

---

## Permission Model

All new settings endpoints use the existing workspace membership check (`require_workspace_member`). No new roles are introduced. Any workspace member can read and write the workspace agent config.

If finer-grained access control is needed (e.g., only workspace owners can change settings), that is a post-M4 concern.

---

## Data Model Changes

No schema changes required. All necessary tables already exist:

- `agent_config` (system prompt storage)
- `workspace_skill_binding` (per-workspace skill enable/disable)
- `workspace_mcp_binding` (per-workspace org MCP server enable/disable)
- `mcp_server` with `owner_workspace_id` (workspace-private servers)

One Alembic data migration is required: backfill an empty `AgentConfig` row for any existing workspace that does not already have one.

---

## Frontend

### New Routes

`/w/[wsId]/settings` — the 3-column settings shell. Sub-routes or query params control which tab is active:
- `/w/[wsId]/settings?tab=system-prompt`
- `/w/[wsId]/settings?tab=skills`
- `/w/[wsId]/settings?tab=mcp`

The settings shell is a new page at `app/(app)/w/[wsId]/settings/page.tsx`. It renders inside the existing app layout (which includes the conversation sidebar). In settings mode, the sidebar renders the settings nav items in place of (or below) the conversation list.

### Core Package Changes

New API methods in `@cubebox/core`:
- `getAgentConfig(wsId)` / `updateAgentConfig(wsId, patch)`
- `listWorkspaceSkills(wsId)` / `toggleWorkspaceSkill(wsId, skillId, enabled)`
- `listWorkspaceMCP(wsId)` / `toggleWorkspaceMCP(wsId, serverId, enabled)`

New Zustand store: `useWorkspaceSettingsStore` — holds agent config + skill list + MCP list for the current workspace. Loaded on settings page mount.

---

## Out of Scope (M4)

- Knowledge base / file indexing (depends on M7)
- Per-model selection per workspace (model management is M2, UI wiring is post-M4)
- Skill publishing / marketplace (separate milestone)
- Fine-grained settings permissions (workspace owner vs member distinction)
- Workspace-private MCP server creation UI (already implemented in existing admin console)
