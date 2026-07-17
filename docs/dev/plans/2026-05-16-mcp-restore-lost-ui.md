# MCP Lost-UI Restoration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the seven MCP UI/API surfaces removed in commit `243e6396` (legacy catalog/server/override cleanup) on top of the four-layer model — without changing the data model.

**Architecture:** All seven features are either (a) read-only consumers of existing install columns (`tools_cache`, `tool_citations`, `last_error`, `discovery_status`) that are populated by a new discovery method, or (b) single-row writes to existing columns. Backend adds five new endpoints, two new service methods (`discover_tools_for_install`, `create_custom_install_for_org`, `promote_workspace_install_to_org`, `invoke_tool_for_install`) plus DTO field additions. Frontend adds a Tools tab, Citations tab, Error banner, Custom install form, Promote dialog, Try-It sub-tab, and Refresh button wiring.

**Tech Stack:** FastAPI, SQLModel, PostgreSQL, Redis, httpx, cubepi MCP helpers, pytest, Next.js, React, TypeScript, Tailwind, shadcn/ui, Playwright.

**Spec:** `docs/dev/specs/2026-05-16-mcp-restore-lost-ui-design.md`

---

## File Structure

### Backend

| Path | Status | Purpose |
| --- | --- | --- |
| `backend/cubeplex/api/schemas/mcp.py` | Modify | Add `tools` and `tool_citations` to `MCPConnectorInstallOut`; add `AdminCreateCustomInstallIn`, `AdminInstallRefreshIn`, `AdminInstallInvokeIn`, `WsInstallInvokeIn`, `PromoteInstallIn`, `TestConnectionIn`, `TestConnectionOut`, `ToolCitationUpsertIn`, `ToolInvokeOut`, `MCPToolEntry`. |
| `backend/cubeplex/services/mcp_discovery.py` | Create | `discover_tools_for_install(install, *, workspace_id, actor_user_id, session, …)` — builds the cubepi MCP client with the right grant, calls `load_mcp_tools_http`, writes `tools_cache` / `discovery_status` / `last_error`. Returns updated install row. |
| `backend/cubeplex/services/mcp_installs.py` | Modify | Add `create_custom_install_for_org(...)`; `promote_workspace_install_to_org(install_id, distribution)`. |
| `backend/cubeplex/api/routes/v1/admin_mcp.py` | Modify | New routes: `POST /installs/{id}/refresh-discovery`, `POST /test-connection`, `POST /installs/{id}/promote-to-org`, `PUT /installs/{id}/tool-citations`, `POST /installs/{id}/tools/{tool_name}/invoke`. Relax `create_admin_install` to accept `template_id=None` via `AdminCreateCustomInstallIn` branch. Modify `_install_to_out` to include `tools` + `tool_citations`. |
| `backend/cubeplex/api/routes/v1/ws_mcp.py` | Modify | New routes: `POST /installs/{id}/refresh-discovery`, `POST /installs/{id}/tools/{tool_name}/invoke`. |
| `backend/cubeplex/mcp/exceptions.py` | Modify | Add `MCPDiscoveryFailed`, `MCPInvokeFailed`, `MCPInvokeRateLimited` exception classes. |
| `backend/tests/e2e/test_mcp_restore_lost_ui.py` | Create | E2E for the seven new endpoints. |

### Frontend

| Path | Status | Purpose |
| --- | --- | --- |
| `frontend/packages/core/src/types/mcp.ts` | Modify | Add `tools: MCPToolEntry[]` and `tool_citations: Record<string, CitationConfigJSON> \| null` to `MCPConnectorInstall`. Add request/response types for all new endpoints. |
| `frontend/packages/core/src/api/mcp.ts` | Modify | Add helpers: `adminRefreshDiscovery`, `wsRefreshDiscovery`, `adminTestConnection`, `adminCreateCustomInstall`, `adminPromoteToOrg`, `adminUpsertToolCitation`, `adminInvokeTool`, `wsInvokeTool`. |
| `frontend/packages/core/src/hooks/useOrgAdminFlag.ts` | Create | Hook reading `useAuthStore().user.org_memberships` to compute `isOrgAdmin(orgId)`. |
| `frontend/packages/core/src/api/auth.ts` | Modify | `MeResult.org_memberships?: Array<{org_id, role}>` so the new hook has data. Backend `/auth/me` already returns this if the field exists — verify; if missing, add to backend. |
| `frontend/packages/web/components/mcp/detail/tools/ToolList.tsx` | Create | Master-detail tool list with search. |
| `frontend/packages/web/components/mcp/detail/tools/ToolDetail.tsx` | Create | Tool detail pane with Schema / Raw JSON / Try It sub-tabs. |
| `frontend/packages/web/components/mcp/detail/tools/SchemaView.tsx` | Create | Pretty-render `input_schema`. |
| `frontend/packages/web/components/mcp/detail/tools/SchemaParameterRow.tsx` | Create | Single parameter row (name + type + required + description + nested). |
| `frontend/packages/web/components/mcp/detail/tools/JsonView.tsx` | Create | Collapsible JSON viewer. |
| `frontend/packages/web/components/mcp/detail/tools/TryItView.tsx` | Create | Form derived from input_schema, Invoke button, result panel. |
| `frontend/packages/web/components/mcp/detail/tools/ToolsPanel.tsx` | Create | Container that hosts the master-detail layout (ToolList + ToolDetail). |
| `frontend/packages/web/components/mcp/detail/MasterDetailList.tsx` | Create | Generic master-detail rail with search (reused by ToolList + CitationList). |
| `frontend/packages/web/components/mcp/detail/ServerErrorBanner.tsx` | Create | Discovery error banner. |
| `frontend/packages/web/components/mcp/MCPCustomCreatePanel.tsx` | Create | Custom connector creation form with embedded "Test connection". |
| `frontend/packages/web/components/mcp/MCPPromoteDialog.tsx` | Create | Promote ws → org dialog. |
| `frontend/packages/web/components/mcp/MCPCitationEditor.tsx` | Create | Editor for one tool's citation config. |
| `frontend/packages/web/components/mcp/MCPCitationsTab.tsx` | Create | Tab listing all tools with citation status + the editor pane. |
| `frontend/packages/web/components/mcp/MCPAdminDetailPanel.tsx` | Modify | Add Tools / Citations tabs, Promote menu item, Error banner, refresh-button wiring to backend. |
| `frontend/packages/web/components/workspace-settings/McpPanel.tsx` | Modify | Wire Tools tab + Refresh button + Error banner + Promote menu (gated on `useOrgAdminFlag`) into `ConnectorDetail`. |
| `frontend/packages/web/lib/jsonSchemaTypes.ts` | Create | Helpers `getProperties(schema)` / `getRequired(schema)` / `SchemaNode` shared by tools UI. |
| `frontend/packages/web/messages/en.json` + `messages/zh.json` | Modify | Add i18n keys: `mcp.tools.*`, `mcp.citations.*`, `mcp.promote.*`, `mcp.custom.*`, `mcp.errorBanner.*`. |
| `frontend/packages/web/__tests__/e2e/mcp/restore-lost-ui.spec.ts` | Create | Playwright E2E for the restored UI. |

---

## Task 1: DTO expansion — expose tools + tool_citations

**Files:**
- Modify: `backend/cubeplex/api/schemas/mcp.py`
- Modify: `backend/cubeplex/api/routes/v1/admin_mcp.py`
- Test: `backend/tests/e2e/test_mcp_restore_lost_ui.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/e2e/test_mcp_restore_lost_ui.py`:

```python
"""E2E coverage for the lost-UI restoration features."""

from __future__ import annotations

import pytest
import httpx


pytestmark = pytest.mark.asyncio


async def test_install_dto_exposes_tools_and_tool_citations(
    admin_client: tuple[httpx.AsyncClient, str],
    seeded_static_org_install_with_tools_cache,
) -> None:
    """MCPConnectorInstallOut must expose the tools list (not just
    tool_count) and tool_citations dict (for org admin callers)."""
    client, _ws = admin_client
    install_id = seeded_static_org_install_with_tools_cache
    res = await client.get(f"/api/v1/admin/mcp/installs/{install_id}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert "tools" in body, "tools field missing from install DTO"
    assert isinstance(body["tools"], list)
    if body["tools"]:
        sample = body["tools"][0]
        assert {"name", "description", "input_schema"} <= sample.keys()
    assert "tool_citations" in body
    assert isinstance(body["tool_citations"], dict) or body["tool_citations"] is None
```

Add the fixture (same file, copying the pattern from existing
`seeded_static_org_install` in `tests/e2e/conftest.py`):

```python
@pytest.fixture
async def seeded_static_org_install_with_tools_cache(
    db_session_maker,
    seed_org_workspace_user,
):
    """Org-scope static install pre-populated with two fake tools."""
    org_id, _ws_id, user_id = seed_org_workspace_user
    async with db_session_maker() as session:
        from cubeplex.models.mcp import MCPConnectorInstall
        from cubeplex.mcp._constants import server_url_hash
        install = MCPConnectorInstall(
            org_id=org_id,
            template_id=None,
            install_scope="org",
            name="seeded-with-tools",
            server_url="https://seeded.example.com/mcp",
            server_url_hash=server_url_hash("https://seeded.example.com/mcp"),
            transport="streamable_http",
            auth_method="static",
            default_credential_policy="org",
            auth_status="pending",
            install_state="active",
            tools_cache=[
                {"name": "ping", "description": "say hi", "input_schema": {"type": "object"}},
                {"name": "pong", "description": "say bye", "input_schema": {"type": "object"}},
            ],
            tool_citations={"ping": {"content_type": "json", "source_type": "api", "content_field": None, "mapping": {"snippet": ""}}},
            created_by_user_id=user_id,
        )
        session.add(install)
        await session.commit()
        await session.refresh(install)
        return install.id
```

- [ ] **Step 2: Run to verify failure**

```bash
cd backend && uv run pytest tests/e2e/test_mcp_restore_lost_ui.py -v --no-cov -k tools_and_tool_citations
```

Expected: FAIL — `tools` field missing.

- [ ] **Step 3: Extend the schema**

Edit `backend/cubeplex/api/schemas/mcp.py`. Add the entry shape and the two new fields on `MCPConnectorInstallOut`:

```python
class MCPToolEntry(BaseModel):
    """Single entry from MCPConnectorInstall.tools_cache."""

    name: str
    description: str | None = None
    input_schema: dict[str, Any] | None = None


CitationConfigJSON = dict[str, Any]  # opaque shape; agent runtime validates


class MCPConnectorInstallOut(BaseModel):
    install_id: str
    template_id: str | None
    install_scope: Literal["org", "workspace"]
    workspace_id: str | None
    name: str
    server_url: str
    transport: str
    auth_method: Literal["oauth", "static", "none"]
    default_credential_policy: Literal["org", "workspace", "user", "none"]
    auth_status: str
    discovery_status: str
    install_state: str
    tool_count: int
    tools: list[MCPToolEntry]  # NEW
    tool_citations: dict[str, CitationConfigJSON] | None  # NEW, admin-only
    last_error: str | None
    auto_enroll_new_workspaces: bool
```

- [ ] **Step 4: Modify `_install_to_out` to populate both**

Edit `backend/cubeplex/api/routes/v1/admin_mcp.py`. The helper currently
returns `tool_count=len(install.tools_cache or [])`. Extend:

```python
def _install_to_out(
    install: MCPConnectorInstall,
    *,
    include_tool_citations: bool = False,
) -> MCPConnectorInstallOut:
    tools_cache = install.tools_cache or []
    return MCPConnectorInstallOut(
        # ...all existing fields unchanged...
        tool_count=len(tools_cache),
        tools=[
            MCPToolEntry(
                name=str(t.get("name", "")),
                description=t.get("description"),
                input_schema=t.get("input_schema"),
            )
            for t in tools_cache
            if isinstance(t, dict) and t.get("name")
        ],
        tool_citations=(install.tool_citations or {}) if include_tool_citations else None,
    )
```

Every existing call site (admin list, admin get, admin patch,
admin create, ws_mcp callers) needs to decide whether to pass
`include_tool_citations=True`. Rule from the spec §3.1: only the
admin routes pass `True`. Update the 4 admin sites + audit
ws_mcp's imports of `_install_to_out` and leave them at the
default `False`.

- [ ] **Step 5: Run test to verify pass**

```bash
cd backend && uv run pytest tests/e2e/test_mcp_restore_lost_ui.py -v --no-cov -k tools_and_tool_citations
```

Expected: PASS.

- [ ] **Step 6: Sync the frontend types**

Edit `frontend/packages/core/src/types/mcp.ts`. Add to `MCPConnectorInstall`:

```ts
export interface MCPToolEntry {
  name: string
  description: string | null
  input_schema: Record<string, unknown> | null
}

export interface MCPConnectorInstall {
  // ...existing...
  tools: MCPToolEntry[]
  tool_citations: Record<string, CitationConfigJSON> | null
}

export type CitationConfigJSON = {
  content_type: string
  source_type: string
  content_field: string | null
  mapping: Record<string, string>
}
```

Run:

```bash
cd frontend && pnpm --filter @cubeplex/core build && pnpm --filter @cubeplex/core type-check
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/api/schemas/mcp.py \
        backend/cubeplex/api/routes/v1/admin_mcp.py \
        backend/tests/e2e/test_mcp_restore_lost_ui.py \
        frontend/packages/core/src/types/mcp.ts
git commit -m "feat(mcp/dto): expose tools + tool_citations on install DTO"
```

---

## Task 2: Discovery service module

**Files:**
- Create: `backend/cubeplex/services/mcp_discovery.py`
- Modify: `backend/cubeplex/mcp/exceptions.py`
- Test: `backend/tests/e2e/test_mcp_restore_lost_ui.py` (extend)

- [ ] **Step 1: Write failing test**

Append to `backend/tests/e2e/test_mcp_restore_lost_ui.py`:

```python
async def test_discover_tools_for_install_writes_tools_cache(
    db_session_maker,
    seed_org_workspace_user,
    monkeypatch,
) -> None:
    """Discovery service should fetch tools via cubepi and persist
    the result into install.tools_cache / .discovery_status."""
    org_id, _ws_id, user_id = seed_org_workspace_user
    from cubeplex.services.mcp_discovery import discover_tools_for_install
    from cubeplex.models.mcp import MCPConnectorInstall
    from cubeplex.mcp._constants import server_url_hash

    async with db_session_maker() as session:
        install = MCPConnectorInstall(
            org_id=org_id,
            template_id=None,
            install_scope="org",
            name="disc-test",
            server_url="https://disc.example.com/mcp",
            server_url_hash=server_url_hash("https://disc.example.com/mcp"),
            transport="streamable_http",
            auth_method="none",
            default_credential_policy="none",
            auth_status="not_required",
            install_state="active",
            tools_cache=[],
            tool_citations={},
            created_by_user_id=user_id,
        )
        session.add(install)
        await session.commit()
        await session.refresh(install)
        install_id = install.id

    # Stub the cubepi helper used inside discover_tools_for_install.
    from cubepi.agent.types import AgentTool
    async def fake_load(*args, **kwargs):
        return [
            AgentTool(name="ping", description="say hi", input_schema={"type": "object"}, fn=None),  # type: ignore
            AgentTool(name="pong", description="say bye", input_schema={"type": "object"}, fn=None),  # type: ignore
        ]
    monkeypatch.setattr("cubeplex.services.mcp_discovery.load_mcp_tools_http", fake_load)

    async with db_session_maker() as session:
        from cubeplex.credentials.dependencies import build_credential_service
        from cubeplex.credentials.encryption import get_test_backend
        from cubeplex.mcp.dependencies import build_user_token_signer
        cred_service = build_credential_service(
            session, get_test_backend(), org_id=org_id, actor_user_id=user_id
        )
        # For auth_method='none' the signer mints an identity token and
        # token_mgr is unused — test can use real instances. For
        # auth_method='static'/'oauth' tests, monkeypatch cred_service
        # / token_mgr as needed.
        signer = build_user_token_signer()
        token_mgr = None  # type: ignore[assignment]
        result = await discover_tools_for_install(
            install_id=install_id,
            workspace_id=None,
            actor_user_id=user_id,
            session=session,
            cred_service=cred_service,
            signer=signer,
            token_mgr=token_mgr,  # type: ignore[arg-type]
        )
        assert result.discovery_status == "ok"
        assert result.tool_count == 2
        names = sorted(t["name"] for t in result.tools_cache_raw)
        assert names == ["ping", "pong"]
        assert result.last_error is None
```

- [ ] **Step 2: Run to verify failure**

```bash
cd backend && uv run pytest tests/e2e/test_mcp_restore_lost_ui.py::test_discover_tools_for_install_writes_tools_cache -v --no-cov
```

Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the discovery service**

Create `backend/cubeplex/services/mcp_discovery.py`:

```python
"""MCP tool discovery for the restore-lost-UI Refresh tools flow.

Replaces the legacy `cubepi_admin_refresh.py` that was deleted in
commit 243e6396. Reuses the runtime path's `load_mcp_tools_http`
cubepi helper and writes the result into the install row's
tools_cache / discovery_status / last_error fields.

Per spec §3.2:
- Caller-grant policy: use the effective grant resolved by the
  install's policy (org / workspace / user). Mirrors agent runtime;
  no cross-scope fallback.
- 30-second cubepi timeout (re-used from install.timeout default).
- On exception: catch and persist `discovery_status='error' +
  last_error=str(exc)`; do NOT raise — return the result with
  status='error' so the route layer can decide.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, cast

from cubepi.mcp import load_mcp_tools_http
from cubepi.mcp.types import MCPTransport
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.mcp.effective import MCPEffectiveConnectorService
from cubeplex.mcp.exceptions import MCPDiscoveryFailed
from cubeplex.models.mcp import MCPConnectorInstall
from cubeplex.repositories.mcp import (
    MCPConnectorInstallRepository,
    MCPCredentialGrantRepository,
    MCPWorkspaceConnectorStateRepository,
    MCPConnectorTemplateRepository,
)
from cubeplex.services.credential import CredentialService

_DISCOVERY_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class DiscoveryResult:
    install_id: str
    discovery_status: str  # "ok" | "error"
    tool_count: int
    tools_cache_raw: list[dict[str, Any]]
    last_error: str | None


def _build_runtime_spec_for_discovery(install, grant):
    """Builds the MCPRuntimeConnectorSpec shape that
    _resolve_headers_from_spec expects without going through the
    full effective-state list (which the caller already computed).

    Field accounting (matches effective.py:MCPRuntimeConnectorSpec):
    - `grant_scope` has NO default — must be supplied. For
      `auth_method='none'` it's None; otherwise read from the grant.
    - `tool_citations` has NO default; pass `install.tool_citations`
      so the loader can wire citations even though discovery itself
      doesn't need them.
    - `grant` (the live MCPCredentialGrant row) MUST be copied through
      because OAuthTokenManager refresh paths mutate it via
      grant_repo.update; passing None for an OAuth install with a
      refresh credential breaks token rotation.
    - `oauth_client_config` carries `client_id` /
      `client_secret_credential_id` that the OAuth refresh needs;
      copy `install.oauth_client_config` so silent re-auth has the
      same context as the runtime.
    """
    from cubeplex.mcp.effective import MCPRuntimeConnectorSpec
    return MCPRuntimeConnectorSpec(
        install_id=install.id,
        name=install.name,
        server_url=install.server_url,
        transport=install.transport,
        auth_method=install.auth_method,
        grant_scope=(grant.grant_scope if grant is not None else None),
        credential_id=(grant.credential_id if grant is not None else None),
        refresh_credential_id=(grant.refresh_credential_id if grant is not None else None),
        tool_citations=dict(install.tool_citations or {}),
        tools_cache=list(install.tools_cache or []),
        headers=dict(install.headers or {}),
        timeout=install.timeout,
        template_id=install.template_id,
        org_id=install.org_id,
        workspace_id=install.workspace_id or "",
        grant=grant,
        oauth_client_config=dict(install.oauth_client_config or {}),
    )


async def discover_tools_for_install(
    *,
    install_id: str,
    workspace_id: str | None,
    actor_user_id: str,
    session: AsyncSession,
    cred_service: CredentialService,
    signer: MCPUserTokenSigner,
    token_mgr: OAuthTokenManager,
) -> DiscoveryResult:
    """Routes inject `signer` and `token_mgr` via the existing
    `get_user_token_signer` and OAuth token-manager DI factories.
    Both are needed because `_resolve_headers_from_spec` mints an
    identity token for `auth_method='none'` installs and refreshes
    OAuth grants on call for `auth_method='oauth'` installs."""
    install_repo = MCPConnectorInstallRepository(session, org_id=cred_service._org_id)  # type: ignore[arg-type]
    install = await install_repo.get(install_id)
    if install is None:
        raise ValueError("connector_install_not_found")
    if install.install_state != "active":
        raise ValueError("connector_install_not_active")

    # Resolve effective grant via the same path the agent runtime uses.
    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=install.org_id)
    grant_repo = MCPCredentialGrantRepository(session, org_id=install.org_id)
    template_repo = MCPConnectorTemplateRepository(session)
    effective_svc = MCPEffectiveConnectorService(
        template_repo=template_repo,
        install_repo=install_repo,
        state_repo=state_repo,
        grant_repo=grant_repo,
        org_id=install.org_id,
    )
    # The effective service produces an MCPEffectiveConnectorDTO whose
    # `.usable=False` carries the same reason codes the routes surface.
    if workspace_id is None and install.install_scope == "workspace":
        workspace_id = install.workspace_id
    if workspace_id is None and install.default_credential_policy in {"workspace", "user"}:
        raise ValueError("workspace_id_required_for_scoped_policy")

    grant = None
    if workspace_id is not None:
        dtos = await effective_svc.list_for_workspace_user(
            workspace_id, actor_user_id, include_unusable=True,
        )
        dto = next((d for d in dtos if d.install.id == install_id), None)
        if dto is None:
            raise ValueError("connector_install_not_found")
        usable = dto.usable
        reason = dto.reason
        grant = dto.grant  # MCPCredentialGrant | None from effective DTO
    else:
        # Org-policy install, no workspace context needed.
        grant = await grant_repo.get_org_grant(install_id)
        usable = install.auth_method == "none" or (
            grant is not None and grant.grant_status == "valid"
        )
        reason = "usable" if usable else "missing_org_grant"

    if not usable:
        raise MCPDiscoveryFailed(f"connector_not_usable:{reason}")

    # Resolve auth headers from the actual grant the agent runtime
    # would use. Just forwarding install.headers leaves out the
    # Bearer token / static credential / OAuth access token that
    # private MCP servers require. The runtime calls
    # `cubeplex.mcp.cubepi_runtime._resolve_headers_from_spec` for
    # this; the discovery service must too. Build an
    # MCPRuntimeConnectorSpec from the install + chosen grant and
    # pass it through. The function returns `None` when a credential
    # is required but cannot be resolved (e.g. credential_id missing)
    # — treat that as a usability error.
    from cubeplex.mcp.cubepi_runtime import (
        MCPRuntimeConnectorSpec,
        _resolve_headers_from_spec,
    )

    spec = _build_runtime_spec_for_discovery(install=install, grant=grant)
    # `grant` was set above by the effective DTO (for workspace/user
    # policy) or `grant_repo.get_org_grant(install_id)` (for org
    # policy). For `auth_method='none'` it's None and the resolver
    # mints an identity token.
    headers = await _resolve_headers_from_spec(
        spec=spec,
        workspace_id=workspace_id or install.workspace_id or "",
        org_id=install.org_id,
        user_id=actor_user_id,
        cred_service=cred_service,
        signer=signer,           # MCPUserTokenSigner injected via DI
        token_manager=token_mgr, # OAuthTokenManager injected via DI
        grant_repo=grant_repo,
    )
    if headers is None:
        install.discovery_status = "error"
        install.last_error = "Auth header resolution failed"
        await install_repo.update(install)
        return DiscoveryResult(
            install_id=install_id,
            discovery_status="error",
            tool_count=0,
            tools_cache_raw=list(install.tools_cache or []),
            last_error=install.last_error,
        )

    try:
        tools = await asyncio.wait_for(
            load_mcp_tools_http(
                install.server_url,
                headers=headers or None,
                timeout=install.timeout,
                transport=cast(MCPTransport, install.transport),
            ),
            timeout=_DISCOVERY_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("MCP discovery failed for %s: %s", install_id, exc)
        install.discovery_status = "error"
        install.last_error = str(exc)[:2048]
        await install_repo.update(install)
        return DiscoveryResult(
            install_id=install_id,
            discovery_status="error",
            tool_count=0,
            tools_cache_raw=list(install.tools_cache or []),
            last_error=install.last_error,
        )

    tools_cache_raw: list[dict[str, Any]] = [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in tools
    ]
    install.tools_cache = tools_cache_raw
    install.discovery_status = "ok"
    install.last_error = None
    await install_repo.update(install)
    return DiscoveryResult(
        install_id=install_id,
        discovery_status="ok",
        tool_count=len(tools),
        tools_cache_raw=tools_cache_raw,
        last_error=None,
    )
```

- [ ] **Step 4: Add the exception class**

Edit `backend/cubeplex/mcp/exceptions.py` — add at the bottom:

```python
class MCPDiscoveryFailed(RuntimeError):
    """Raised when refresh-discovery cannot resolve a usable grant."""


class MCPInvokeFailed(RuntimeError):
    """Raised when Try It cannot resolve a usable grant or the tool errors."""


class MCPInvokeRateLimited(RuntimeError):
    """Raised when the Try It rate limit is exceeded."""
```

- [ ] **Step 5: Run test to verify pass**

```bash
cd backend && uv run pytest tests/e2e/test_mcp_restore_lost_ui.py::test_discover_tools_for_install_writes_tools_cache -v --no-cov
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/services/mcp_discovery.py \
        backend/cubeplex/mcp/exceptions.py \
        backend/tests/e2e/test_mcp_restore_lost_ui.py
git commit -m "feat(mcp/discovery): add discover_tools_for_install service"
```

---

## Task 3: Refresh-discovery routes (admin + ws)

**Files:**
- Modify: `backend/cubeplex/api/schemas/mcp.py`
- Modify: `backend/cubeplex/api/routes/v1/admin_mcp.py`
- Modify: `backend/cubeplex/api/routes/v1/ws_mcp.py`
- Test: `backend/tests/e2e/test_mcp_restore_lost_ui.py` (extend)

- [ ] **Step 1: Write failing test**

Append:

```python
async def test_admin_refresh_discovery_writes_install(
    admin_client,
    seeded_static_org_install_no_grant,
    monkeypatch,
) -> None:
    client, _ws = admin_client
    install_id = seeded_static_org_install_no_grant

    from cubepi.agent.types import AgentTool
    async def fake_load(*args, **kwargs):
        return [AgentTool(name="ping", description=None, input_schema=None, fn=None)]  # type: ignore
    monkeypatch.setattr("cubeplex.services.mcp_discovery.load_mcp_tools_http", fake_load)

    # No grant exists yet, but auth_method='none' → usable.
    res = await client.post(f"/api/v1/admin/mcp/installs/{install_id}/refresh-discovery", json={})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["discovery_status"] == "ok"
    assert body["tool_count"] == 1
    assert body["tools"][0]["name"] == "ping"


async def test_admin_refresh_discovery_requires_workspace_id_for_scoped_policy(
    admin_client,
    seeded_oauth_user_policy_install,
) -> None:
    client, _ws = admin_client
    install_id = seeded_oauth_user_policy_install
    res = await client.post(f"/api/v1/admin/mcp/installs/{install_id}/refresh-discovery", json={})
    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"][-1] == "workspace_id"
```

(Add `seeded_static_org_install_no_grant` and
`seeded_oauth_user_policy_install` fixtures following the existing
seed patterns.)

- [ ] **Step 2: Run to verify failure**

```bash
cd backend && uv run pytest tests/e2e/test_mcp_restore_lost_ui.py -v --no-cov -k refresh_discovery
```

Expected: 2 FAIL — route does not exist.

- [ ] **Step 3: Add the schema**

Edit `backend/cubeplex/api/schemas/mcp.py`:

```python
class AdminInstallRefreshIn(BaseModel):
    workspace_id: str | None = None


class WsInstallRefreshIn(BaseModel):
    pass  # body empty; workspace comes from {ws} path param
```

- [ ] **Step 4: Add the admin route**

Edit `backend/cubeplex/api/routes/v1/admin_mcp.py`. Add:

```python
@router.post(
    "/installs/{install_id}/refresh-discovery",
    response_model=MCPConnectorInstallOut,
)
async def admin_refresh_discovery(
    install_id: str,
    body: AdminInstallRefreshIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
    signer: Annotated[MCPUserTokenSigner, Depends(get_user_token_signer)],
    token_mgr: Annotated[OAuthTokenManager, Depends(get_oauth_token_manager)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> MCPConnectorInstallOut:
    cred_service = build_credential_service(
        session, backend, org_id=ctx.org_id, actor_user_id=ctx.user.id,
    )
    install_repo = MCPConnectorInstallRepository(session, org_id=ctx.org_id)
    install = await install_repo.get(install_id)
    if install is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})
    needs_ws = install.default_credential_policy in {"workspace", "user"}
    if needs_ws and not body.workspace_id:
        raise HTTPException(
            422,
            detail=[
                {
                    "type": "value_error",
                    "loc": ["body", "workspace_id"],
                    "msg": "workspace_id_required_for_scoped_policy",
                }
            ],
        )
    try:
        await discover_tools_for_install(
            install_id=install_id,
            workspace_id=body.workspace_id,
            actor_user_id=ctx.user.id,
            session=session,
            cred_service=cred_service,
            signer=signer,
            token_mgr=token_mgr,
        )
    except ValueError as exc:
        raise HTTPException(400, detail={"code": str(exc)}) from exc
    refreshed = await install_repo.get(install_id)
    return _install_to_out(refreshed, include_tool_citations=True)
```

- [ ] **Step 5: Add the workspace route**

Edit `backend/cubeplex/api/routes/v1/ws_mcp.py`:

```python
@router.post(
    "/installs/{install_id}/refresh-discovery",
    response_model=MCPConnectorInstallOut,
)
async def ws_refresh_discovery(
    workspace_id: str,
    install_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    cred_service: Annotated[CredentialService, Depends(get_credential_service)],
    signer: Annotated[MCPUserTokenSigner, Depends(get_user_token_signer)],
    token_mgr: Annotated[OAuthTokenManager, Depends(get_oauth_token_manager)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
) -> MCPConnectorInstallOut:
    install_repo = MCPConnectorInstallRepository(session, org_id=ctx.org_id)
    try:
        await discover_tools_for_install(
            install_id=install_id,
            workspace_id=workspace_id,
            actor_user_id=ctx.user.id,
            session=session,
            cred_service=cred_service,
            signer=signer,
            token_mgr=token_mgr,
        )
    except ValueError as exc:
        raise HTTPException(400, detail={"code": str(exc)}) from exc
    refreshed = await install_repo.get(install_id)
    return _install_to_out(refreshed)
```

- [ ] **Step 6: Run tests to verify pass**

```bash
cd backend && uv run pytest tests/e2e/test_mcp_restore_lost_ui.py -v --no-cov -k refresh_discovery
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/api/schemas/mcp.py \
        backend/cubeplex/api/routes/v1/admin_mcp.py \
        backend/cubeplex/api/routes/v1/ws_mcp.py \
        backend/tests/e2e/test_mcp_restore_lost_ui.py
git commit -m "feat(mcp/refresh): wire refresh-discovery on admin + ws routes"
```

---

## Task 4: Test connection route

**Files:**
- Modify: `backend/cubeplex/api/schemas/mcp.py`
- Modify: `backend/cubeplex/api/routes/v1/admin_mcp.py`
- Test: `backend/tests/e2e/test_mcp_restore_lost_ui.py` (extend)

- [ ] **Step 1: Write failing test**

```python
async def test_admin_test_connection_returns_tool_count(
    admin_client,
    monkeypatch,
) -> None:
    client, _ws = admin_client
    from cubepi.agent.types import AgentTool
    async def fake_load(*args, **kwargs):
        return [AgentTool(name="a", description=None, input_schema=None, fn=None),  # type: ignore
                AgentTool(name="b", description=None, input_schema=None, fn=None)]  # type: ignore
    monkeypatch.setattr("cubeplex.api.routes.v1.admin_mcp.load_mcp_tools_http", fake_load)

    res = await client.post(
        "/api/v1/admin/mcp/test-connection",
        json={
            "server_url": "https://probe.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body == {"ok": True, "tool_count": 2}


async def test_admin_test_connection_rejects_static_plaintext_with_none_auth(
    admin_client,
) -> None:
    client, _ws = admin_client
    res = await client.post(
        "/api/v1/admin/mcp/test-connection",
        json={
            "server_url": "https://probe.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "credential_plaintext": "should-not-be-here",
        },
    )
    assert res.status_code == 422
```

- [ ] **Step 2: Run to verify failure**

`uv run pytest tests/e2e/test_mcp_restore_lost_ui.py -k test_connection -v --no-cov` → FAIL.

- [ ] **Step 3: Add schema**

```python
class TestConnectionIn(BaseModel):
    server_url: str
    transport: Literal["streamable_http", "sse"]
    auth_method: Literal["oauth", "static", "none"]
    credential_plaintext: str | None = None
    headers: dict[str, str] | None = None

    @model_validator(mode="after")
    def _validate_plaintext_only_with_static(self) -> "TestConnectionIn":
        if self.credential_plaintext is not None and self.auth_method != "static":
            raise ValueError("credential_plaintext only valid with auth_method='static'")
        return self


class TestConnectionOut(BaseModel):
    ok: bool
    tool_count: int = 0
    error_code: str | None = None
    error_message: str | None = None
```

- [ ] **Step 4: Add route**

In `admin_mcp.py`:

```python
import asyncio
from typing import cast
from cubepi.mcp import load_mcp_tools_http
from cubepi.mcp.types import MCPTransport

_TEST_CONNECTION_TIMEOUT = 10.0


@router.post("/test-connection", response_model=TestConnectionOut)
async def admin_test_connection(
    body: TestConnectionIn,
    _ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> TestConnectionOut:
    headers = dict(body.headers or {})
    if body.auth_method == "static" and body.credential_plaintext:
        headers.setdefault("Authorization", f"Bearer {body.credential_plaintext}")
    try:
        tools = await asyncio.wait_for(
            load_mcp_tools_http(
                body.server_url,
                headers=headers or None,
                timeout=_TEST_CONNECTION_TIMEOUT,
                transport=cast(MCPTransport, body.transport),
            ),
            timeout=_TEST_CONNECTION_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        return TestConnectionOut(
            ok=False,
            tool_count=0,
            error_code=type(exc).__name__,
            error_message=str(exc)[:256],
        )
    return TestConnectionOut(ok=True, tool_count=len(tools))
```

- [ ] **Step 5: Run test to verify pass**

PASS for both tests.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(mcp/test-connection): add POST /admin/mcp/test-connection"
```

---

## Task 5: Custom connector creation

**Files:**
- Modify: `backend/cubeplex/api/schemas/mcp.py`
- Modify: `backend/cubeplex/api/routes/v1/admin_mcp.py`
- Modify: `backend/cubeplex/services/mcp_installs.py`
- Test: `backend/tests/e2e/test_mcp_restore_lost_ui.py` (extend)

- [ ] **Step 1: Write failing test**

```python
async def test_admin_create_custom_install_for_org(admin_client) -> None:
    client, _ws = admin_client
    res = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": None,
            "install_scope": "org",
            "name": "My internal MCP",
            "server_url": "https://internal.corp/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
            "auto_enable": {"mode": "none"},
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["template_id"] is None
    assert body["name"] == "My internal MCP"


async def test_admin_create_custom_install_rejects_credential_plaintext_with_scoped_policy(
    admin_client,
) -> None:
    client, _ws = admin_client
    res = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": None,
            "install_scope": "org",
            "name": "x",
            "server_url": "https://x.com/mcp",
            "transport": "streamable_http",
            "auth_method": "static",
            "default_credential_policy": "user",
            "auto_enable": {"mode": "none"},
            "credential_plaintext": "should-fail",
        },
    )
    assert res.status_code == 422
    assert "credential_plaintext_only_valid_for_org_policy" in res.text
```

- [ ] **Step 2: Run to verify failure**

FAIL — current `create_admin_install` requires `template_id`.

- [ ] **Step 3: Extend the request schema**

In `schemas/mcp.py`, change `AdminCreateInstallIn`:

```python
class AdminCreateInstallIn(BaseModel):
    template_id: str | None
    install_scope: Literal["org"] = "org"

    # Custom-install required when template_id is None:
    name: str | None = None
    server_url: str | None = None
    transport: Literal["streamable_http", "sse"] | None = None

    auth_method: Literal["oauth", "static", "none"]
    default_credential_policy: Literal["org", "workspace", "user", "none"]
    auto_enable: AutoEnableIn = AutoEnableIn(mode="none")
    headers: dict[str, str] | None = None

    # Org-policy static one-shot:
    credential_plaintext: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> "AdminCreateInstallIn":
        if self.template_id is None:
            if not (self.name and self.server_url and self.transport):
                raise ValueError("name/server_url/transport required for custom installs")
        if self.credential_plaintext is not None:
            if self.auth_method != "static":
                raise ValueError("credential_plaintext only valid with auth_method='static'")
            if self.default_credential_policy != "org":
                raise ValueError("credential_plaintext_only_valid_for_org_policy")
        return self
```

- [ ] **Step 4: Add the service method**

In `services/mcp_installs.py`:

```python
async def create_custom_install_for_org(
    self,
    *,
    name: str,
    server_url: str,
    transport: str,
    auth_method: str,
    default_credential_policy: str,
    headers: dict[str, str] | None,
    distribution: dict[str, Any],
) -> MCPConnectorInstall:
    """Custom (no template) install at install_scope='org'.

    Mirrors create_from_template_for_org but skips the template
    lookup and uses the user-supplied name / URL / transport.
    Uniqueness is enforced by the existing partial unique index on
    (org_id, server_url_hash) filtered by install_state='active'.
    """
    from cubeplex.mcp._constants import server_url_hash as _hash

    defaults = install_defaults_for_auth_method(auth_method, default_credential_policy)
    install = MCPConnectorInstall(
        org_id=self._org_id,
        template_id=None,
        install_scope="org",
        workspace_id=None,
        name=name,
        server_url=server_url,
        server_url_hash=_hash(server_url),
        transport=transport,
        auth_method=auth_method,
        default_credential_policy=defaults.credential_policy,
        auth_status=defaults.auth_status,
        install_state="active",
        headers=headers or {},
        tools_cache=[],
        tool_citations={},
        created_by_user_id=self._actor_user_id,
        auto_enroll_new_workspaces=distribution.get("mode") == "all",
    )
    saved = await self._install_repo.add(install)
    await self._apply_distribution_for_org(install=saved, distribution=distribution)
    return saved
```

(`_apply_distribution_for_org` is the existing fan-out helper inside
`create_from_template_for_org`. If it's inline today, refactor it
out into a private method before this task's commit.)

- [ ] **Step 5: Wire the route**

In `admin_mcp.py::create_admin_install`, branch on `template_id`:

```python
if body.template_id is None:
    try:
        install = await svc.create_custom_install_for_org(
            name=body.name,
            server_url=body.server_url,
            transport=body.transport,
            auth_method=body.auth_method,
            default_credential_policy=body.default_credential_policy,
            headers=body.headers,
            distribution=body.auto_enable.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(400, detail={"code": str(exc)}) from exc
else:
    # ...existing template-install branch unchanged...

# Org-policy static one-shot grant:
if body.credential_plaintext is not None:
    await svc.create_static_grant(
        install_id=install.id,
        grant_scope="org",
        plaintext=body.credential_plaintext,
    )

# Audit etc. unchanged.
```

- [ ] **Step 6: Run tests to verify pass**

PASS.

- [ ] **Step 7: Commit**

```bash
git commit -m "feat(mcp/custom): allow template_id=None custom installs on admin route"
```

---

## Task 6: Promote ws → org

**Files:**
- Modify: `backend/cubeplex/api/schemas/mcp.py`
- Modify: `backend/cubeplex/api/routes/v1/admin_mcp.py`
- Modify: `backend/cubeplex/services/mcp_installs.py`
- Test: `backend/tests/e2e/test_mcp_restore_lost_ui.py` (extend)

- [ ] **Step 1: Write failing test**

```python
async def test_promote_install_writes_org_scope_and_excludes_source(
    admin_client,
    seeded_static_workspace_install_with_state,
    seed_extra_workspace,
) -> None:
    """Promote a workspace install to org with mode='all' must:
    - flip install_scope to 'org'
    - clear install.workspace_id
    - upsert state rows in OTHER workspaces
    - NOT overwrite the source workspace's existing state row
    - set auto_enroll_new_workspaces=true
    """
    client, _ws = admin_client
    install_id, source_ws, source_state_policy = seeded_static_workspace_install_with_state
    other_ws = seed_extra_workspace

    res = await client.post(
        f"/api/v1/admin/mcp/installs/{install_id}/promote-to-org",
        json={"distribution": {"mode": "all"}},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["install_scope"] == "org"
    assert body["workspace_id"] is None
    assert body["auto_enroll_new_workspaces"] is True

    # Source row preserved:
    state_res = await client.get(
        f"/api/v1/ws/{source_ws}/mcp/connectors"
    )
    sources = [c for c in state_res.json()["items"] if c["install"]["install_id"] == install_id]
    assert len(sources) == 1
    assert sources[0]["workspace_state"]["credential_policy"] == source_state_policy

    # Other workspace got a state row:
    other_res = await client.get(f"/api/v1/ws/{other_ws}/mcp/connectors")
    others = [c for c in other_res.json()["items"] if c["install"]["install_id"] == install_id]
    assert len(others) == 1
```

- [ ] **Step 2: Add the schema**

```python
class PromoteInstallIn(BaseModel):
    distribution: AutoEnableIn = AutoEnableIn(mode="none")
```

- [ ] **Step 3: Add the service method**

```python
async def promote_workspace_install_to_org(
    self,
    *,
    install_id: str,
    distribution: dict[str, Any],
) -> MCPConnectorInstall:
    install = await self._install_repo.get(install_id)
    if install is None or install.org_id != self._org_id:
        raise ValueError("connector_install_not_found")
    if install.install_scope != "workspace":
        raise ValueError("install_already_org_scope")
    if install.install_state != "active":
        raise ValueError("connector_install_not_active")

    source_ws = install.workspace_id
    install.install_scope = "org"
    install.workspace_id = None
    mode = distribution.get("mode", "none")
    install.auto_enroll_new_workspaces = (mode == "all")
    saved = await self._install_repo.update(install)

    # Apply distribution but EXCLUDE source workspace from fan-out
    # (its existing state row is preserved untouched).
    workspace_ids = list(distribution.get("workspace_ids") or [])
    if mode == "selected":
        workspace_ids = [w for w in workspace_ids if w != source_ws]
        await self._upsert_state_for_workspaces(saved, workspace_ids)
    elif mode == "all":
        if self._workspace_repo is None:
            raise ValueError("promote_mode_all_requires_workspace_repo")
        all_workspaces = await self._workspace_repo.list_for_org(self._org_id)
        targets = [w.id for w in all_workspaces if w.id != source_ws]
        await self._upsert_state_for_workspaces(saved, targets)
    # mode='none' → no fan-out.

    return saved
```

(`_upsert_state_for_workspaces` is the existing private helper; if
not yet extracted, do so in this task's commit.)

- [ ] **Step 4: Add the route**

```python
@router.post(
    "/installs/{install_id}/promote-to-org",
    response_model=MCPConnectorInstallOut,
)
async def admin_promote_install_to_org(
    install_id: str,
    body: PromoteInstallIn,
    svc: Annotated[MCPConnectorInstallService, Depends(get_admin_install_service)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> MCPConnectorInstallOut:
    try:
        install = await svc.promote_workspace_install_to_org(
            install_id=install_id,
            distribution=body.distribution.model_dump(),
        )
    except ValueError as exc:
        code = str(exc)
        status_code = 409 if code == "install_already_org_scope" else 400
        raise HTTPException(status_code, detail={"code": code}) from exc
    await audit.record(
        event="mcp.install.promoted",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=install_id,
        details={"distribution_mode": body.distribution.mode},
    )
    return _install_to_out(install, include_tool_citations=True)
```

- [ ] **Step 5: Run tests, commit**

```bash
git commit -m "feat(mcp/promote): add ws→org promotion endpoint"
```

---

## Task 7: Citation editor route + audit-only frontend hook

**Files:**
- Modify: `backend/cubeplex/api/schemas/mcp.py`
- Modify: `backend/cubeplex/api/routes/v1/admin_mcp.py`
- Test: `backend/tests/e2e/test_mcp_restore_lost_ui.py` (extend)

- [ ] **Step 1: Write failing test**

```python
async def test_admin_upsert_tool_citation(
    admin_client,
    seeded_static_org_install_with_tools_cache,
) -> None:
    client, _ws = admin_client
    install_id = seeded_static_org_install_with_tools_cache
    res = await client.put(
        f"/api/v1/admin/mcp/installs/{install_id}/tool-citations",
        json={
            "tool_name": "pong",
            "config": {
                "content_type": "json",
                "source_type": "web",
                "content_field": None,
                "mapping": {"snippet": "summary"},
            },
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["tool_citations"]["pong"]["mapping"] == {"snippet": "summary"}


async def test_admin_clear_tool_citation_with_null_config(
    admin_client,
    seeded_static_org_install_with_tools_cache,
) -> None:
    client, _ws = admin_client
    install_id = seeded_static_org_install_with_tools_cache
    res = await client.put(
        f"/api/v1/admin/mcp/installs/{install_id}/tool-citations",
        json={"tool_name": "ping", "config": None},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "ping" not in body["tool_citations"]
```

- [ ] **Step 2: Schema**

```python
class ToolCitationUpsertIn(BaseModel):
    tool_name: str
    config: CitationConfigJSON | None
```

- [ ] **Step 3: Route**

```python
@router.put(
    "/installs/{install_id}/tool-citations",
    response_model=MCPConnectorInstallOut,
)
async def admin_upsert_tool_citation(
    install_id: str,
    body: ToolCitationUpsertIn,
    svc: Annotated[MCPConnectorInstallService, Depends(get_admin_install_service)],
    _ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> MCPConnectorInstallOut:
    install = await svc._install_repo.get(install_id)
    if install is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})
    current = dict(install.tool_citations or {})
    if body.config is None:
        current.pop(body.tool_name, None)
    else:
        current[body.tool_name] = body.config
    install.tool_citations = current
    saved = await svc._install_repo.update(install)
    return _install_to_out(saved, include_tool_citations=True)
```

- [ ] **Step 4: Run tests + commit**

```bash
git commit -m "feat(mcp/citations): add PUT /installs/{id}/tool-citations"
```

---

## Task 8: Try It routes (admin + ws)

**Files:**
- Modify: `backend/cubeplex/api/schemas/mcp.py`
- Modify: `backend/cubeplex/api/routes/v1/admin_mcp.py`
- Modify: `backend/cubeplex/api/routes/v1/ws_mcp.py`
- Test: `backend/tests/e2e/test_mcp_restore_lost_ui.py` (extend)

- [ ] **Step 1: Write failing test**

```python
async def test_ws_invoke_tool_returns_result(
    workspace_member_client,
    seeded_none_auth_install_with_tools_and_state,
    monkeypatch,
) -> None:
    client, ws_id = workspace_member_client
    install_id = seeded_none_auth_install_with_tools_and_state

    # Stub the cubepi invoke path.
    async def fake_invoke(server_url, tool_name, arguments, *, headers, timeout, transport):
        return {"echo": arguments, "tool": tool_name}
    monkeypatch.setattr("cubeplex.api.routes.v1.ws_mcp._invoke_tool_via_cubepi", fake_invoke)

    res = await client.post(
        f"/api/v1/ws/{ws_id}/mcp/installs/{install_id}/tools/ping/invoke",
        json={"arguments": {"x": 1}},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["result"]["echo"] == {"x": 1}
    assert "duration_ms" in body


async def test_admin_invoke_requires_workspace_id_for_scoped_policy(
    admin_client,
    seeded_oauth_user_policy_install,
) -> None:
    client, _ws = admin_client
    install_id = seeded_oauth_user_policy_install
    res = await client.post(
        f"/api/v1/admin/mcp/installs/{install_id}/tools/foo/invoke",
        json={"arguments": {}},
    )
    assert res.status_code == 422
```

- [ ] **Step 2: Schema**

```python
class AdminInstallInvokeIn(BaseModel):
    workspace_id: str | None = None
    arguments: dict[str, Any]


class WsInstallInvokeIn(BaseModel):
    arguments: dict[str, Any]


class ToolInvokeOut(BaseModel):
    ok: bool
    result: Any | None = None
    error: str | None = None
    duration_ms: int
```

- [ ] **Step 3: Add the helper used by both routes**

In `backend/cubeplex/api/routes/v1/ws_mcp.py` (and import from
admin_mcp.py):

```python
async def _invoke_tool_via_cubepi(
    server_url: str, tool_name: str, arguments: dict, *,
    headers: dict | None, timeout: float, transport: str,
) -> Any:
    """Thin wrapper for unit-test monkeypatching. In prod, uses
    cubepi.mcp.invoke_mcp_tool_http."""
    from cubepi.mcp import invoke_mcp_tool_http
    return await invoke_mcp_tool_http(
        server_url=server_url,
        tool_name=tool_name,
        arguments=arguments,
        headers=headers,
        timeout=timeout,
        transport=transport,
    )
```

(If `invoke_mcp_tool_http` does not exist in cubepi yet, add a
shim that constructs the cubepi MCP client + calls `.invoke()`.)

- [ ] **Step 4: Workspace route**

```python
from slowapi.util import get_remote_address
from cubeplex.api.middleware.rate_limit import limiter
from cubeplex.auth.dependencies import current_active_user
from cubeplex.models import User

# Limiter key: read the authenticated user id from a context var
# set by a dependency rather than from req.state.user_id. The
# request.state.user_id field is populated by UserIdentityMiddleware
# from the client-controlled X-User-ID header / cubeplex_user_id
# cookie — an authenticated client could rotate either to dodge
# their per-user bucket. Key on the JWT-verified User row instead.
from contextvars import ContextVar
_INVOKE_USER_ID: ContextVar[str | None] = ContextVar("_INVOKE_USER_ID", default=None)


def _set_invoke_user_id(user: User = Depends(current_active_user)) -> User:
    _INVOKE_USER_ID.set(user.id)
    return user


def _invoke_rate_key(_req: Request) -> str:
    # Called by slowapi to derive the bucket key. The dep above runs
    # BEFORE the route body, so the contextvar is set by the time
    # slowapi calls this.
    return _INVOKE_USER_ID.get() or "anonymous"


@router.post(
    "/installs/{install_id}/tools/{tool_name}/invoke",
    response_model=ToolInvokeOut,
)
@limiter.limit("30/minute", key_func=_invoke_rate_key)
async def ws_invoke_tool(
    request: Request,
    workspace_id: str,
    install_id: str,
    tool_name: str,
    body: WsInstallInvokeIn,
    svc: Annotated[MCPConnectorInstallService, Depends(get_ws_install_service)],
    effective_svc: Annotated[MCPEffectiveConnectorService, Depends(get_ws_effective_service)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    cred_service: Annotated[CredentialService, Depends(get_credential_service)],
    signer: Annotated[MCPUserTokenSigner, Depends(get_user_token_signer)],
    token_mgr: Annotated[OAuthTokenManager, Depends(get_oauth_token_manager)],
    _rate_key_user: Annotated[User, Depends(_set_invoke_user_id)],
    audit: Annotated[AuditSink, Depends(get_audit_sink)],
) -> ToolInvokeOut:
    # Build a workspace-scoped grant repo directly. `get_grant_repo`
    # in mcp/dependencies.py depends on `get_admin_request_context`
    # (org-admin gated), so wiring it here would 403 ordinary
    # workspace members — but the spec marks this route as
    # accessible to any workspace member (`require_member`). The
    # grant repo only needs (session, org_id) and we already have
    # both from the request context.
    grant_repo = MCPCredentialGrantRepository(session, org_id=ctx.org_id)
    install = await svc._install_repo.get(install_id)
    if install is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})
    dtos = await effective_svc.list_for_workspace_user(
        workspace_id, ctx.user.id, include_unusable=True,
    )
    dto = next((d for d in dtos if d.install.id == install_id), None)
    if dto is None or not dto.usable:
        raise HTTPException(400, detail={"code": "connector_not_usable",
                                          "reason": dto.reason if dto else "missing"})
    # Same credential resolution as discovery (Task 2): build the
    # runtime spec from (install, dto.grant) and ask
    # _resolve_headers_from_spec for the Authorization header.
    # Without this, Try It against a static / OAuth connector sends
    # no Bearer token even though dto.usable=True, and the invoke
    # fails for any private MCP server.
    spec = _build_runtime_spec_for_discovery(install=install, grant=dto.grant)
    # Wrap credential resolution + the None-result branch in try/except
    # so that vault-read failures (deleted credential, wrong kind,
    # failed OAuth refresh) come back as a clean ToolInvokeOut with
    # ok=False + a documented `error` string, NOT a 500. Audit the
    # failure too so it traces in the same channel as a successful
    # invoke. The cubepi-invoke try/except below catches tool-side
    # failures separately.
    started = time.perf_counter()
    try:
        headers = await _resolve_headers_from_spec(
            spec=spec,
            workspace_id=workspace_id,
            org_id=ctx.org_id,
            user_id=ctx.user.id,
            cred_service=cred_service,
            signer=signer,
            token_manager=token_mgr,
            grant_repo=grant_repo,
        )
        if headers is None:
            raise RuntimeError("credential_resolution_returned_none")
    except Exception as exc:  # noqa: BLE001
        duration = int((time.perf_counter() - started) * 1000)
        await audit.record(
            event="mcp.tool.invoked",
            actor_user_id=ctx.user.id,
            org_id=ctx.org_id,
            target_id=install_id,
            details={
                "tool_name": tool_name,
                "workspace_id": workspace_id,
                "ok": False,
                "error_kind": "credential_resolution_failed",
            },
        )
        return ToolInvokeOut(
            ok=False,
            error=f"credential_resolution_failed: {exc}"[:512],
            duration_ms=duration,
        )
    try:
        result = await asyncio.wait_for(
            _invoke_tool_via_cubepi(
                install.server_url, tool_name, body.arguments,
                headers=headers or None,
                timeout=install.timeout,
                transport=install.transport,
            ),
            timeout=10.0,
        )
    except Exception as exc:  # noqa: BLE001
        duration = int((time.perf_counter() - started) * 1000)
        await audit.record(
            event="mcp.tool.invoked",
            actor_user_id=ctx.user.id,
            org_id=ctx.org_id,
            target_id=install_id,
            details={"tool_name": tool_name, "workspace_id": workspace_id, "ok": False},
        )
        return ToolInvokeOut(ok=False, error=str(exc)[:512], duration_ms=duration)
    duration = int((time.perf_counter() - started) * 1000)
    await audit.record(
        event="mcp.tool.invoked",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target_id=install_id,
        details={"tool_name": tool_name, "workspace_id": workspace_id, "ok": True},
    )
    return ToolInvokeOut(ok=True, result=result, duration_ms=duration)
```

- [ ] **Step 5: Admin route**

Same shape, with the workspace_id sourced from
`body.workspace_id` (REQUIRED if `install.default_credential_policy
∈ {workspace, user}`). 422 with code
`workspace_id_required_for_scoped_policy` if missing.

- [ ] **Step 6: Run tests + commit**

```bash
git commit -m "feat(mcp/invoke): add Try It routes (admin + ws) with rate limit"
```

---

## Task 9: Frontend — Tools tab + Refresh button + Error banner

**Files:**
- Create: `frontend/packages/web/lib/jsonSchemaTypes.ts`
- Create: `frontend/packages/web/components/mcp/detail/MasterDetailList.tsx`
- Create: `frontend/packages/web/components/mcp/detail/ServerErrorBanner.tsx`
- Create: `frontend/packages/web/components/mcp/detail/tools/SchemaParameterRow.tsx`
- Create: `frontend/packages/web/components/mcp/detail/tools/SchemaView.tsx`
- Create: `frontend/packages/web/components/mcp/detail/tools/JsonView.tsx`
- Create: `frontend/packages/web/components/mcp/detail/tools/TryItView.tsx`
- Create: `frontend/packages/web/components/mcp/detail/tools/ToolList.tsx`
- Create: `frontend/packages/web/components/mcp/detail/tools/ToolDetail.tsx`
- Create: `frontend/packages/web/components/mcp/detail/tools/ToolsPanel.tsx`
- Modify: `frontend/packages/web/components/mcp/MCPAdminDetailPanel.tsx`
- Modify: `frontend/packages/web/components/workspace-settings/McpPanel.tsx`
- Modify: `frontend/packages/core/src/api/mcp.ts`
- Modify: `frontend/packages/web/messages/{en,zh}.json`

This task is structurally a port of the deleted code in
`243e6396~1` plus new wiring. The reference implementations are
the deleted files — `git show 243e6396~1:<path>` retrieves each
one. Implementer should read them once for the layout shape,
then re-implement against the new four-layer types.

- [ ] **Step 1: Add JSON schema helpers**

Port `lib/jsonSchemaTypes.ts` from `git show
243e6396~1:frontend/packages/web/lib/jsonSchemaTypes.ts` (the
file's content is verbatim recoverable; small enough to commit
without further edits). Adds `SchemaNode`, `getProperties`,
`getRequired`.

- [ ] **Step 2: Build the leaf components**

Port `MasterDetailList.tsx`, `SchemaParameterRow.tsx`,
`SchemaView.tsx`, `JsonView.tsx`, `ServerErrorBanner.tsx` from
their deleted versions. Replace any `MCPServer` references with
the four-layer `MCPConnectorInstall` shape; the rest of the JSX
applies as-is.

- [ ] **Step 3: Build TryItView**

Port the deleted `TryItView.tsx`. Replace its old invoke path
with calls to the new core helpers:

```ts
import { adminInvokeTool, wsInvokeTool } from '@cubeplex/core'
// chosen by caller via a prop `surface: 'admin' | 'ws'`
```

Add the workspace picker dropdown when `surface === 'admin'` and
the connector's effective `required_grant_scope` ∈ {`workspace`,
`user`}.

- [ ] **Step 4: Build ToolList + ToolDetail + ToolsPanel**

Port the three files. Wire `ToolDetail`'s third sub-tab (Try It)
to the component from Step 3.

- [ ] **Step 5: Add core API helpers**

In `frontend/packages/core/src/api/mcp.ts`:

```ts
export async function adminRefreshDiscovery(
  client: ApiClient, installId: string, workspaceId?: string,
): Promise<MCPConnectorInstall> {
  const res = await client.post(
    `/api/v1/admin/mcp/installs/${installId}/refresh-discovery`,
    { workspace_id: workspaceId ?? null },
  )
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPConnectorInstall
}

export async function wsRefreshDiscovery(
  client: ApiClient, wsId: string, installId: string,
): Promise<MCPConnectorInstall> {
  const res = await client.post(
    `/api/v1/ws/${wsId}/mcp/installs/${installId}/refresh-discovery`,
    {},
  )
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPConnectorInstall
}

export async function adminInvokeTool(...) { /* mirror backend body */ }
export async function wsInvokeTool(...) { /* mirror backend body */ }
```

- [ ] **Step 6: Wire into MCPAdminDetailPanel + McpPanel**

`MCPAdminDetailPanel.tsx`:
- Add a `<Tabs>` value `tools` between Overview and Workspaces;
  mount `<ToolsPanel install={...} surface="admin" />`.
- Mount `<ServerErrorBanner install={...} onRetry={...}/>` above
  the existing title row when
  `install.discovery_status === 'error'`.
- Change the existing `handleRefresh` to call
  `adminRefreshDiscovery(client, installId, lensWsId)` (lens for
  scoped policies); on success, call `onRefresh()` to re-pull the
  list, then re-fetch the effective connector for the detail
  panel.

`workspace-settings/McpPanel.tsx` (`ConnectorDetail`):
- Add Tools tab + ErrorBanner same way.
- Refresh button calls `wsRefreshDiscovery(client, wsId, installId)`.

- [ ] **Step 7: i18n**

Add the `mcp.tools.*` / `mcp.errorBanner.*` / `mcp.tryIt.*` keys
in BOTH `en.json` and `zh.json`. Reference the deleted i18n
shape via `git show 243e6396~1:frontend/packages/web/messages/en.json`.

- [ ] **Step 8: Verify**

```bash
cd frontend && pnpm --filter web type-check && pnpm --filter web lint
```

- [ ] **Step 9: Commit**

```bash
git commit -m "feat(web/mcp): restore Tools tab + Refresh + Error banner"
```

---

## Task 10: Frontend — Custom install + Promote dialog + Citations tab

**Files:**
- Create: `frontend/packages/web/components/mcp/MCPCustomCreatePanel.tsx`
- Create: `frontend/packages/web/components/mcp/MCPPromoteDialog.tsx`
- Create: `frontend/packages/web/components/mcp/MCPCitationsTab.tsx`
- Create: `frontend/packages/web/components/mcp/MCPCitationEditor.tsx`
- Create: `frontend/packages/core/src/hooks/useOrgAdminFlag.ts`
- Modify: `frontend/packages/core/src/api/auth.ts` (MeResult.org_memberships)
- Modify: `frontend/packages/core/src/api/mcp.ts`
- Modify: `frontend/packages/web/components/mcp/MCPAdminDetailPanel.tsx`
- Modify: `frontend/packages/web/components/workspace-settings/McpPanel.tsx`
- Modify: `frontend/packages/web/app/admin/mcp/page.tsx`
- Modify: `frontend/packages/web/messages/{en,zh}.json`

- [ ] **Step 1: Add `useOrgAdminFlag` hook**

Verify `GET /api/v1/auth/me` returns `org_memberships` (an array
of `{org_id, role}`). If not, add it on the backend (separate
sub-step). Then in core:

```ts
// frontend/packages/core/src/hooks/useOrgAdminFlag.ts
import { useAuthStore } from '../stores/authStore'

export function useOrgAdminFlag(orgId: string | null | undefined): boolean {
  const memberships = useAuthStore(s => s.user?.org_memberships ?? [])
  if (!orgId) return false
  return memberships.some(
    m => m.org_id === orgId && (m.role === 'admin' || m.role === 'owner')
  )
}
```

- [ ] **Step 2: Port + adapt MCPCustomCreatePanel**

Port `git show 243e6396~1:frontend/packages/web/components/mcp/MCPCustomCreatePanel.tsx`.
Replace its old `MCPServerCreateAdminBody` shape with
`AdminCreateInstallIn` (the four-layer one):
- `name`, `server_url`, `transport`, `auth_method`,
  `default_credential_policy`, `auto_enable: { mode: 'none' }`,
  optional `credential_plaintext`, optional `headers`.
- Test-connection button calls `adminTestConnection(client,
  body)` and renders the result inline.

Mount it as a new entry in the admin templates sidebar under
"Connector templates": **+ Add custom connector** button →
opens the panel in `mode='custom_install'`.

- [ ] **Step 3: Port + adapt MCPPromoteDialog**

Port the deleted `MCPPromoteDialog.tsx`. Replace its old
`MCPServer` prop with `MCPConnectorInstall`. Body shape sent on
confirm: `{ distribution: { mode: 'all' | 'selected' | 'none',
workspace_ids?: string[] } }`.

Wire-up:
- In `MCPAdminDetailPanel`: add a `Promote to org-wide` menu item
  next to Uninstall when `install.install_scope === 'workspace'`
  AND `useOrgAdminFlag(install.org_id) === true`.
- In `McpPanel`'s `ConnectorDetail`: same menu item with same
  visibility logic.

- [ ] **Step 4: Build MCPCitationEditor + MCPCitationsTab**

Port the deleted `MCPCitationEditor.tsx` shape. Drop the
"peerMappings" arg until we add a cross-install peer lookup;
keep the JSON schema field-name picker.

`MCPCitationsTab` uses `MasterDetailList` over
`install.tools` with a "✓ Mapped" hint when
`install.tool_citations[tool.name]` is set. Selecting a tool
opens `MCPCitationEditor`. Save → `adminUpsertToolCitation(...)`.
Reset → same call with `config: null`.

Mount the tab in `MCPAdminDetailPanel` between Tools and
Workspaces. NOT in workspace settings (admin-only).

- [ ] **Step 5: i18n + verify + commit**

```bash
cd frontend && pnpm --filter web type-check && pnpm --filter web lint
git commit -m "feat(web/mcp): restore custom install / promote / citations UI"
```

---

## Task 11: E2E

**Files:**
- Create: `frontend/packages/web/__tests__/e2e/mcp/restore-lost-ui.spec.ts`

- [ ] **Step 1: Write scoped E2E**

```typescript
import { test, expect } from '@playwright/test'

// Lightweight: verifies UI mounts without runtime errors and core
// flows render. Heavier flow tests live in backend E2E.

test('Tools tab renders with empty state when no tools cached', async ({ page }) => {
  // ... register + install a none-auth template, navigate to settings ...
  await expect(page.getByText('Discovery has not run yet')).toBeVisible()
})

test('admin sees + Add custom connector entry', async ({ page }) => {
  // ... login as admin, go to /admin/mcp ...
  await expect(page.getByText('+ Add custom connector')).toBeVisible()
})

test('Promote menu hidden for non-org-admin members', async ({ page }) => {
  // ... seed a non-admin workspace member, go to workspace settings ...
  await expect(page.getByRole('menuitem', { name: /promote/i })).toHaveCount(0)
})
```

Heavier flows (full Test Connection success, real Promote
round-trip) are out of scope here — too much fixture setup for
playwright. Backend E2E covers the data paths.

- [ ] **Step 2: Commit**

```bash
git commit -m "test(web/mcp): E2E for restored UI surfaces"
```

---

## Task 12: Final sweep

- [ ] **Step 1: Confirm no dangling references**

```bash
grep -rn "MCPServer\b\|workspace_mcp_overrides\|MCPCatalogConnector" \
  backend/cubeplex/ frontend/packages/ 2>&1 | grep -v __pycache__
```

Expected: NO matches (the legacy types should be fully gone; if
any leak in, fix the relevant task).

- [ ] **Step 2: Backend full check**

```bash
cd backend && make check
```

Expected: PASS.

- [ ] **Step 3: Frontend full check**

```bash
cd frontend && pnpm --filter web type-check && pnpm --filter web lint && pnpm --filter web build
```

Expected: PASS.

- [ ] **Step 4: Run backend E2E**

```bash
cd backend && uv run pytest tests/e2e/test_mcp_restore_lost_ui.py tests/e2e/test_mcp_four_layer_routes.py tests/e2e/test_mcp_oauth_handoff.py -v --no-cov
```

Expected: PASS (the four-layer + oauth-handoff existing E2E must
not regress with the new DTO fields).

- [ ] **Step 5: No commit — verification only.**

If anything fails, fix in the relevant earlier task and re-run.

---

## Self-review

- §3.1 Tools tab → Task 9.
- §3.2 Refresh tools → Task 3 (backend) + Task 9 (frontend).
- §3.3 Test connection → Task 4 (backend) + Task 10 (frontend).
- §3.4 Error banner → Task 9.
- §3.5 Custom connector creation → Task 5 (backend) + Task 10 (frontend).
- §3.6 Promote ws→org → Task 6 (backend) + Task 10 (frontend).
- §3.7 Citation editor → Task 7 (backend) + Task 10 (frontend).
- §3.8 Try It → Task 8 (backend) + Task 9 (frontend).
- §4 Caller authority matrix → enforced per route (each task) +
  visibility gates per UI (Task 10's `useOrgAdminFlag`).
- §5 Back-end contract table → fully covered by Tasks 1-8.
- §6 Edge cases (advisory lock, 422 missing workspace_id,
  audit on Try It, no cross-scope fallback) → Tasks 2/3/6/8.

No placeholders, no "TBD", no missing types referenced across
tasks.
