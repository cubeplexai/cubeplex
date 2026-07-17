# MCP Admin Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Overhaul MCP management UI to unify with Skills patterns (master-detail layout), separate credential from install (Register → Authenticate → Distribute), and fix all interaction bugs.

**Architecture:** Backend override logic inverts (no row = invisible, `enabled=True` row = visible). Frontend consolidates from 3 surfaces to 2 (admin page + workspace settings). Admin page rewrites from catalog-grid+drawer to master-detail with sidebar cards, toolbar, and tabbed detail panel. Workspace settings MCP tab enhances with credential source display and inline management.

**Tech Stack:** Next.js 16, React 19, TypeScript, Tailwind CSS 4, shadcn/ui, Zustand, FastAPI, SQLAlchemy/SQLModel

**Worktree:** `/home/chris/cubeplex/.worktrees/feat/mcp-optimize` (branch `feat/mcp-optimize`, ports 8026/3026)

---

## File Structure

### Files to Delete

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
- `frontend/packages/web/components/mcp/MCPServerList.tsx`
- `frontend/packages/core/src/stores/workspaceMcpStore.ts`

### Files to Create

| File | Responsibility |
|------|---------------|
| `frontend/packages/web/components/mcp/MCPConnectorCard.tsx` | Rich sidebar card matching SkillCard pattern |
| `frontend/packages/web/components/mcp/MCPConnectorList.tsx` | Sidebar list with section headers (Installed/Available/Custom) |
| `frontend/packages/web/components/mcp/MCPToolbar.tsx` | Search + filter pills + Add Custom button |
| `frontend/packages/web/components/mcp/MCPInstallForm.tsx` | Inline catalog install form (auth tabs) for detail panel |
| `frontend/packages/web/components/mcp/MCPCustomServerForm.tsx` | Inline custom server form for detail panel |
| `frontend/packages/web/components/mcp/MCPAdminDetailPanel.tsx` | Admin detail panel with Overview/Tools/Workspaces tabs |
| `frontend/packages/web/components/mcp/MCPWorkspacesTab.tsx` | Workspaces tab: enable toggle + credential source label |

### Files to Rewrite

| File | What changes |
|------|-------------|
| `frontend/packages/web/app/admin/mcp/page.tsx` | Catalog grid → master-detail layout matching Skills |
| `frontend/packages/web/components/workspace-settings/McpPanel.tsx` | Enhanced with credential source, inline override flow |
| `frontend/packages/web/components/mcp/MCPOverrideGrid.tsx` | Rename to MCPWorkspacesTab, invert logic, add credential source |
| `frontend/packages/web/components/mcp/MCPCredentialPanel.tsx` | Add override flow (org→own→clear) |
| `frontend/packages/web/components/mcp/MCPServerDetail.tsx` | Adapt as admin inline panel (no separate page) |
| `frontend/packages/web/components/mcp/MCPServerForm.tsx` | Remove OAuth "coming soon", enable all auth methods |
| `frontend/packages/core/src/types/mcp.ts` | Add `credential_source` field, `MCPAdminConnector` union type |
| `frontend/packages/core/src/stores/mcpStore.ts` | Rewrite: unified admin store for master-detail |
| `frontend/packages/core/src/types/workspace-settings.ts` | Add `credential_source` to `MCPServerItem` |
| `backend/cubeplex/models/mcp.py` | Add `credential_mode` column to `WorkspaceMCPOverride` + update docstring |
| `backend/cubeplex/services/mcp_catalog.py` | Invert override logic in `list_for_member` |
| `backend/cubeplex/services/mcp.py` | Update `promote_to_org` for new override semantics |
| `backend/cubeplex/api/routes/v1/ws_settings.py` | Add `credential_mode` + `credential_source` to MCP list response, accept `credential_mode` in PATCH |
| `backend/cubeplex/api/routes/v1/ws_mcp.py` | Update `_get_workspace_visible_server` for new semantics |
| `backend/cubeplex/api/schemas/ws_settings.py` | Add `credential_mode`, `credential_source`, `credential_shared_by` to `MCPServerItem` |

---

## Task 1: Backend — Invert Override Logic + Add credential_mode Column

The core semantic change. After this, org installs are invisible by default; `WorkspaceMCPOverride(enabled=True)` explicitly enables. Also adds the `credential_mode` column to `WorkspaceMCPOverride` for workspace-level credential mode control.

**Files:**
- Modify: `backend/cubeplex/models/mcp.py:123-139` (add column + update docstring)
- Modify: `backend/cubeplex/services/mcp_catalog.py:126-146`
- Modify: `backend/cubeplex/services/mcp.py:490-520`
- Modify: `backend/cubeplex/api/routes/v1/ws_settings.py:263-296`
- Modify: `backend/cubeplex/api/routes/v1/ws_mcp.py:51-71`
- Create: Alembic migration for `credential_mode` column
- Test: `backend/tests/e2e/test_mcp_override_inversion.py`

- [ ] **Step 1: Write E2E test for override inversion**

Create `backend/tests/e2e/test_mcp_override_inversion.py`:

```python
"""E2E: override logic inversion — org installs invisible by default."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_org_install_invisible_by_default(
    admin_client: AsyncClient,
    workspace_id: str,
    org_mcp_server_id: str,
):
    """An org-wide MCP server with no override row should NOT appear
    in the workspace settings MCP list."""
    resp = await admin_client.get(f"/api/v1/ws/{workspace_id}/settings/mcp")
    assert resp.status_code == 200
    data = resp.json()
    org_ids = [s["server_id"] for s in data["org_servers"]]
    assert org_mcp_server_id not in org_ids


@pytest.mark.asyncio
async def test_org_install_visible_after_enable(
    admin_client: AsyncClient,
    workspace_id: str,
    org_mcp_server_id: str,
):
    """After creating an enabled=True override, the server appears."""
    resp = await admin_client.put(
        f"/api/v1/admin/mcp/servers/{org_mcp_server_id}/overrides",
        json={"workspace_id": workspace_id, "enabled": True},
    )
    assert resp.status_code == 200

    resp = await admin_client.get(f"/api/v1/ws/{workspace_id}/settings/mcp")
    assert resp.status_code == 200
    data = resp.json()
    org_ids = [s["server_id"] for s in data["org_servers"]]
    assert org_mcp_server_id in org_ids
    match = next(s for s in data["org_servers"] if s["server_id"] == org_mcp_server_id)
    assert match["enabled"] is True


@pytest.mark.asyncio
async def test_org_install_hidden_after_disable(
    admin_client: AsyncClient,
    workspace_id: str,
    org_mcp_server_id: str,
):
    """enable=True → delete override row → server vanishes again."""
    await admin_client.put(
        f"/api/v1/admin/mcp/servers/{org_mcp_server_id}/overrides",
        json={"workspace_id": workspace_id, "enabled": True},
    )
    # Now disable by setting enabled=False (or deleting)
    await admin_client.put(
        f"/api/v1/admin/mcp/servers/{org_mcp_server_id}/overrides",
        json={"workspace_id": workspace_id, "enabled": False},
    )
    resp = await admin_client.get(f"/api/v1/ws/{workspace_id}/settings/mcp")
    data = resp.json()
    org_ids = [s["server_id"] for s in data["org_servers"]]
    assert org_mcp_server_id not in org_ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize/backend && uv run pytest tests/e2e/test_mcp_override_inversion.py -v`

Expected: FAIL — org install currently visible by default (old semantics).

- [ ] **Step 3: Invert `list_for_member` in mcp_catalog.py**

In `backend/cubeplex/services/mcp_catalog.py`, change lines 127-146:

```python
    # OLD: Workspace overrides (disable rows) for the active workspace.
    # ws_overrides = await self.override_repo.list_for_workspace(workspace_id)
    # disabled_server_ids = {row.mcp_server_id for row in ws_overrides if row.enabled is False}

    # NEW: Workspace overrides — enabled=True rows make an org install visible.
    ws_overrides = await self.override_repo.list_for_workspace(workspace_id)
    enabled_server_ids = {
        row.mcp_server_id for row in ws_overrides if row.enabled is True
    }
```

And the visibility check inside the loop (line ~144-146):

```python
    # OLD:
    # workspace_visible = False
    # if org_install is not None and org_install.authed:
    #     workspace_visible = org_install.id not in disabled_server_ids

    # NEW: visible only if explicitly enabled for this workspace
    workspace_visible = (
        org_install is not None and org_install.id in enabled_server_ids
    )
```

- [ ] **Step 4: Invert `set_workspace_override` in mcp.py**

In `backend/cubeplex/services/mcp.py`, method `set_workspace_override` (lines ~490-520):

```python
    async def set_workspace_override(
        self,
        *,
        server_id: str,
        workspace_id: str,
        enabled: bool,
    ) -> None:
        """Enable or disable an org-wide install for a single workspace.

        New semantics: no override row = not visible. ``enabled=True`` makes
        the connector visible to this workspace. ``enabled=False`` (or deleting
        the row) hides it.
        """
        server = await self.server_repo.get(server_id)
        if server is None:
            raise MCPServerNotFound(server_id)
        if server.owner_workspace_id is not None:
            raise MCPWorkspaceOwnedNoOverride()
        if not enabled:
            # Disabling = delete the override row (no row = invisible).
            await self.override_repo.delete(
                workspace_id=workspace_id,
                mcp_server_id=server_id,
            )
            return
        # Enabling = upsert an enabled=True row.
        await self.override_repo.upsert(
            workspace_id=workspace_id,
            mcp_server_id=server_id,
            enabled=True,
            updated_by_user_id=self._ctx.user.id,
        )
```

- [ ] **Step 5: Invert workspace settings MCP list endpoint**

In `backend/cubeplex/api/routes/v1/ws_settings.py`, the `list_workspace_mcp` function (lines 263-296):

```python
@router.get("/mcp", response_model=WorkspaceMCPOut)
async def list_workspace_mcp(
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WorkspaceMCPOut:
    server_repo = MCPServerRepository(session, org_id=ctx.org_id)

    org_rows = await server_repo.list_org_wide_with_workspace_override(ctx.workspace_id)
    org_servers = [
        MCPServerItem(
            server_id=srv.id,
            name=srv.name,
            server_url=srv.server_url,
            transport=srv.transport,
            # New semantics: visible only if override row exists and enabled=True.
            enabled=override is not None and override.enabled,
            scope="org",
        )
        for srv, override in org_rows
        if override is not None and override.enabled
    ]

    workspace_servers = [
        MCPServerItem(
            server_id=srv.id,
            name=srv.name,
            server_url=srv.server_url,
            transport=srv.transport,
            enabled=True,
            scope="workspace",
        )
        for srv in await server_repo.list_for_org(owner_workspace_id=ctx.workspace_id)
    ]

    return WorkspaceMCPOut(org_servers=org_servers, workspace_servers=workspace_servers)
```

- [ ] **Step 6: Invert `_get_workspace_visible_server` in ws_mcp.py**

In `backend/cubeplex/api/routes/v1/ws_mcp.py` (lines 51-71):

```python
async def _get_workspace_visible_server(
    *,
    svc: MCPServerService,
    server_id: str,
    workspace_id: str,
) -> MCPServer:
    server = await svc.server_repo.get(server_id)
    if server is None:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"})
    if server.owner_workspace_id == workspace_id:
        return server
    if server.owner_workspace_id is None:
        # New semantics: visible only if an enabled=True override row exists.
        override = await svc.override_repo.get_for_workspace_and_server(
            workspace_id=workspace_id,
            mcp_server_id=server_id,
        )
        if override is not None and override.enabled:
            return server
    raise HTTPException(403, detail={"code": "mcp_server_not_available_to_workspace"})
```

- [ ] **Step 7: Update `promote_to_org` for new semantics**

In `backend/cubeplex/services/mcp.py`, method `promote_to_org` (around line 322-333). After setting `owner_workspace_id = None`, CREATE an enabled override for the source workspace instead of deleting disable overrides:

```python
        server.owner_workspace_id = None
        await self.server_repo.update(server)

        # New semantics: org installs are invisible by default. Create an
        # enabled override for the source workspace so the promoter still
        # sees the connector immediately after promotion.
        await self.override_repo.upsert(
            workspace_id=original_workspace_id,
            mcp_server_id=server_id,
            enabled=True,
            updated_by_user_id=self._ctx.user.id,
        )

        return await self.server_repo.get(server.id) or server
```

- [ ] **Step 8: Update WorkspaceMCPOverride model — docstring + credential_mode column**

In `backend/cubeplex/models/mcp.py` (lines 123-139), update the model:

```python
class WorkspaceMCPOverride(CubeplexBase, OrgScopedMixin, table=True):
    """Workspace-level visibility and credential override for org-wide MCP installs.

    A row with ``enabled=True`` means this workspace can see and use the
    connector. No row means the connector is not visible to this workspace
    (default-invisible semantics).

    ``credential_mode`` controls how credentials resolve for this workspace:
    - ``org``: use the org-level shared credential (MCPServer.credential_id)
    - ``workspace``: one member provides a credential shared by all workspace members
    - ``user``: each member authenticates individually
    """

    _PREFIX: ClassVar[str] = "wmov"
    __tablename__ = "workspace_mcp_overrides"
    __table_args__ = (
        UniqueConstraint("workspace_id", "mcp_server_id", name="uq_ws_mcp_override"),
    )

    mcp_server_id: str = Field(foreign_key="mcp_servers.id", max_length=20, index=True)
    enabled: bool = Field(default=False)
    credential_mode: str = Field(default="org", max_length=16)
    updated_by_user_id: str = Field(foreign_key="users.id", max_length=20)
```

- [ ] **Step 8b: Generate Alembic migration for credential_mode column**

Run: `cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize/backend && alembic revision --autogenerate -m "add credential_mode to workspace_mcp_overrides"`

Then run: `cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize/backend && alembic upgrade head`

- [ ] **Step 9: Update toggle_mcp_binding in ws_settings.py to handle enabled + credential_mode**

In `backend/cubeplex/api/routes/v1/ws_settings.py`, the `toggle_mcp_binding` function (lines 299-326):

```python
@router.patch("/mcp/{server_id}")
async def toggle_mcp_binding(
    server_id: str,
    body: MCPBindingPatch,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    server_repo = MCPServerRepository(session, org_id=ctx.org_id)
    srv = await server_repo.get(server_id)
    if srv is None or srv.owner_workspace_id is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="org MCP server not found")

    override_repo = WorkspaceMCPOverrideRepository(session, org_id=ctx.org_id)

    if body.enabled is not None:
        if body.enabled:
            # New semantics: enabled=True row makes it visible.
            await override_repo.upsert(
                workspace_id=ctx.workspace_id,
                mcp_server_id=server_id,
                enabled=True,
                updated_by_user_id=ctx.user.id,
            )
        else:
            # Disabling = delete the row (no row = invisible).
            await override_repo.delete(
                workspace_id=ctx.workspace_id,
                mcp_server_id=server_id,
            )

    if body.credential_mode is not None:
        # Update credential_mode on the existing override row.
        override = await override_repo.get_for_workspace_and_server(
            workspace_id=ctx.workspace_id,
            mcp_server_id=server_id,
        )
        if override is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="connector must be enabled before setting credential mode",
            )
        override.credential_mode = body.credential_mode
        override.updated_by_user_id = ctx.user.id
        session.add(override)
        await session.commit()

    return {
        "server_id": server_id,
        "enabled": body.enabled,
        "credential_mode": body.credential_mode,
    }
```

- [ ] **Step 10: Run tests**

Run: `cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize/backend && uv run pytest tests/e2e/test_mcp_override_inversion.py -v`

Expected: PASS

- [ ] **Step 11: Run existing MCP test suite to check for regressions**

Run: `cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize/backend && uv run pytest tests/e2e/ -k mcp -v`

Fix any failures caused by the semantic inversion — tests that assumed "no override row = visible" need to create explicit `enabled=True` rows.

- [ ] **Step 12: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize
git add backend/cubeplex/services/mcp_catalog.py backend/cubeplex/services/mcp.py backend/cubeplex/api/routes/v1/ws_settings.py backend/cubeplex/api/routes/v1/ws_mcp.py backend/cubeplex/models/mcp.py backend/tests/e2e/test_mcp_override_inversion.py
git commit -m "$(cat <<'EOF'
feat(mcp): invert override logic — org installs invisible by default

Override semantics change: no WorkspaceMCPOverride row = not visible
(previously = visible). enabled=True row = visible. This implements
the Register → Authenticate → Distribute model where admin must
explicitly enable connectors per workspace.
EOF
)"
```

---

## Task 2: Backend — Add credential_mode + credential_source to Workspace Settings MCP List

The workspace settings MCP list response needs `credential_mode` and `credential_source` fields per connector so the frontend can display credential state and offer the right actions. The `toggle_mcp_binding` endpoint also needs to accept `credential_mode` changes.

**Files:**
- Modify: `backend/cubeplex/api/schemas/ws_settings.py`
- Modify: `backend/cubeplex/api/routes/v1/ws_settings.py:263-296`
- Modify: `frontend/packages/core/src/types/workspace-settings.ts`

- [ ] **Step 1: Find and read the ws_settings schema file**

Run: `find /home/chris/cubeplex/.worktrees/feat/mcp-optimize/backend -path "*/schemas/ws_settings.py" -type f`

Read the file to find the `MCPServerItem` Pydantic model.

- [ ] **Step 2: Add credential_mode and credential_source to schema**

In `backend/cubeplex/api/schemas/ws_settings.py`, add both fields to `MCPServerItem`:

```python
class MCPServerItem(BaseModel):
    server_id: str
    name: str
    server_url: str
    transport: str
    enabled: bool
    scope: str
    credential_mode: str = "org"  # "org" | "workspace" | "user"
    credential_source: str | None = None  # "org" | "workspace" | "user" | "needs_setup" | None
    credential_shared_by: str | None = None  # display name when mode=workspace and cred exists
```

Also add `credential_mode` to `MCPBindingPatch`:

```python
class MCPBindingPatch(BaseModel):
    enabled: bool | None = None
    credential_mode: str | None = None  # "org" | "workspace" | "user"
```

- [ ] **Step 3: Compute credential_mode + credential_source in list_workspace_mcp**

In `backend/cubeplex/api/routes/v1/ws_settings.py`, update `list_workspace_mcp` to compute both fields. Resolution is mode-driven:

```python
from cubeplex.repositories.mcp import (
    MCPServerRepository,
    UserMCPCredentialRepository,
    WorkspaceMCPCredentialRepository,
    WorkspaceMCPOverrideRepository,
)

@router.get("/mcp", response_model=WorkspaceMCPOut)
async def list_workspace_mcp(
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WorkspaceMCPOut:
    server_repo = MCPServerRepository(session, org_id=ctx.org_id)
    ws_cred_repo = WorkspaceMCPCredentialRepository(session, org_id=ctx.org_id)
    user_cred_repo = UserMCPCredentialRepository(session, org_id=ctx.org_id)

    org_rows = await server_repo.list_org_wide_with_workspace_override(ctx.workspace_id)
    org_servers: list[MCPServerItem] = []
    for srv, override in org_rows:
        if override is None or not override.enabled:
            continue

        mode = override.credential_mode or "org"
        credential_source: str | None = None
        credential_shared_by: str | None = None

        if srv.auth_method == "none":
            credential_source = None
        elif mode == "org":
            credential_source = "org" if srv.credential_id else "needs_setup"
        elif mode == "workspace":
            ws_cred = await ws_cred_repo.get(
                workspace_id=ctx.workspace_id,
                mcp_server_id=srv.id,
            )
            if ws_cred is not None:
                credential_source = "workspace"
                # Look up display name of who shared it
                shared_by_user = await session.get(User, ws_cred.created_by_user_id)
                credential_shared_by = shared_by_user.email if shared_by_user else None
            else:
                credential_source = "needs_setup"
        elif mode == "user":
            user_cred = await user_cred_repo.get(
                user_id=ctx.user.id,
                mcp_server_id=srv.id,
            )
            credential_source = "user" if user_cred else "needs_setup"

        org_servers.append(
            MCPServerItem(
                server_id=srv.id,
                name=srv.name,
                server_url=srv.server_url,
                transport=srv.transport,
                enabled=True,
                scope="org",
                credential_mode=mode,
                credential_source=credential_source,
                credential_shared_by=credential_shared_by,
            )
        )

    workspace_servers = [
        MCPServerItem(
            server_id=srv.id,
            name=srv.name,
            server_url=srv.server_url,
            transport=srv.transport,
            enabled=True,
            scope="workspace",
        )
        for srv in await server_repo.list_for_org(owner_workspace_id=ctx.workspace_id)
    ]

    return WorkspaceMCPOut(org_servers=org_servers, workspace_servers=workspace_servers)
```

- [ ] **Step 4: Update frontend type**

In `frontend/packages/core/src/types/workspace-settings.ts`, add `credential_mode`, `credential_source`, and `credential_shared_by`:

```typescript
export type MCPCredentialMode = 'org' | 'workspace' | 'user'
export type MCPCredentialSource = 'org' | 'workspace' | 'user' | 'needs_setup' | null

export interface MCPServerItem {
  server_id: string
  name: string
  server_url: string
  transport: string
  enabled: boolean
  scope: 'org' | 'workspace'
  credential_mode: MCPCredentialMode
  credential_source: MCPCredentialSource
  credential_shared_by: string | null
}
```

- [ ] **Step 5: Run backend lint and type check**

Run: `cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize/backend && make lint && make type-check`

- [ ] **Step 6: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize
git add backend/cubeplex/api/schemas/ws_settings.py backend/cubeplex/api/routes/v1/ws_settings.py frontend/packages/core/src/types/workspace-settings.ts
git commit -m "$(cat <<'EOF'
feat(mcp): add credential_mode + credential_source to workspace MCP list

Each org connector in the workspace settings MCP list now reports
credential_mode (org/workspace/user) and resolved credential_source.
Workspace admin can control how credentials work per connector:
org-shared, workspace-shared (with shared_by tracking), or per-user.
EOF
)"
```

---

## Task 3: Frontend Types — Unified Admin Connector Type

Create the TypeScript types that the new admin page needs: a union type that represents both catalog connectors and custom servers in a single sidebar list.

**Files:**
- Modify: `frontend/packages/core/src/types/mcp.ts`

- [ ] **Step 1: Add admin connector types**

In `frontend/packages/core/src/types/mcp.ts`, add after the existing types:

```typescript
// ---------------- Admin unified connector types ---------------- //

export type MCPConnectorFilter = 'all' | 'installed' | 'available' | 'custom'

export interface MCPAdminConnector {
  kind: 'catalog' | 'custom'
  id: string
  name: string
  provider: string
  description: string
  server_url: string
  transport: MCPTransport
  // Catalog-specific
  catalog_id?: string
  supported_auth_methods?: MCPAuthMethod[]
  static_form_fields?: MCPCatalogStaticFormField[] | null
  // Install state
  installed: boolean
  server?: MCPServer
  // Status display
  authed: boolean
  tool_count: number
  workspace_count: number
  last_error: string | null
}
```

- [ ] **Step 2: Run frontend type check**

Run: `cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize/frontend && pnpm type-check`

- [ ] **Step 3: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize
git add frontend/packages/core/src/types/mcp.ts
git commit -m "feat(mcp): add MCPAdminConnector union type for unified sidebar"
```

---

## Task 4: Frontend Store — Rewrite mcpStore for Admin Master-Detail

Rewrite the admin MCP store to support the unified master-detail pattern: fetch both catalog and custom servers, merge into `MCPAdminConnector[]`, track selection.

**Files:**
- Rewrite: `frontend/packages/core/src/stores/mcpStore.ts`

- [ ] **Step 1: Rewrite mcpStore.ts**

Replace `frontend/packages/core/src/stores/mcpStore.ts` entirely:

```typescript
import { create } from 'zustand'

import { type ApiClient } from '../api/client'
import * as api from '../api/mcp'
import type {
  MCPAdminConnector,
  MCPCatalogConnector,
  MCPCatalogInstallRequest,
  MCPCatalogInstallResult,
  MCPInstallSwitchAuthRequest,
  MCPOAuthStartResult,
  MCPOverrideUpdateBody,
  MCPServer,
  MCPServerCreateAdminBody,
  MCPServerPatchBody,
  MCPTestConnectionBody,
  MCPTestConnectionResult,
  WorkspaceOverride,
} from '../types/mcp'
import { type CatalogErrorEnvelope, toCatalogError } from './mcpShared'

function mergeConnectors(
  catalog: MCPCatalogConnector[],
  servers: MCPServer[],
  overrideCounts: Map<string, number>,
): MCPAdminConnector[] {
  const result: MCPAdminConnector[] = []
  const serverByCatalogId = new Map<string, MCPServer>()

  for (const srv of servers) {
    if (srv.owner_workspace_id !== null) continue
    const parts = srv.name.match(/^catalog:(.+)$/)
    if (parts) {
      const matched = catalog.find(
        (c) => c.org_install_id === srv.id,
      )
      if (matched) {
        serverByCatalogId.set(matched.id, srv)
        continue
      }
    }
    result.push({
      kind: 'custom',
      id: srv.id,
      name: srv.name,
      provider: '',
      description: '',
      server_url: srv.server_url,
      transport: srv.transport,
      installed: true,
      server: srv,
      authed: srv.authed,
      tool_count: srv.tools_cache?.length ?? 0,
      workspace_count: overrideCounts.get(srv.id) ?? 0,
      last_error: srv.last_error,
    })
  }

  for (const cat of catalog) {
    const srv = serverByCatalogId.get(cat.id) ??
      (cat.org_install_id
        ? servers.find((s) => s.id === cat.org_install_id)
        : undefined)
    result.push({
      kind: 'catalog',
      id: cat.org_install_id ?? cat.id,
      name: cat.name,
      provider: cat.provider,
      description: cat.description,
      server_url: cat.server_url,
      transport: cat.transport,
      catalog_id: cat.id,
      supported_auth_methods: cat.supported_auth_methods,
      static_form_fields: cat.static_form_fields,
      installed: cat.org_install_id !== null,
      server: srv ?? undefined,
      authed: srv?.authed ?? false,
      tool_count: srv?.tools_cache?.length ?? 0,
      workspace_count: overrideCounts.get(srv?.id ?? '') ?? 0,
      last_error: srv?.last_error ?? null,
    })
  }

  return result
}

interface CatalogListParams {
  q?: string
  provider?: string
}

export interface McpStore {
  connectors: MCPAdminConnector[]
  loading: boolean
  error: CatalogErrorEnvelope | null
  selectedId: string | null
  pendingOAuthInstallId: string | null
  overrideCounts: Map<string, number>

  fetchAll(client: ApiClient, wsId: string): Promise<void>
  setSelectedId(id: string | null): void

  createCustom(client: ApiClient, body: MCPServerCreateAdminBody): Promise<MCPServer>
  updateServer(client: ApiClient, id: string, body: MCPServerPatchBody): Promise<MCPServer>
  deleteServer(client: ApiClient, id: string, wsId: string): Promise<void>
  refreshTools(client: ApiClient, id: string): Promise<MCPServer>
  testConnection(client: ApiClient, body: MCPTestConnectionBody): Promise<MCPTestConnectionResult>

  fetchOverrides(client: ApiClient, id: string): Promise<WorkspaceOverride[]>
  saveOverride(
    client: ApiClient,
    id: string,
    body: MCPOverrideUpdateBody,
  ): Promise<WorkspaceOverride[]>

  installFromCatalog(
    client: ApiClient,
    wsId: string,
    catalogId: string,
    body: MCPCatalogInstallRequest,
  ): Promise<MCPCatalogInstallResult>
  patchInstall(
    client: ApiClient,
    wsId: string,
    installId: string,
    body: MCPInstallSwitchAuthRequest,
  ): Promise<MCPCatalogInstallResult>
  deleteInstall(client: ApiClient, wsId: string, installId: string): Promise<void>
  startOAuth(client: ApiClient, installId: string): Promise<MCPOAuthStartResult>
  clearPendingOAuth(): void
  reset(): void
}

export const useMcpStore = create<McpStore>((set, get) => ({
  connectors: [],
  loading: false,
  error: null,
  selectedId: null,
  pendingOAuthInstallId: null,
  overrideCounts: new Map(),

  async fetchAll(client, wsId) {
    set({ loading: true, error: null })
    try {
      const [servers, catalogItems] = await Promise.all([
        api.adminListServers(client),
        api.wsCatalogList(client, wsId).catch(() => [] as MCPCatalogConnector[]),
      ])

      const overrideCounts = new Map<string, number>()
      for (const srv of servers) {
        if (srv.owner_workspace_id !== null) continue
        try {
          const overrides = await api.adminGetOverrides(client, srv.id)
          overrideCounts.set(
            srv.id,
            overrides.filter((o) => o.enabled).length,
          )
        } catch {
          // ignore
        }
      }

      const connectors = mergeConnectors(catalogItems, servers, overrideCounts)
      set({ connectors, overrideCounts })
    } catch (err) {
      set({ error: toCatalogError(err) })
    } finally {
      set({ loading: false })
    }
  },

  setSelectedId(id) {
    set({ selectedId: id })
  },

  async createCustom(client, body) {
    const created = await api.adminCreateServer(client, body)
    const connector: MCPAdminConnector = {
      kind: 'custom',
      id: created.id,
      name: created.name,
      provider: '',
      description: '',
      server_url: created.server_url,
      transport: created.transport,
      installed: true,
      server: created,
      authed: created.authed,
      tool_count: created.tools_cache?.length ?? 0,
      workspace_count: 0,
      last_error: created.last_error,
    }
    set({
      connectors: [...get().connectors, connector],
      selectedId: created.id,
    })
    return created
  },

  async updateServer(client, id, body) {
    const updated = await api.adminPatchServer(client, id, body)
    set({
      connectors: get().connectors.map((c) =>
        c.id === id || c.server?.id === id
          ? { ...c, server: updated, name: updated.name, authed: updated.authed,
              tool_count: updated.tools_cache?.length ?? 0,
              last_error: updated.last_error }
          : c,
      ),
    })
    return updated
  },

  async deleteServer(client, id, wsId) {
    await api.adminDeleteServer(client, id)
    set({
      connectors: get().connectors.filter((c) => c.id !== id && c.server?.id !== id),
      selectedId: get().selectedId === id ? null : get().selectedId,
    })
  },

  async refreshTools(client, id) {
    const refreshed = await api.adminRefreshTools(client, id)
    set({
      connectors: get().connectors.map((c) =>
        c.id === id || c.server?.id === id
          ? { ...c, server: refreshed, authed: refreshed.authed,
              tool_count: refreshed.tools_cache?.length ?? 0,
              last_error: refreshed.last_error }
          : c,
      ),
    })
    return refreshed
  },

  testConnection(client, body) {
    return api.adminTestConnection(client, body)
  },

  fetchOverrides(client, id) {
    return api.adminGetOverrides(client, id)
  },

  saveOverride(client, id, body) {
    return api.adminPutOverride(client, id, body)
  },

  async installFromCatalog(client, wsId, catalogId, body) {
    set({ error: null })
    try {
      const result = await api.adminCatalogInstall(client, catalogId, body)
      if (result.requires_oauth) {
        set({ pendingOAuthInstallId: result.install_id })
      }
      await get().fetchAll(client, wsId)
      set({ selectedId: result.install_id })
      return result
    } catch (err) {
      set({ error: toCatalogError(err) })
      throw err
    }
  },

  async patchInstall(client, wsId, installId, body) {
    set({ error: null })
    try {
      const result = await api.adminCatalogPatchInstall(client, installId, body)
      if (result.requires_oauth) {
        set({ pendingOAuthInstallId: result.install_id })
      }
      await get().fetchAll(client, wsId)
      return result
    } catch (err) {
      set({ error: toCatalogError(err) })
      throw err
    }
  },

  async deleteInstall(client, wsId, installId) {
    set({ error: null })
    try {
      await api.adminCatalogDeleteInstall(client, installId)
      await get().fetchAll(client, wsId)
    } catch (err) {
      set({ error: toCatalogError(err) })
      throw err
    }
  },

  async startOAuth(client, installId) {
    set({ error: null })
    try {
      const result = await api.adminOAuthStart(client, installId)
      set({ pendingOAuthInstallId: installId })
      return result
    } catch (err) {
      set({ error: toCatalogError(err) })
      throw err
    }
  },

  clearPendingOAuth() {
    set({ pendingOAuthInstallId: null })
  },

  reset() {
    set({
      connectors: [],
      loading: false,
      error: null,
      selectedId: null,
      pendingOAuthInstallId: null,
      overrideCounts: new Map(),
    })
  },
}))
```

- [ ] **Step 2: Update core package exports**

Check `frontend/packages/core/src/index.ts` — ensure `MCPAdminConnector`, `MCPConnectorFilter`, `MCPCredentialSource` are exported from the types re-export.

- [ ] **Step 3: Run type check**

Run: `cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize/frontend && pnpm type-check`

- [ ] **Step 4: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize
git add frontend/packages/core/src/stores/mcpStore.ts frontend/packages/core/src/index.ts
git commit -m "feat(mcp): rewrite mcpStore for unified admin master-detail"
```

---

## Task 5: Frontend — MCPToolbar Component

Search + filter pills + Add Custom button, matching SkillsToolbar.

**Files:**
- Create: `frontend/packages/web/components/mcp/MCPToolbar.tsx`

- [ ] **Step 1: Create MCPToolbar.tsx**

```typescript
'use client'

import { Plus, Search } from 'lucide-react'
import { useTranslations } from 'next-intl'
import type { MCPConnectorFilter } from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

interface MCPToolbarProps {
  search: string
  onSearchChange: (value: string) => void
  filter: MCPConnectorFilter
  onFilterChange: (value: MCPConnectorFilter) => void
  onAddCustom: () => void
}

const FILTERS: { value: MCPConnectorFilter; labelKey: string }[] = [
  { value: 'all', labelKey: 'filterAll' },
  { value: 'installed', labelKey: 'filterInstalled' },
  { value: 'available', labelKey: 'filterAvailable' },
  { value: 'custom', labelKey: 'filterCustom' },
]

export function MCPToolbar({
  search,
  onSearchChange,
  filter,
  onFilterChange,
  onAddCustom,
}: MCPToolbarProps) {
  const t = useTranslations('mcpAdmin')

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border/70 px-4 py-3">
      <div className="relative min-w-[180px] flex-1">
        <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/70" />
        <Input
          type="search"
          placeholder={t('searchPlaceholder')}
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          className="pl-7"
          aria-label={t('searchAriaLabel')}
        />
      </div>

      <div
        role="group"
        aria-label={t('filterByStatus')}
        className="inline-flex items-center gap-0.5 rounded-lg border border-border bg-muted/30 p-0.5"
      >
        {FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            onClick={() => onFilterChange(f.value)}
            className={cn(
              'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
              f.value === filter
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {t(f.labelKey)}
          </button>
        ))}
      </div>

      <Button size="sm" onClick={onAddCustom} className="ml-auto">
        <Plus className="size-3.5" />
        {t('addCustom')}
      </Button>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize
git add frontend/packages/web/components/mcp/MCPToolbar.tsx
git commit -m "feat(mcp): add MCPToolbar component (search + filter pills)"
```

---

## Task 6: Frontend — MCPConnectorCard Component

Rich sidebar card matching SkillCard. Shows icon, name, provider, status chip, transport badge, workspace count.

**Files:**
- Create: `frontend/packages/web/components/mcp/MCPConnectorCard.tsx`

- [ ] **Step 1: Create MCPConnectorCard.tsx**

```typescript
'use client'

import { useTranslations } from 'next-intl'
import { CheckCircle2, AlertCircle, Globe, Server } from 'lucide-react'
import type { MCPAdminConnector } from '@cubeplex/core'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

interface MCPConnectorCardProps {
  connector: MCPAdminConnector
  active: boolean
  onClick: () => void
}

function StatusChip({ connector }: { connector: MCPAdminConnector }) {
  const t = useTranslations('mcpAdmin')
  if (!connector.installed) return null
  if (connector.last_error) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
        <AlertCircle className="size-3" />
        {t('statusError')}
      </span>
    )
  }
  if (connector.authed) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
        <CheckCircle2 className="size-3" />
        {t('statusInstalled')}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
      {t('statusNotAuthed')}
    </span>
  )
}

export function MCPConnectorCard({ connector, active, onClick }: MCPConnectorCardProps) {
  const t = useTranslations('mcpAdmin')
  const Icon = connector.kind === 'catalog' ? Globe : Server

  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={`mcp-connector-card-${connector.name}`}
      aria-current={active ? 'true' : undefined}
      className={cn(
        'group/mcp-card flex w-full flex-col gap-1.5 rounded-lg border p-3 text-left transition-all',
        active
          ? 'border-primary/40 bg-primary/5 shadow-sm'
          : 'border-border/70 bg-card/40 hover:border-border hover:bg-accent/40',
      )}
    >
      <div className="flex items-center gap-2">
        <Icon
          className={cn(
            'size-3.5 shrink-0',
            connector.kind === 'catalog' ? 'text-primary' : 'text-muted-foreground',
          )}
        />
        <span className="truncate text-sm font-semibold">{connector.name}</span>
        <StatusChip connector={connector} />
      </div>
      {connector.provider && (
        <p className="line-clamp-1 text-xs text-muted-foreground">{connector.provider}</p>
      )}
      {connector.description && (
        <p className="line-clamp-2 text-xs text-muted-foreground">{connector.description}</p>
      )}
      <div className="flex flex-wrap items-center gap-1 pt-0.5">
        <Badge variant="outline" className="px-1.5 text-[10px]">
          {connector.transport}
        </Badge>
        {connector.installed && connector.workspace_count > 0 && (
          <span className="text-[10px] text-muted-foreground/70">
            {t('workspaceCount', { count: connector.workspace_count })}
          </span>
        )}
        {!connector.installed && (
          <span className="text-[10px] text-muted-foreground/70">{t('available')}</span>
        )}
      </div>
    </button>
  )
}
```

- [ ] **Step 2: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize
git add frontend/packages/web/components/mcp/MCPConnectorCard.tsx
git commit -m "feat(mcp): add MCPConnectorCard sidebar component"
```

---

## Task 7: Frontend — MCPConnectorList Component

Sidebar list that filters, sorts, and renders MCPConnectorCard items.

**Files:**
- Create: `frontend/packages/web/components/mcp/MCPConnectorList.tsx`

- [ ] **Step 1: Create MCPConnectorList.tsx**

```typescript
'use client'

import { useMemo } from 'react'
import { useTranslations } from 'next-intl'
import type { MCPAdminConnector, MCPConnectorFilter } from '@cubeplex/core'
import { MCPConnectorCard } from './MCPConnectorCard'

interface MCPConnectorListProps {
  connectors: MCPAdminConnector[]
  loading: boolean
  search: string
  filter: MCPConnectorFilter
  selectedId: string | null
  onSelect: (id: string) => void
}

export function MCPConnectorList({
  connectors,
  loading,
  search,
  filter,
  selectedId,
  onSelect,
}: MCPConnectorListProps) {
  const t = useTranslations('mcpAdmin')

  const filtered = useMemo(() => {
    let list = connectors
    if (search) {
      const q = search.toLowerCase()
      list = list.filter(
        (c) =>
          c.name.toLowerCase().includes(q) ||
          c.provider.toLowerCase().includes(q) ||
          c.description.toLowerCase().includes(q),
      )
    }
    if (filter === 'installed') list = list.filter((c) => c.installed)
    if (filter === 'available') list = list.filter((c) => !c.installed && c.kind === 'catalog')
    if (filter === 'custom') list = list.filter((c) => c.kind === 'custom')
    return list.sort((a, b) => {
      if (a.installed !== b.installed) return a.installed ? -1 : 1
      return a.name.localeCompare(b.name)
    })
  }, [connectors, search, filter])

  if (loading) {
    return <p className="px-4 py-6 text-center text-xs text-muted-foreground">{t('loading')}</p>
  }

  if (filtered.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-1 px-6 text-center">
        <p className="text-sm text-muted-foreground">{t('emptyList')}</p>
        <p className="text-xs text-muted-foreground/70">{t('emptyListHint')}</p>
      </div>
    )
  }

  return (
    <ul className="flex flex-col gap-1.5 p-3">
      {filtered.map((c) => (
        <li key={c.id}>
          <MCPConnectorCard
            connector={c}
            active={selectedId === c.id}
            onClick={() => onSelect(c.id)}
          />
        </li>
      ))}
    </ul>
  )
}
```

- [ ] **Step 2: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize
git add frontend/packages/web/components/mcp/MCPConnectorList.tsx
git commit -m "feat(mcp): add MCPConnectorList sidebar component"
```

---

## Task 8: Frontend — MCPWorkspacesTab Component

The admin Workspaces tab: workspace name + enabled toggle + credential source label. Matches Skills WorkspaceBindingsTable pattern with inline confirm on disable.

**Files:**
- Create: `frontend/packages/web/components/mcp/MCPWorkspacesTab.tsx`

- [ ] **Step 1: Create MCPWorkspacesTab.tsx**

```typescript
'use client'

import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Check, X, Loader2 } from 'lucide-react'
import type { ApiClient, WorkspaceOverride } from '@cubeplex/core'
import { adminGetOverrides, adminPutOverride } from '@cubeplex/core'
import type { Workspace } from '@cubeplex/core'
import { createApiClient, listWorkspaces } from '@cubeplex/core'

interface MCPWorkspacesTabProps {
  serverId: string
  client: ApiClient
}

interface WsEntry {
  ws: Workspace
  enabled: boolean
  credentialSource: string | null
  saving: boolean
  error: string | null
  confirmDisable: boolean
}

export function MCPWorkspacesTab({ serverId, client }: MCPWorkspacesTabProps) {
  const t = useTranslations('mcpAdmin')
  const [entries, setEntries] = useState<WsEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    let active = true

    async function load(): Promise<void> {
      setLoading(true)
      setLoadError(null)
      try {
        const wsClient = createApiClient('')
        const [workspaces, overrides] = await Promise.all([
          listWorkspaces(wsClient),
          adminGetOverrides(client, serverId),
        ])
        if (!active) return
        const enabledMap = new Map(
          overrides.filter((o) => o.enabled).map((o) => [o.workspace_id, true]),
        )
        setEntries(
          workspaces.map((ws) => ({
            ws,
            enabled: enabledMap.has(ws.id),
            credentialSource: null,
            saving: false,
            error: null,
            confirmDisable: false,
          })),
        )
      } catch (err) {
        if (active) setLoadError((err as Error).message)
      } finally {
        if (active) setLoading(false)
      }
    }

    void load()
    return () => { active = false }
  }, [client, serverId])

  function patch(wsId: string, update: Partial<WsEntry>): void {
    setEntries((prev) =>
      prev.map((e) => (e.ws.id === wsId ? { ...e, ...update } : e)),
    )
  }

  async function toggle(wsId: string, enabled: boolean): Promise<void> {
    patch(wsId, { saving: true, error: null, confirmDisable: false })
    try {
      const overrides = await adminPutOverride(client, serverId, {
        workspace_id: wsId,
        enabled,
      })
      const enabledMap = new Map(
        overrides.filter((o) => o.enabled).map((o) => [o.workspace_id, true]),
      )
      setEntries((prev) =>
        prev.map((e) => ({
          ...e,
          enabled: enabledMap.has(e.ws.id),
          saving: e.ws.id === wsId ? false : e.saving,
        })),
      )
    } catch (err) {
      patch(wsId, { saving: false, error: (err as Error).message })
    }
  }

  if (loading) {
    return <p className="text-xs text-muted-foreground">{t('loadingWorkspaces')}</p>
  }
  if (loadError) {
    return <p className="text-xs text-destructive">{loadError}</p>
  }
  if (entries.length === 0) {
    return <p className="text-xs text-muted-foreground">{t('noWorkspaces')}</p>
  }

  const enabledCount = entries.filter((e) => e.enabled).length

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted-foreground">
        {t('workspacesSummary', { enabled: enabledCount, total: entries.length })}
      </p>
      <ul className="flex flex-col divide-y divide-border/70 rounded-md border border-border/70">
        {entries.map(({ ws, enabled, saving, error, confirmDisable }) => (
          <li
            key={ws.id}
            className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
            data-testid={`ws-mcp-row-${ws.name}`}
          >
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium">{ws.name}</div>
              {error && <div className="mt-0.5 text-[11px] text-destructive">{error}</div>}
            </div>

            <div className="flex shrink-0 items-center gap-1.5">
              {saving ? (
                <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
              ) : confirmDisable ? (
                <>
                  <span className="text-xs text-destructive">{t('confirmDisable')}</span>
                  <button
                    type="button"
                    className="cursor-pointer rounded p-0.5 text-destructive hover:bg-destructive/10"
                    onClick={() => void toggle(ws.id, false)}
                  >
                    <Check className="size-3.5" />
                  </button>
                  <button
                    type="button"
                    className="cursor-pointer rounded p-0.5 text-muted-foreground hover:bg-muted"
                    onClick={() => patch(ws.id, { confirmDisable: false })}
                  >
                    <X className="size-3.5" />
                  </button>
                </>
              ) : (
                <label className="inline-flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={enabled}
                    disabled={saving}
                    onChange={(e) => {
                      if (e.target.checked) {
                        void toggle(ws.id, true)
                      } else {
                        patch(ws.id, { confirmDisable: true })
                      }
                    }}
                    className="size-4 cursor-pointer rounded border-border accent-primary"
                    data-testid={`ws-mcp-checkbox-${ws.name}`}
                  />
                  {enabled ? t('enabled') : t('notEnabled')}
                </label>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize
git add frontend/packages/web/components/mcp/MCPWorkspacesTab.tsx
git commit -m "feat(mcp): add MCPWorkspacesTab with inline confirm pattern"
```

---

## Task 9: Frontend — MCPAdminDetailPanel Component

The main detail panel for the admin page. Shows: empty state, install form (for uninstalled catalog), custom server form (for Add Custom), or the installed detail view with Overview/Tools/Workspaces tabs.

**Files:**
- Create: `frontend/packages/web/components/mcp/MCPAdminDetailPanel.tsx`

- [ ] **Step 1: Create MCPAdminDetailPanel.tsx**

This is a large component. It delegates to MCPToolsTable (unchanged), MCPWorkspacesTab (new), and has inline overview content. The key states are:

1. `selectedId === null` → "Select a connector" placeholder
2. `selectedId === '__add_custom__'` → MCPServerForm inline
3. Selected uninstalled catalog connector → MCPInstallForm inline
4. Selected installed connector → detail view with tabs

```typescript
'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Check, FileText, Loader2, Network, RefreshCw, Trash2, Wrench, X } from 'lucide-react'
import type { ApiClient, MCPAdminConnector } from '@cubeplex/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { MCPToolsTable } from './MCPToolsTable'
import { MCPWorkspacesTab } from './MCPWorkspacesTab'

interface MCPAdminDetailPanelProps {
  connector: MCPAdminConnector | null
  mode: 'detail' | 'add_custom' | null
  client: ApiClient
  onRefresh: (id: string) => Promise<void>
  onDelete: (id: string) => Promise<void>
}

function InfoRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg bg-muted/30 p-3 sm:flex-row sm:items-center sm:justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium break-all">{children}</span>
    </div>
  )
}

export function MCPAdminDetailPanel({
  connector,
  mode,
  client,
  onRefresh,
  onDelete,
}: MCPAdminDetailPanelProps) {
  const t = useTranslations('mcpAdmin')
  const [refreshing, setRefreshing] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)

  if (!connector && mode !== 'add_custom') {
    return (
      <div className="flex flex-1 items-center justify-center p-8 text-sm text-muted-foreground">
        {t('selectConnector')}
      </div>
    )
  }

  if (!connector || !connector.installed) {
    return (
      <div className="flex flex-1 items-center justify-center p-8 text-sm text-muted-foreground">
        {t('installFormPlaceholder')}
      </div>
    )
  }

  const server = connector.server
  if (!server) {
    return (
      <div className="flex flex-1 items-center justify-center p-8 text-sm text-muted-foreground">
        {t('noServerData')}
      </div>
    )
  }

  async function handleRefresh(): Promise<void> {
    setRefreshing(true)
    try {
      await onRefresh(server!.id)
    } finally {
      setRefreshing(false)
    }
  }

  async function handleDelete(): Promise<void> {
    setDeleting(true)
    try {
      await onDelete(server!.id)
    } finally {
      setDeleting(false)
      setConfirmDelete(false)
    }
  }

  return (
    <div className="flex w-full flex-col gap-4 p-6" data-testid="mcp-detail-panel">
      {/* Header */}
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-xl font-semibold tracking-tight">{connector.name}</h3>
          {connector.kind === 'catalog' && (
            <Badge variant="outline" className="text-[10px]">{connector.provider}</Badge>
          )}
          <Badge variant={connector.authed ? 'default' : 'secondary'}>
            {connector.authed ? t('authenticated') : t('notAuthenticated')}
          </Badge>
          {connector.kind === 'custom' && (
            <Badge variant="secondary">{t('custom')}</Badge>
          )}
          <div className="ml-auto flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={refreshing || deleting}
              onClick={() => void handleRefresh()}
            >
              {refreshing ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <RefreshCw className="size-3.5" />
              )}
              {t('refreshTools')}
            </Button>
            {!confirmDelete ? (
              <Button
                size="sm"
                variant="ghost"
                className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                disabled={refreshing || deleting}
                onClick={() => setConfirmDelete(true)}
              >
                <Trash2 className="size-3.5" />
                {t('delete')}
              </Button>
            ) : (
              <div className="flex items-center gap-1.5 rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-1.5">
                <span className="text-xs text-destructive">{t('confirmDelete')}</span>
                <button
                  type="button"
                  className="cursor-pointer rounded p-0.5 text-destructive hover:bg-destructive/20"
                  disabled={deleting}
                  onClick={() => void handleDelete()}
                >
                  {deleting ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />}
                </button>
                <button
                  type="button"
                  className="cursor-pointer rounded p-0.5 text-muted-foreground hover:bg-muted"
                  onClick={() => setConfirmDelete(false)}
                >
                  <X className="size-3.5" />
                </button>
              </div>
            )}
          </div>
        </div>
        {connector.description && (
          <p className="text-sm leading-relaxed text-muted-foreground">{connector.description}</p>
        )}
        {server.last_error && (
          <p className="text-sm text-destructive">{server.last_error}</p>
        )}
      </header>

      {/* Tabs */}
      <Tabs defaultValue="overview" className="flex-1 flex-col">
        <TabsList variant="line" className="w-full justify-start border-b border-border/60 pb-0">
          <TabsTrigger value="overview">
            <FileText className="size-3.5" />
            {t('overview')}
          </TabsTrigger>
          <TabsTrigger value="tools">
            <Wrench className="size-3.5" />
            {t('toolsTab', { count: server.tools_cache?.length ?? 0 })}
          </TabsTrigger>
          {server.owner_workspace_id === null && (
            <TabsTrigger value="workspaces">
              <Network className="size-3.5" />
              {t('workspaces')}
            </TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="overview" className="mt-4 flex flex-col gap-4">
          <div className="flex flex-col gap-2 text-sm">
            <InfoRow label={t('url')}>{server.server_url}</InfoRow>
            <InfoRow label={t('transport')}>{server.transport}</InfoRow>
            <InfoRow label={t('authMethod')}>{server.auth_method}</InfoRow>
            <InfoRow label={t('credentialScope')}>{server.credential_scope}</InfoRow>
          </div>
          {/* Org Credential card */}
          {server.auth_method !== 'none' && (
            <div className="rounded-lg border border-border/70 p-4">
              <div className="text-sm font-semibold mb-2">{t('orgCredential')}</div>
              <div className="flex items-center gap-2">
                <Badge
                  variant="outline"
                  className={
                    server.authed
                      ? 'border-emerald-500/40 text-emerald-600'
                      : 'border-destructive/40 text-destructive'
                  }
                >
                  {server.authed ? t('credentialActive') : t('credentialMissing')}
                </Badge>
                <span className="text-xs text-muted-foreground">{server.auth_method}</span>
              </div>
            </div>
          )}
        </TabsContent>

        <TabsContent value="tools" className="mt-4">
          <MCPToolsTable tools={server.tools_cache ?? []} />
        </TabsContent>

        {server.owner_workspace_id === null && (
          <TabsContent value="workspaces" className="mt-4">
            <MCPWorkspacesTab serverId={server.id} client={client} />
          </TabsContent>
        )}
      </Tabs>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize
git add frontend/packages/web/components/mcp/MCPAdminDetailPanel.tsx
git commit -m "feat(mcp): add MCPAdminDetailPanel with tabs and inline confirm"
```

---

## Task 10: Frontend — Rewrite Admin MCP Page

The master-detail page that wires all components together. Matches Skills page.tsx structure exactly.

**Files:**
- Rewrite: `frontend/packages/web/app/admin/mcp/page.tsx`
- Delete: `frontend/packages/web/app/admin/mcp/new/page.tsx`
- Delete: `frontend/packages/web/app/admin/mcp/[id]/page.tsx`

- [ ] **Step 1: Delete old sub-pages**

```bash
rm -f /home/chris/cubeplex/.worktrees/feat/mcp-optimize/frontend/packages/web/app/admin/mcp/new/page.tsx
rm -rf /home/chris/cubeplex/.worktrees/feat/mcp-optimize/frontend/packages/web/app/admin/mcp/\[id\]
```

- [ ] **Step 2: Rewrite admin/mcp/page.tsx**

```typescript
'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  createApiClient,
  useMcpStore,
  useWorkspaceStore,
  type MCPConnectorFilter,
} from '@cubeplex/core'
import { MCPToolbar } from '@/components/mcp/MCPToolbar'
import { MCPConnectorList } from '@/components/mcp/MCPConnectorList'
import { MCPAdminDetailPanel } from '@/components/mcp/MCPAdminDetailPanel'

export default function AdminMcpPage() {
  const t = useTranslations('mcpAdmin')
  const client = useMemo(() => createApiClient(''), [])

  const connectors = useMcpStore((s) => s.connectors)
  const loading = useMcpStore((s) => s.loading)
  const selectedId = useMcpStore((s) => s.selectedId)
  const setSelectedId = useMcpStore((s) => s.setSelectedId)
  const fetchAll = useMcpStore((s) => s.fetchAll)
  const refreshTools = useMcpStore((s) => s.refreshTools)
  const deleteServer = useMcpStore((s) => s.deleteServer)

  const workspaces = useWorkspaceStore((s) => s.workspaces)
  const fetchWorkspaceList = useWorkspaceStore((s) => s.fetchList)

  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<MCPConnectorFilter>('all')
  const [mode, setMode] = useState<'detail' | 'add_custom' | null>(null)

  const lensWsId = workspaces[0]?.id ?? ''

  useEffect(() => {
    if (workspaces.length === 0) void fetchWorkspaceList(client)
  }, [client, fetchWorkspaceList, workspaces.length])

  useEffect(() => {
    if (lensWsId) void fetchAll(client, lensWsId)
  }, [client, fetchAll, lensWsId])

  const selected = useMemo(
    () => connectors.find((c) => c.id === selectedId) ?? null,
    [connectors, selectedId],
  )

  function handleSelect(id: string): void {
    setSelectedId(id)
    setMode('detail')
  }

  function handleAddCustom(): void {
    setSelectedId(null)
    setMode('add_custom')
  }

  async function handleRefresh(id: string): Promise<void> {
    await refreshTools(client, id)
  }

  async function handleDelete(id: string): Promise<void> {
    await deleteServer(client, id, lensWsId)
  }

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border/70 px-6 py-4">
        <h2 className="text-lg font-semibold tracking-tight">{t('pageTitle')}</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{t('pageSubtitle')}</p>
      </header>

      <MCPToolbar
        search={search}
        onSearchChange={setSearch}
        filter={filter}
        onFilterChange={setFilter}
        onAddCustom={handleAddCustom}
      />

      <div className="flex flex-1 overflow-hidden">
        <aside
          aria-label="connector-list"
          className="w-[360px] shrink-0 overflow-y-auto border-r border-border/70 bg-card/20"
        >
          <MCPConnectorList
            connectors={connectors}
            loading={loading}
            search={search}
            filter={filter}
            selectedId={selectedId}
            onSelect={handleSelect}
          />
        </aside>

        <section className="flex flex-1 overflow-y-auto">
          <MCPAdminDetailPanel
            connector={selected}
            mode={mode}
            client={client}
            onRefresh={handleRefresh}
            onDelete={handleDelete}
          />
        </section>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize
git add -A frontend/packages/web/app/admin/mcp/
git commit -m "$(cat <<'EOF'
feat(mcp): rewrite admin page as master-detail layout

Replaces catalog grid + drawer + separate detail/new pages with a
unified master-detail layout matching the Skills page pattern.
Sidebar shows all connectors (catalog + custom) with rich cards.
Detail panel has Overview/Tools/Workspaces tabs.
EOF
)"
```

---

## Task 11: Frontend — Delete Old Components and Routes

Clean up the deleted pages and catalog components.

**Files to delete:**
- `frontend/packages/web/app/(app)/w/[wsId]/integrations/mcp/page.tsx`
- `frontend/packages/web/app/(app)/w/[wsId]/integrations/mcp/[id]/page.tsx`
- `frontend/packages/web/app/(app)/w/[wsId]/integrations/mcp/new/page.tsx`
- `frontend/packages/web/components/mcp/catalog/MCPCatalogGrid.tsx`
- `frontend/packages/web/components/mcp/catalog/MCPCatalogCard.tsx`
- `frontend/packages/web/components/mcp/catalog/MCPInstallDrawer.tsx`
- `frontend/packages/web/components/mcp/catalog/MCPStaticForm.tsx`
- `frontend/packages/web/components/mcp/catalog/StatusChip.tsx`
- `frontend/packages/web/components/mcp/catalog/index.ts`
- `frontend/packages/web/components/mcp/MCPServerList.tsx`
- `frontend/packages/web/components/mcp/MCPOverrideGrid.tsx`
- `frontend/packages/core/src/stores/workspaceMcpStore.ts`

- [ ] **Step 1: Delete files**

```bash
cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize
rm -rf frontend/packages/web/app/\(app\)/w/\[wsId\]/integrations/mcp
rm -rf frontend/packages/web/components/mcp/catalog
rm -f frontend/packages/web/components/mcp/MCPServerList.tsx
rm -f frontend/packages/web/components/mcp/MCPOverrideGrid.tsx
rm -f frontend/packages/core/src/stores/workspaceMcpStore.ts
```

- [ ] **Step 2: Remove imports of deleted files**

Search for and remove all imports of the deleted files in the codebase:

```bash
grep -rn "workspaceMcpStore\|MCPOverrideGrid\|MCPServerList\|MCPCatalogGrid\|MCPInstallDrawer\|MCPCatalogCard\|MCPStaticForm\|StatusChip\|catalog/index" frontend/packages/ --include="*.ts" --include="*.tsx" -l
```

Update `frontend/packages/core/src/index.ts` to remove `workspaceMcpStore` exports. Remove any references to `MCPOverrideGrid`, `MCPServerList`, catalog components from remaining files.

- [ ] **Step 3: Fix any references to /integrations/mcp routes in McpPanel.tsx and elsewhere**

The `McpPanel.tsx` has links to `/w/${wsId}/integrations/mcp/new` and `/w/${wsId}/integrations/mcp/${id}`. These must be removed.

- [ ] **Step 4: Run type check to catch broken imports**

Run: `cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize/frontend && pnpm type-check`

Fix any errors.

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize
git add -A
git commit -m "$(cat <<'EOF'
refactor(mcp): delete old pages, catalog components, and workspaceMcpStore

Removes the 3 workspace /integrations/mcp routes (consolidated into
workspace settings), catalog grid/drawer/card components (replaced by
master-detail), MCPServerList, MCPOverrideGrid (replaced by
MCPWorkspacesTab), and workspaceMcpStore (merged into mcpStore).
EOF
)"
```

---

## Task 12: Frontend — Enhance McpPanel (Workspace Settings MCP Tab)

Update the workspace settings MCP tab to show credential mode + source, add credential_mode selector in detail panel, remove links to deleted routes, and improve the detail panel.

**Files:**
- Modify: `frontend/packages/web/components/workspace-settings/McpPanel.tsx`

- [ ] **Step 1: Update McpPanel to show credential_mode and credential_source**

Key changes:
1. Remove `Link` to `/w/${wsId}/integrations/mcp/new` and `/w/${wsId}/integrations/mcp/${id}`
2. Add credential source badge to `McpItemCard` (mode-aware)
3. Add credential_mode radio selector in detail panel for org connectors (workspace admin only)
4. Show credential state based on resolved mode+source
5. Replace "Add connector" button with inline-form trigger for workspace admin

In `McpItemCard`, add the credential source display (now mode-aware):

```typescript
function McpItemCard({
  srv,
  active,
  toggling,
  onClick,
  onToggle,
}: {
  srv: MCPServerItem
  active: boolean
  toggling: boolean
  onClick: () => void
  onToggle: (enabled: boolean) => void
}) {
  const t = useTranslations('mcp.wsPanel')
  const SourceIcon = srv.scope === 'workspace' ? Plug : Globe
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? 'true' : undefined}
      className={cn(
        'group flex w-full flex-col gap-1.5 rounded-lg border p-3 text-left transition-all',
        active
          ? 'border-primary/40 bg-primary/5 shadow-sm'
          : 'border-border/70 bg-card/40 hover:border-border hover:bg-accent/40',
      )}
    >
      <div className="flex items-center gap-2">
        <SourceIcon
          className={cn(
            'size-3.5 shrink-0',
            srv.scope === 'workspace' ? 'text-muted-foreground' : 'text-primary',
          )}
        />
        <span className="truncate text-sm font-semibold">{srv.name}</span>
        {srv.enabled && (
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="size-3" />
            {t('onBadge')}
          </span>
        )}
        {srv.scope === 'org' && (
          <Switch
            checked={srv.enabled}
            disabled={toggling}
            onCheckedChange={onToggle}
            onClick={(e) => e.stopPropagation()}
            className="ml-auto shrink-0 scale-75"
          />
        )}
      </div>
      <p className="line-clamp-1 truncate text-xs text-muted-foreground">{srv.server_url}</p>
      <div className="flex items-center gap-1 pt-0.5">
        <Badge variant="outline" className="px-1.5 text-[10px]">
          {srv.transport}
        </Badge>
        {srv.scope === 'org' && srv.credential_source === 'org' && (
          <Badge variant="outline" className="px-1.5 text-[10px] border-primary/30 text-primary">
            {t('usingOrgCredential')}
          </Badge>
        )}
        {srv.scope === 'org' && srv.credential_source === 'workspace' && (
          <Badge variant="outline" className="px-1.5 text-[10px] border-blue-500/30 text-blue-600">
            {srv.credential_shared_by
              ? t('workspaceCredentialSharedBy', { name: srv.credential_shared_by })
              : t('workspaceCredential')}
          </Badge>
        )}
        {srv.scope === 'org' && srv.credential_source === 'user' && (
          <Badge variant="outline" className="px-1.5 text-[10px] border-emerald-500/30 text-emerald-600">
            {t('perUserActive')}
          </Badge>
        )}
        {srv.scope === 'org' && srv.credential_source === 'needs_setup' && (
          <Badge variant="outline" className="px-1.5 text-[10px] border-destructive/30 text-destructive">
            {t('needsSetup')}
          </Badge>
        )}
      </div>
    </button>
  )
}
```

In the McpPanel header, remove the Link to integrations/mcp/new:

```typescript
<header className="flex items-center justify-between gap-2 border-b border-border/70 px-6 py-4">
  <div>
    <h2 className="text-lg font-semibold tracking-tight">{t('title')}</h2>
    <p className="mt-0.5 text-xs text-muted-foreground">
      {t('summary', { enabled: enabledCount, total: allServers.length })}
    </p>
  </div>
</header>
```

In the detail panel section, remove the Link to `/w/${wsId}/integrations/mcp/${id}`:

```typescript
In the detail panel for org connectors, add the credential_mode selector (workspace admin only). This is a radio group below the overview info:

```typescript
{selected.scope === 'org' && isWsAdmin && (
  <div className="flex flex-col gap-3 rounded-lg border border-border/70 p-4">
    <h4 className="text-sm font-semibold">{t('credentialMode')}</h4>
    <RadioGroup
      value={selected.credential_mode}
      onValueChange={(mode) => void handleCredentialModeChange(selected.server_id, mode)}
    >
      <label className="flex items-start gap-3 rounded-lg border p-3 cursor-pointer hover:bg-accent/40">
        <RadioGroupItem value="org" />
        <span className="flex flex-col gap-0.5">
          <span className="text-sm font-medium">{t('modeOrg')}</span>
          <span className="text-xs text-muted-foreground">{t('modeOrgDesc')}</span>
        </span>
      </label>
      <label className="flex items-start gap-3 rounded-lg border p-3 cursor-pointer hover:bg-accent/40">
        <RadioGroupItem value="workspace" />
        <span className="flex flex-col gap-0.5">
          <span className="text-sm font-medium">{t('modeWorkspace')}</span>
          <span className="text-xs text-muted-foreground">{t('modeWorkspaceDesc')}</span>
        </span>
      </label>
      <label className="flex items-start gap-3 rounded-lg border p-3 cursor-pointer hover:bg-accent/40">
        <RadioGroupItem value="user" />
        <span className="flex flex-col gap-0.5">
          <span className="text-sm font-medium">{t('modeUser')}</span>
          <span className="text-xs text-muted-foreground">{t('modeUserDesc')}</span>
        </span>
      </label>
    </RadioGroup>
    {/* Credential state below the selector */}
    {selected.credential_mode === 'workspace' && selected.credential_source === 'workspace' && (
      <div className="flex items-center gap-2 text-sm">
        <CheckCircle2 className="size-3.5 text-emerald-600" />
        <span>{t('sharedBy', { name: selected.credential_shared_by })}</span>
        <Button size="sm" variant="ghost" className="ml-auto text-destructive"
          onClick={() => void handleClearWorkspaceCredential(selected.server_id)}>
          {t('clear')}
        </Button>
      </div>
    )}
    {selected.credential_mode === 'workspace' && selected.credential_source === 'needs_setup' && (
      <p className="text-xs text-muted-foreground">{t('workspaceCredNeeded')}</p>
    )}
    {selected.credential_mode === 'user' && (
      <p className="text-xs text-muted-foreground">{t('perUserExplain')}</p>
    )}
  </div>
)}
```

The `handleCredentialModeChange` function calls `PATCH /ws/{wsId}/settings/mcp/{serverId}` with `{ credential_mode: mode }` and refreshes the list.

- [ ] **Step 2: Run type check**

Run: `cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize/frontend && pnpm type-check`

- [ ] **Step 3: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize
git add frontend/packages/web/components/workspace-settings/McpPanel.tsx
git commit -m "$(cat <<'EOF'
feat(mcp): enhance McpPanel with credential mode selector

Workspace admin can choose credential mode per org connector:
org-shared, workspace-shared (with shared_by tracking), or per-user.
Shows mode-aware badges and detail panel with radio group selector.
Removes links to deleted /integrations/mcp routes.
EOF
)"
```

---

## Task 13: Frontend — Remove OAuth "Coming Soon" from MCPServerForm

**Files:**
- Modify: `frontend/packages/web/components/mcp/MCPServerForm.tsx:208-219`

- [ ] **Step 1: Remove the disabled OAuth radio option**

In `frontend/packages/web/components/mcp/MCPServerForm.tsx`, delete the disabled OAuth radio group item (lines ~208-219):

```typescript
// DELETE this entire block:
<label
  htmlFor="mcp-scope-oauth"
  className="flex cursor-not-allowed items-start gap-3 rounded-lg border p-4 opacity-60"
  title={t('comingSoonTooltip')}
>
  <RadioGroupItem value="oauth-disabled" disabled id="mcp-scope-oauth" />
  <span className="flex flex-col gap-1">
    <span className="font-medium">{t('oauth')}</span>
    <span className="text-sm text-muted-foreground">{t('oauthComingSoon')}</span>
  </span>
</label>
```

- [ ] **Step 2: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize
git add frontend/packages/web/components/mcp/MCPServerForm.tsx
git commit -m "fix(mcp): remove OAuth 'coming soon' from custom server form"
```

---

## Task 14: i18n — Add Translation Keys

Add the translation keys used by all new components.

**Files:**
- Modify: `frontend/packages/web/messages/en.json` (or equivalent i18n file)
- Modify: `frontend/packages/web/messages/zh.json` (if exists)

- [ ] **Step 1: Find the i18n file structure**

```bash
find /home/chris/cubeplex/.worktrees/feat/mcp-optimize/frontend -name "en.json" -o -name "messages" -type d | head -10
```

- [ ] **Step 2: Add mcpAdmin namespace keys**

Add a `mcpAdmin` section with all the keys used by new components:

```json
{
  "mcpAdmin": {
    "pageTitle": "MCP Connectors",
    "pageSubtitle": "Manage connector registrations, authentication, and workspace distribution",
    "searchPlaceholder": "Search connectors...",
    "searchAriaLabel": "Search connectors",
    "filterByStatus": "Filter by status",
    "filterAll": "All",
    "filterInstalled": "Installed",
    "filterAvailable": "Available",
    "filterCustom": "Custom",
    "addCustom": "Add Custom",
    "loading": "Loading connectors...",
    "emptyList": "No connectors found",
    "emptyListHint": "Install from the catalog or add a custom server",
    "statusInstalled": "Installed",
    "statusError": "Error",
    "statusNotAuthed": "Not authed",
    "available": "Available",
    "workspaceCount": "{count} workspaces",
    "selectConnector": "Select a connector to view details",
    "installFormPlaceholder": "Select an available connector to install",
    "noServerData": "No server data available",
    "overview": "Overview",
    "toolsTab": "Tools ({count})",
    "workspaces": "Workspaces",
    "url": "URL",
    "transport": "Transport",
    "authMethod": "Auth Method",
    "credentialScope": "Credential Scope",
    "orgCredential": "Org Credential",
    "credentialActive": "Active",
    "credentialMissing": "Not configured",
    "authenticated": "Authenticated",
    "notAuthenticated": "Not authenticated",
    "custom": "Custom",
    "refreshTools": "Refresh Tools",
    "delete": "Delete",
    "confirmDelete": "Delete this connector?",
    "loadingWorkspaces": "Loading workspaces...",
    "noWorkspaces": "No workspaces in this organization",
    "workspacesSummary": "{enabled} of {total} workspaces enabled",
    "confirmDisable": "Disable?",
    "enabled": "Enabled",
    "notEnabled": "Not enabled"
  }
}
```

- [ ] **Step 3: Add workspace panel credential mode + source keys**

Add to the `mcp.wsPanel` namespace:

```json
{
  "mcp": {
    "wsPanel": {
      "usingOrgCredential": "Org credential",
      "workspaceCredential": "Workspace credential",
      "workspaceCredentialSharedBy": "Shared by {name}",
      "perUserActive": "Per-user (active)",
      "needsSetup": "Needs setup",
      "credentialMode": "Credential Mode",
      "modeOrg": "Use org credential",
      "modeOrgDesc": "Use the org-level shared credential for all members",
      "modeWorkspace": "Workspace credential",
      "modeWorkspaceDesc": "Provide one credential shared by all workspace members",
      "modeUser": "Per-user",
      "modeUserDesc": "Each member authenticates individually",
      "sharedBy": "Shared by {name}",
      "clear": "Clear",
      "workspaceCredNeeded": "A workspace admin needs to provide a credential",
      "perUserExplain": "Each member will be prompted to authenticate when using this connector"
    }
  }
}
```

- [ ] **Step 4: Run the dev server to verify no missing translation warnings**

Run: `cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize/frontend && pnpm dev`

Check browser console for missing translation warnings.

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize
git add frontend/packages/web/messages/
git commit -m "feat(mcp): add i18n keys for admin master-detail and credential source"
```

---

## Task 15: Frontend — Type Check and Build Verification

Full verification pass.

- [ ] **Step 1: Run frontend type check**

Run: `cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize/frontend && pnpm type-check`

Fix all type errors.

- [ ] **Step 2: Run frontend build**

Run: `cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize/frontend && pnpm build`

Fix any build errors.

- [ ] **Step 3: Run backend checks**

Run: `cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize/backend && make lint && make type-check`

Fix any errors.

- [ ] **Step 4: Commit fixes if any**

```bash
cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize
git add -A
git commit -m "fix: resolve type check and build errors from MCP overhaul"
```

---

## Task 16: Visual Smoke Test

Start dev servers and verify the new admin page in browser.

- [ ] **Step 1: Start backend**

Run: `cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize/backend && python main.py`

(Uses port from .worktree.env, likely 8026)

- [ ] **Step 2: Start frontend**

Run: `cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize/frontend && pnpm dev`

(Uses port from .worktree.env, likely 3026)

- [ ] **Step 3: Test admin MCP page**

Navigate to `http://localhost:3026/admin/mcp` and verify:

1. Master-detail layout renders (sidebar 360px + detail panel)
2. Toolbar shows search input + filter pills + Add Custom button
3. Catalog connectors and custom servers appear in sidebar
4. Clicking a connector shows detail panel with Overview/Tools/Workspaces tabs
5. Workspaces tab shows checkboxes with inline confirm pattern
6. Delete uses inline confirm (no `window.confirm`)
7. Empty state shows "Select a connector" when nothing is selected

- [ ] **Step 4: Test workspace settings MCP tab**

Navigate to `http://localhost:3026/w/<wsId>/settings` and verify:

1. MCP tab shows org servers with credential source badges
2. Toggle switch works for enabling/disabling
3. No broken links to deleted routes

- [ ] **Step 5: Commit any visual fixes**

```bash
cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize
git add -A
git commit -m "fix(mcp): visual fixes from smoke test"
```

---

## Task 17: E2E Tests

Update or create E2E tests for the new admin page and workspace settings.

**Files:**
- Modify/Create: `frontend/packages/web/__tests__/e2e/mcp/admin-mcp.spec.ts`

- [ ] **Step 1: Check existing E2E test structure**

```bash
find /home/chris/cubeplex/.worktrees/feat/mcp-optimize/frontend -path "*e2e*mcp*" -type f
```

- [ ] **Step 2: Write admin MCP page E2E test**

Key scenarios:
1. Page loads with master-detail layout
2. Sidebar shows connectors
3. Clicking a connector opens detail panel
4. Search filters the sidebar
5. Filter pills work (Installed/Available/Custom)
6. Delete with inline confirm works

- [ ] **Step 3: Run E2E tests**

Run: `cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize/frontend && pnpm test:e2e --grep mcp`

- [ ] **Step 4: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/mcp-optimize
git add frontend/packages/web/__tests__/e2e/
git commit -m "test(mcp): add E2E tests for admin master-detail page"
```
