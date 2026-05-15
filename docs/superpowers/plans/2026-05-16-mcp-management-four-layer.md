# MCP Management Four-Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the four-layer MCP management model so templates, installs, workspace state, and credential grants have separate product and runtime semantics.

**Architecture:** Add a backend effective-state service over the current tables first, then move runtime, APIs, and UI to consume that service. Keep legacy `catalog`/`override` URLs as compatibility wrappers while the product language changes to connector templates, installs, workspace state, and grants.

**Tech Stack:** FastAPI, SQLModel, Alembic, PostgreSQL, Redis, OAuthTokenManager, Next.js, Zustand, TypeScript, pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-05-15-mcp-management-four-layer-design.md`

**Conventions:**

- Run backend commands from `backend/`.
- Run frontend commands from `frontend/`.
- In this worktree, read `.worktree.env` before manual server checks.
- Keep one logical commit per task unless a task only updates this plan.
- Do not physically rename existing DB tables in this implementation pass.
- Preserve old endpoints until frontend and runtime have migrated.

---

## File Structure

**Backend — new files:**

- `backend/cubebox/mcp/effective.py` — four-layer domain types, pure state computation,
  DB-backed effective connector service, and runtime spec conversion.
- `backend/tests/unit/mcp/test_effective_state.py` — pure decision-table tests.
- `backend/tests/unit/mcp/test_effective_service.py` — service tests using fake rows/repos.
- `backend/tests/e2e/test_mcp_effective_connectors.py` — route and runtime-level coverage.

**Backend — modified files:**

- `backend/cubebox/models/mcp.py` — add `install_status` to separate install lifecycle
  from credential readiness.
- `backend/cubebox/repositories/mcp.py` — filter active installs and add active-list helpers.
- `backend/cubebox/services/mcp_catalog.py` — no-auth workspace install fix,
  `install_status` transitions, and reactivation behavior.
- `backend/cubebox/mcp/dependencies.py` — add `get_effective_connector_service`.
- `backend/cubebox/api/schemas/mcp.py` — effective connector response and patch schemas.
- `backend/cubebox/api/schemas/ws_settings.py` — tighten MCP credential mode literals.
- `backend/cubebox/api/routes/v1/ws_mcp.py` — add normalized workspace connector routes.
- `backend/cubebox/api/routes/v1/mcp_catalog.py` — expose template aliases while keeping
  catalog routes.
- `backend/cubebox/api/routes/v1/ws_settings.py` — delegate MCP settings to the
  effective service.
- `backend/cubebox/mcp/cubepi_discovery.py` — stop duplicating effective-state logic.
- `backend/cubebox/mcp/cubepi_runtime.py` — load runtime specs from the effective service.
- `backend/cubebox/streams/run_manager.py` — pass OAuth token manager into MCP runtime load.

**Frontend — modified files:**

- `frontend/packages/core/src/types/mcp.ts` — add template, install, state, grant,
  and effective connector types.
- `frontend/packages/core/src/api/mcp.ts` — add effective connector API helpers.
- `frontend/packages/core/src/api/workspace-settings.ts` — route MCP settings through
  the normalized connector endpoints.
- `frontend/packages/core/src/stores/workspaceSettingsStore.ts` — store effective connector
  state and update it after workspace-state patches.
- `frontend/packages/core/__tests__/api/mcp.test.ts` — API path and payload coverage.
- `frontend/packages/web/components/workspace-settings/McpPanel.tsx` — show four-layer
  semantics without exposing `override` as product language.
- `frontend/packages/web/components/mcp/MCPCatalogInstallPanel.tsx` — present catalog as
  connector templates.
- `frontend/packages/web/messages/en.json` and
  `frontend/packages/web/messages/zh.json` — terminology updates.

---

## Task 1: Effective State Domain Model

**Files:**

- Create: `backend/cubebox/mcp/effective.py`
- Create: `backend/tests/unit/mcp/test_effective_state.py`

- [ ] **Step 1: Write the pure state tests**

Create `backend/tests/unit/mcp/test_effective_state.py`:

```python
from cubebox.mcp.effective import (
    ConnectorInstallView,
    ConnectorTemplateView,
    CredentialGrantView,
    WorkspaceConnectorStateView,
    compute_effective_state,
)


def _install(
    *,
    auth_method: str = "static",
    credential_scope: str = "org",
    install_status: str = "active",
    origin: str = "org",
) -> ConnectorInstallView:
    return ConnectorInstallView(
        install_id="mcp-1",
        name="GitHub",
        server_url="https://github.example.com/mcp",
        transport="streamable_http",
        auth_method=auth_method,
        credential_scope=credential_scope,
        install_status=install_status,
        origin=origin,
        owner_workspace_id=None if origin == "org" else "ws-1",
        credential_id="cred-1" if credential_scope == "org" else None,
        headers={},
        tools_cache=[],
        tool_citations={},
    )


def _template(status: str = "active") -> ConnectorTemplateView:
    return ConnectorTemplateView(
        template_id="mctlg-1",
        slug="github",
        name="GitHub",
        provider="GitHub",
        status=status,
    )


def test_no_auth_connector_is_usable_without_grant() -> None:
    state = compute_effective_state(
        template=_template(),
        install=_install(auth_method="none", credential_scope="none"),
        workspace_state=WorkspaceConnectorStateView(enabled=True, credential_policy=None),
        grant=None,
    )

    assert state.usable is True
    assert state.reason == "usable"
    assert state.credential_policy == "none"
    assert state.credential_availability == "not_required"


def test_user_policy_requires_current_user_grant() -> None:
    state = compute_effective_state(
        template=_template(),
        install=_install(credential_scope="org"),
        workspace_state=WorkspaceConnectorStateView(enabled=True, credential_policy="user"),
        grant=None,
    )

    assert state.usable is False
    assert state.reason == "credential_missing"
    assert state.credential_policy == "user"
    assert state.credential_availability == "missing"


def test_available_user_grant_makes_user_policy_usable() -> None:
    state = compute_effective_state(
        template=_template(),
        install=_install(credential_scope="org"),
        workspace_state=WorkspaceConnectorStateView(enabled=True, credential_policy="user"),
        grant=CredentialGrantView(policy="user", available=True, source="user"),
    )

    assert state.usable is True
    assert state.reason == "usable"
    assert state.credential_source == "user"


def test_disabled_workspace_state_blocks_runtime() -> None:
    state = compute_effective_state(
        template=_template(),
        install=_install(credential_scope="org"),
        workspace_state=WorkspaceConnectorStateView(enabled=False, credential_policy=None),
        grant=CredentialGrantView(policy="org", available=True, source="org"),
    )

    assert state.usable is False
    assert state.reason == "workspace_disabled"


def test_inactive_template_blocks_catalog_backed_install() -> None:
    state = compute_effective_state(
        template=_template(status="disabled"),
        install=_install(credential_scope="org"),
        workspace_state=WorkspaceConnectorStateView(enabled=True, credential_policy=None),
        grant=CredentialGrantView(policy="org", available=True, source="org"),
    )

    assert state.usable is False
    assert state.reason == "template_inactive"


def test_deleted_install_blocks_runtime_before_credentials() -> None:
    state = compute_effective_state(
        template=_template(),
        install=_install(credential_scope="org", install_status="deleted"),
        workspace_state=WorkspaceConnectorStateView(enabled=True, credential_policy=None),
        grant=CredentialGrantView(policy="org", available=True, source="org"),
    )

    assert state.usable is False
    assert state.reason == "install_deleted"
```

- [ ] **Step 2: Run tests and confirm the missing module failure**

Run: `cd backend && uv run pytest -q tests/unit/mcp/test_effective_state.py`

Expected: FAIL with `ModuleNotFoundError: No module named 'cubebox.mcp.effective'`.

- [ ] **Step 3: Add the domain model and pure computation**

Create `backend/cubebox/mcp/effective.py`:

```python
"""Effective MCP connector state.

This module is the semantic boundary for the four-layer MCP model:
template, install, workspace state, and credential grant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, cast

AuthMethod = Literal["static", "oauth", "none"]
ConnectorOrigin = Literal["org", "workspace"]
CredentialPolicy = Literal["org", "workspace", "user", "none"]
CredentialAvailability = Literal["available", "missing", "not_required"]
CredentialSource = Literal["org", "workspace", "user"]
EffectiveReason = Literal[
    "usable",
    "template_inactive",
    "install_deleted",
    "workspace_disabled",
    "credential_missing",
    "unsupported_transport",
]

_VALID_CREDENTIAL_POLICIES: frozenset[str] = frozenset({"org", "workspace", "user", "none"})
_VALID_TRANSPORTS: frozenset[str] = frozenset({"sse", "streamable_http"})


@dataclass(frozen=True, slots=True)
class ConnectorTemplateView:
    template_id: str | None
    slug: str | None
    name: str
    provider: str | None
    status: str


@dataclass(frozen=True, slots=True)
class ConnectorInstallView:
    install_id: str
    name: str
    server_url: str
    transport: str
    auth_method: str
    credential_scope: str
    install_status: str
    origin: str
    owner_workspace_id: str | None
    credential_id: str | None
    headers: dict[str, str] = field(default_factory=dict)
    tools_cache: list[dict[str, Any]] = field(default_factory=list)
    tool_citations: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkspaceConnectorStateView:
    enabled: bool
    credential_policy: str | None


@dataclass(frozen=True, slots=True)
class CredentialGrantView:
    policy: str
    available: bool
    source: str | None = None
    shared_by: str | None = None


@dataclass(frozen=True, slots=True)
class EffectiveConnectorState:
    template: ConnectorTemplateView | None
    install: ConnectorInstallView
    workspace_state: WorkspaceConnectorStateView
    credential_policy: CredentialPolicy
    credential_availability: CredentialAvailability
    credential_source: str | None
    credential_shared_by: str | None
    usable: bool
    reason: EffectiveReason


def normalize_credential_policy(
    *,
    auth_method: str,
    install_scope: str,
    workspace_policy: str | None,
) -> CredentialPolicy:
    if auth_method == "none":
        return "none"
    raw = workspace_policy or install_scope
    if raw not in _VALID_CREDENTIAL_POLICIES or raw == "none":
        return "user"
    return cast(CredentialPolicy, raw)


def compute_effective_state(
    *,
    template: ConnectorTemplateView | None,
    install: ConnectorInstallView,
    workspace_state: WorkspaceConnectorStateView,
    grant: CredentialGrantView | None,
) -> EffectiveConnectorState:
    policy = normalize_credential_policy(
        auth_method=install.auth_method,
        install_scope=install.credential_scope,
        workspace_policy=workspace_state.credential_policy,
    )

    if policy == "none":
        availability: CredentialAvailability = "not_required"
        source: str | None = None
        shared_by: str | None = None
    elif grant is not None and grant.available and grant.policy == policy:
        availability = "available"
        source = grant.source
        shared_by = grant.shared_by
    else:
        availability = "missing"
        source = None
        shared_by = None

    reason: EffectiveReason = "usable"
    usable = True
    if template is not None and template.status != "active":
        reason = "template_inactive"
        usable = False
    elif install.install_status != "active":
        reason = "install_deleted"
        usable = False
    elif install.transport not in _VALID_TRANSPORTS:
        reason = "unsupported_transport"
        usable = False
    elif not workspace_state.enabled:
        reason = "workspace_disabled"
        usable = False
    elif availability == "missing":
        reason = "credential_missing"
        usable = False

    return EffectiveConnectorState(
        template=template,
        install=install,
        workspace_state=workspace_state,
        credential_policy=policy,
        credential_availability=availability,
        credential_source=source,
        credential_shared_by=shared_by,
        usable=usable,
        reason=reason,
    )
```

- [ ] **Step 4: Run the pure tests**

Run: `cd backend && uv run pytest -q tests/unit/mcp/test_effective_state.py`

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/mcp/effective.py backend/tests/unit/mcp/test_effective_state.py
git commit -m "feat(mcp): add effective connector state model"
```

---

## Task 2: Install Lifecycle and No-Auth Correctness

**Files:**

- Modify: `backend/cubebox/models/mcp.py`
- Modify: `backend/cubebox/repositories/mcp.py`
- Modify: `backend/cubebox/services/mcp_catalog.py`
- Modify: `backend/tests/unit/test_mcp_models.py`
- Modify: `backend/tests/e2e/test_mcp_catalog_routes.py`
- Create: `backend/alembic/versions/<ts>_add_mcp_install_status.py`

- [ ] **Step 1: Add model and route tests for `install_status`**

Append to `backend/tests/unit/test_mcp_models.py`:

```python
def test_mcp_server_install_status_defaults_active() -> None:
    from cubebox.models import MCPServer

    row = MCPServer(
        org_id="org-1",
        name="GitHub",
        server_url="https://github.example.com/mcp",
        server_url_hash="hash",
        transport="streamable_http",
        auth_method="none",
        credential_scope="none",
        created_by_user_id="user-1",
    )

    assert row.install_status == "active"
```

Append to `backend/tests/e2e/test_mcp_catalog_routes.py`:

```python
async def test_workspace_no_auth_catalog_install_uses_none_scope(
    client: httpx.AsyncClient,
    workspace_id: str,
    db_session: AsyncSession,
) -> None:
    from cubebox.repositories.mcp_catalog import MCPCatalogConnectorRepository

    row = await MCPCatalogConnectorRepository(db_session).upsert_by_slug(
        slug="noauth-effective-test",
        name="NoAuth Effective Test",
        description="No auth connector.",
        provider="Cubebox",
        server_url="https://noauth-effective.example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["none"],
        default_credential_scope="none",
    )
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/catalog/{row.id}/install",
        json={"auth_method": "none"},
    )

    assert resp.status_code == 201, resp.text
    install_id = resp.json()["install_id"]
    detail = await client.get(f"/api/v1/ws/{workspace_id}/mcp/servers/{install_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["auth_method"] == "none"
    assert body["credential_scope"] == "none"
    assert body["authed"] is True
```

- [ ] **Step 2: Run tests and confirm failures**

Run: `cd backend && uv run pytest -q tests/unit/test_mcp_models.py::test_mcp_server_install_status_defaults_active`

Expected: FAIL with `AttributeError: 'MCPServer' object has no attribute 'install_status'`.

Run: `cd backend && uv run pytest -q tests/e2e/test_mcp_catalog_routes.py::test_workspace_no_auth_catalog_install_uses_none_scope`

Expected: FAIL because workspace catalog install currently forces user scope.

- [ ] **Step 3: Add `install_status` to the model**

Edit `backend/cubebox/models/mcp.py` inside `MCPServer`, after `credential_scope`:

```python
    install_status: str = Field(
        default="active",
        max_length=16,
        sa_column_kwargs={"server_default": text("'active'")},
    )
```

- [ ] **Step 4: Add the Alembic migration**

Run:

```bash
cd backend
uv run alembic revision --autogenerate -m "add mcp install status"
```

Edit the generated migration so `upgrade()` contains:

```python
op.add_column(
    "mcp_servers",
    sa.Column(
        "install_status",
        sa.String(length=16),
        server_default=sa.text("'active'"),
        nullable=False,
    ),
)
op.create_check_constraint(
    "ck_mcp_servers_install_status",
    "mcp_servers",
    "install_status IN ('active', 'deleted')",
)
```

Ensure `downgrade()` contains:

```python
op.drop_constraint("ck_mcp_servers_install_status", "mcp_servers", type_="check")
op.drop_column("mcp_servers", "install_status")
```

- [ ] **Step 5: Filter deleted installs in repository list helpers**

Edit `backend/cubebox/repositories/mcp.py`.

In `list_for_org`, add this filter before returning:

```python
        stmt = stmt.where(MCPServer.install_status == "active")  # type: ignore[arg-type]
```

In `list_for_workspace`, replace the `MCPServer.authed` filter with:

```python
            MCPServer.install_status == "active",  # type: ignore[arg-type]
```

Keep credential readiness out of this repository method. Effective-state code will decide
whether an active install is usable for a specific user.

In `list_org_wide_with_workspace_override`, add:

```python
                MCPServer.install_status == "active",  # type: ignore[arg-type]
```

- [ ] **Step 6: Set lifecycle state during delete and re-key**

Edit `backend/cubebox/services/mcp_catalog.py`.

In `delete_install`, replace the block that sets `server.authed = False` with:

```python
        server.install_status = "deleted"
        server.authed = False
        server.last_error = None
        server.tools_cache = []
        server.last_discovered_at = datetime.now(UTC)
        await self.server_repo.update(server)
```

In `switch_auth_method`, immediately after `server.auth_method = new_auth_method`, add:

```python
        server.install_status = "active"
```

In `_finalize_install`, add this as the first statement:

```python
        server.install_status = "active"
```

- [ ] **Step 7: Fix workspace no-auth catalog install scope**

Edit `_install_workspace_user` in `backend/cubebox/services/mcp_catalog.py`.

Replace the server constructor's hard-coded user scope with:

```python
            credential_scope="none" if auth_method == "none" else "user",
```

Keep static and OAuth user installs as user-scope because those grants are owned by the
installing user.

- [ ] **Step 8: Run migration and tests**

Run:

```bash
cd backend
uv run alembic upgrade head
uv run pytest -q tests/unit/test_mcp_models.py::test_mcp_server_install_status_defaults_active
uv run pytest -q tests/e2e/test_mcp_catalog_routes.py::test_workspace_no_auth_catalog_install_uses_none_scope
```

Expected: all commands pass.

- [ ] **Step 9: Commit**

```bash
git add backend/cubebox/models/mcp.py \
        backend/cubebox/repositories/mcp.py \
        backend/cubebox/services/mcp_catalog.py \
        backend/alembic/versions/*add_mcp_install_status* \
        backend/tests/unit/test_mcp_models.py \
        backend/tests/e2e/test_mcp_catalog_routes.py
git commit -m "feat(mcp): separate install lifecycle from credential readiness"
```

---

## Task 3: DB-Backed Effective Connector Service

**Files:**

- Modify: `backend/cubebox/mcp/effective.py`
- Modify: `backend/cubebox/mcp/dependencies.py`
- Create: `backend/tests/unit/mcp/test_effective_service.py`

- [ ] **Step 1: Add service tests with fake dependencies**

Create `backend/tests/unit/mcp/test_effective_service.py`:

```python
from types import SimpleNamespace

import pytest

from cubebox.mcp.effective import EffectiveConnectorService


def _server(**overrides: object) -> SimpleNamespace:
    base = {
        "id": "mcp-1",
        "catalog_connector_id": "mctlg-1",
        "name": "GitHub",
        "server_url": "https://github.example.com/mcp",
        "transport": "streamable_http",
        "auth_method": "static",
        "credential_scope": "org",
        "install_status": "active",
        "owner_workspace_id": None,
        "credential_id": "cred-1",
        "headers": {},
        "tools_cache": [],
        "tool_citations": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _catalog(**overrides: object) -> SimpleNamespace:
    base = {
        "id": "mctlg-1",
        "slug": "github",
        "name": "GitHub",
        "provider": "GitHub",
        "status": "active",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _ServerRepo:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows

    async def list_for_org(self, *, owner_workspace_id: str | None | object = ...) -> list[object]:
        if owner_workspace_id is ...:
            return list(self.rows)
        return [row for row in self.rows if row.owner_workspace_id == owner_workspace_id]

    async def list_org_wide_with_workspace_override(self, workspace_id: str) -> list[tuple[object, object | None]]:
        return [(row, SimpleNamespace(enabled=True, credential_mode=None)) for row in self.rows]


class _CatalogRepo:
    async def get_by_id(self, catalog_id: str) -> object:
        assert catalog_id == "mctlg-1"
        return _catalog()


class _WorkspaceCredRepo:
    async def get(self, *, workspace_id: str, mcp_server_id: str) -> object | None:
        return None


class _UserCredRepo:
    async def get(self, *, user_id: str, mcp_server_id: str) -> object | None:
        return None


class _Session:
    async def get(self, model: object, key: str) -> object | None:
        return None


@pytest.mark.asyncio
async def test_effective_service_lists_active_org_install_with_org_grant() -> None:
    service = EffectiveConnectorService(
        session=_Session(),  # type: ignore[arg-type]
        server_repo=_ServerRepo([_server()]),  # type: ignore[arg-type]
        catalog_repo=_CatalogRepo(),  # type: ignore[arg-type]
        ws_cred_repo=_WorkspaceCredRepo(),  # type: ignore[arg-type]
        user_cred_repo=_UserCredRepo(),  # type: ignore[arg-type]
    )

    rows = await service.list_for_workspace_user(
        workspace_id="ws-1",
        user_id="user-1",
        include_unusable=True,
    )

    assert len(rows) == 1
    assert rows[0].usable is True
    assert rows[0].credential_policy == "org"
    assert rows[0].credential_source == "org"


@pytest.mark.asyncio
async def test_effective_service_marks_missing_user_grant_unusable() -> None:
    service = EffectiveConnectorService(
        session=_Session(),  # type: ignore[arg-type]
        server_repo=_ServerRepo([_server(credential_scope="user", credential_id=None)]),  # type: ignore[arg-type]
        catalog_repo=_CatalogRepo(),  # type: ignore[arg-type]
        ws_cred_repo=_WorkspaceCredRepo(),  # type: ignore[arg-type]
        user_cred_repo=_UserCredRepo(),  # type: ignore[arg-type]
    )

    rows = await service.list_for_workspace_user(
        workspace_id="ws-1",
        user_id="user-1",
        include_unusable=True,
    )

    assert rows[0].usable is False
    assert rows[0].reason == "credential_missing"
```

- [ ] **Step 2: Run tests and confirm service is missing**

Run: `cd backend && uv run pytest -q tests/unit/mcp/test_effective_service.py`

Expected: FAIL with `ImportError` for `EffectiveConnectorService`.

- [ ] **Step 3: Add service construction and row mapping**

Append to `backend/cubebox/mcp/effective.py`:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import User
from cubebox.repositories.mcp import (
    MCPServerRepository,
    UserMCPCredentialRepository,
    WorkspaceMCPCredentialRepository,
)
from cubebox.repositories.mcp_catalog import MCPCatalogConnectorRepository


class EffectiveConnectorService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        server_repo: MCPServerRepository,
        catalog_repo: MCPCatalogConnectorRepository,
        ws_cred_repo: WorkspaceMCPCredentialRepository,
        user_cred_repo: UserMCPCredentialRepository,
    ) -> None:
        self._session = session
        self._server_repo = server_repo
        self._catalog_repo = catalog_repo
        self._ws_cred_repo = ws_cred_repo
        self._user_cred_repo = user_cred_repo

    async def list_for_workspace_user(
        self,
        *,
        workspace_id: str,
        user_id: str,
        include_unusable: bool = True,
    ) -> list[EffectiveConnectorState]:
        org_rows = await self._server_repo.list_org_wide_with_workspace_override(workspace_id)
        workspace_rows = [
            (row, None)
            for row in await self._server_repo.list_for_org(owner_workspace_id=workspace_id)
        ]
        states: list[EffectiveConnectorState] = []

        for server, override in [*org_rows, *workspace_rows]:
            enabled = bool(server.owner_workspace_id == workspace_id)
            credential_policy: str | None = None
            if server.owner_workspace_id is None:
                enabled = bool(override is not None and override.enabled)
                credential_policy = getattr(override, "credential_mode", None)

            template = await self._template_view(server.catalog_connector_id)
            install = ConnectorInstallView(
                install_id=server.id,
                name=server.name,
                server_url=server.server_url,
                transport=server.transport,
                auth_method=server.auth_method,
                credential_scope=server.credential_scope,
                install_status=server.install_status,
                origin="workspace" if server.owner_workspace_id else "org",
                owner_workspace_id=server.owner_workspace_id,
                credential_id=server.credential_id,
                headers=dict(server.headers or {}),
                tools_cache=list(server.tools_cache or []),
                tool_citations=dict(server.tool_citations or {}),
            )
            workspace_state = WorkspaceConnectorStateView(
                enabled=enabled,
                credential_policy=credential_policy,
            )
            policy = normalize_credential_policy(
                auth_method=server.auth_method,
                install_scope=server.credential_scope,
                workspace_policy=credential_policy,
            )
            grant = await self._grant_view(
                policy=policy,
                server_id=server.id,
                server_credential_id=server.credential_id,
                workspace_id=workspace_id,
                user_id=user_id,
            )
            state = compute_effective_state(
                template=template,
                install=install,
                workspace_state=workspace_state,
                grant=grant,
            )
            if include_unusable or state.usable:
                states.append(state)

        return states

    async def _template_view(self, catalog_connector_id: str | None) -> ConnectorTemplateView | None:
        if catalog_connector_id is None:
            return None
        row = await self._catalog_repo.get_by_id(catalog_connector_id)
        if row is None:
            return None
        return ConnectorTemplateView(
            template_id=row.id,
            slug=row.slug,
            name=row.name,
            provider=row.provider,
            status=row.status,
        )

    async def _grant_view(
        self,
        *,
        policy: CredentialPolicy,
        server_id: str,
        server_credential_id: str | None,
        workspace_id: str,
        user_id: str,
    ) -> CredentialGrantView | None:
        if policy == "none":
            return None
        if policy == "org":
            return CredentialGrantView(
                policy="org",
                available=server_credential_id is not None,
                source="org" if server_credential_id is not None else None,
            )
        if policy == "workspace":
            row = await self._ws_cred_repo.get(
                workspace_id=workspace_id,
                mcp_server_id=server_id,
            )
            shared_by = None
            if row is not None:
                user = await self._session.get(User, row.created_by_user_id)
                shared_by = user.email if user is not None else None
            return CredentialGrantView(
                policy="workspace",
                available=row is not None,
                source="workspace" if row is not None else None,
                shared_by=shared_by,
            )
        row = await self._user_cred_repo.get(user_id=user_id, mcp_server_id=server_id)
        return CredentialGrantView(
            policy="user",
            available=row is not None,
            source="user" if row is not None else None,
        )
```

- [ ] **Step 4: Add the FastAPI dependency**

Edit `backend/cubebox/mcp/dependencies.py`.

Add the import:

```python
from cubebox.mcp.effective import EffectiveConnectorService
```

Add this provider after `get_member_catalog_service`:

```python
async def get_effective_connector_service(
    session: AsyncSession = Depends(get_session),
    ctx: RequestContext = Depends(require_member),
) -> EffectiveConnectorService:
    return EffectiveConnectorService(
        session=session,
        server_repo=MCPServerRepository(session, org_id=ctx.org_id),
        catalog_repo=MCPCatalogConnectorRepository(session),
        ws_cred_repo=WorkspaceMCPCredentialRepository(session, org_id=ctx.org_id),
        user_cred_repo=UserMCPCredentialRepository(session, org_id=ctx.org_id),
    )
```

- [ ] **Step 5: Run service tests**

Run: `cd backend && uv run pytest -q tests/unit/mcp/test_effective_state.py tests/unit/mcp/test_effective_service.py`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/mcp/effective.py \
        backend/cubebox/mcp/dependencies.py \
        backend/tests/unit/mcp/test_effective_service.py
git commit -m "feat(mcp): add effective connector service"
```

---

## Task 4: Normalized Workspace Connector API

**Files:**

- Modify: `backend/cubebox/api/schemas/mcp.py`
- Modify: `backend/cubebox/api/routes/v1/ws_mcp.py`
- Modify: `backend/cubebox/api/schemas/ws_settings.py`
- Modify: `backend/cubebox/api/routes/v1/ws_settings.py`
- Modify: `backend/tests/e2e/conftest.py`
- Create: `backend/tests/e2e/test_mcp_effective_connectors.py`

- [ ] **Step 1: Add the no-auth catalog fixture**

Append to `backend/tests/e2e/conftest.py`:

```python
@pytest_asyncio.fixture
async def noauth_catalog_id(db_session: AsyncSession) -> AsyncIterator[str]:
    from cubebox.repositories.mcp_catalog import MCPCatalogConnectorRepository

    row = await MCPCatalogConnectorRepository(db_session).upsert_by_slug(
        slug="noauth-effective",
        name="NoAuth Effective",
        description="No auth connector for effective-state tests.",
        provider="Cubebox",
        server_url="https://noauth-effective.example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["none"],
        default_credential_scope="none",
    )
    await db_session.commit()
    yield row.id
```

- [ ] **Step 2: Add API tests for effective connector listing and state patching**

Create `backend/tests/e2e/test_mcp_effective_connectors.py`:

```python
import httpx
import pytest


@pytest.mark.usefixtures("stub_discover_tools")
async def test_effective_connectors_report_missing_user_grant(
    admin_client: tuple[httpx.AsyncClient, str],
    github_catalog_id: str,
) -> None:
    client, workspace_id = admin_client

    install = await client.post(
        f"/api/v1/admin/mcp/catalog/{github_catalog_id}/install",
        json={"auth_method": "static", "credential_plaintext": "ghp_test"},
    )
    assert install.status_code == 201, install.text
    install_id = install.json()["install_id"]

    patch = await client.patch(
        f"/api/v1/ws/{workspace_id}/mcp/connectors/{install_id}/state",
        json={"enabled": True, "credential_policy": "user"},
    )
    assert patch.status_code == 200, patch.text

    resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/connectors")
    assert resp.status_code == 200, resp.text
    rows = resp.json()["items"]
    row = next(item for item in rows if item["install"]["install_id"] == install_id)
    assert row["workspace_state"]["enabled"] is True
    assert row["credential_policy"] == "user"
    assert row["credential_availability"] == "missing"
    assert row["usable"] is False
    assert row["reason"] == "credential_missing"


@pytest.mark.usefixtures("stub_discover_tools")
async def test_effective_connectors_report_no_auth_as_usable(
    client: httpx.AsyncClient,
    workspace_id: str,
    noauth_catalog_id: str,
) -> None:
    install = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/catalog/{noauth_catalog_id}/install",
        json={"auth_method": "none"},
    )
    assert install.status_code == 201, install.text

    resp = await client.get(f"/api/v1/ws/{workspace_id}/mcp/connectors")
    assert resp.status_code == 200, resp.text
    row = next(
        item
        for item in resp.json()["items"]
        if item["install"]["install_id"] == install.json()["install_id"]
    )
    assert row["credential_policy"] == "none"
    assert row["credential_availability"] == "not_required"
    assert row["usable"] is True
```

- [ ] **Step 3: Run route tests and confirm missing route failure**

Run: `cd backend && uv run pytest -q tests/e2e/test_mcp_effective_connectors.py`

Expected: FAIL with 404 for `/api/v1/ws/{workspace_id}/mcp/connectors`.

- [ ] **Step 4: Add response and patch schemas**

Append to `backend/cubebox/api/schemas/mcp.py`:

```python
class MCPConnectorTemplateOut(BaseModel):
    template_id: str | None
    slug: str | None
    name: str
    provider: str | None
    status: str


class MCPConnectorInstallOut(BaseModel):
    install_id: str
    name: str
    server_url: str
    transport: str
    auth_method: str
    credential_scope: str
    install_status: str
    origin: str
    owner_workspace_id: str | None
    tool_count: int


class MCPWorkspaceConnectorStateOut(BaseModel):
    enabled: bool
    credential_policy: str | None


class MCPEffectiveConnectorOut(BaseModel):
    template: MCPConnectorTemplateOut | None
    install: MCPConnectorInstallOut
    workspace_state: MCPWorkspaceConnectorStateOut
    credential_policy: str
    credential_availability: str
    credential_source: str | None
    credential_shared_by: str | None
    usable: bool
    reason: str


class MCPEffectiveConnectorListOut(BaseModel):
    items: list[MCPEffectiveConnectorOut]


class MCPWorkspaceConnectorStatePatch(BaseModel):
    enabled: bool | None = None
    credential_policy: Literal["org", "workspace", "user", "none"] | None = None
```

- [ ] **Step 5: Add route mapping helpers and endpoints**

Edit `backend/cubebox/api/routes/v1/ws_mcp.py`.

Add imports:

```python
from cubebox.api.schemas.mcp import (
    MCPEffectiveConnectorListOut,
    MCPEffectiveConnectorOut,
    MCPConnectorInstallOut,
    MCPConnectorTemplateOut,
    MCPWorkspaceConnectorStateOut,
    MCPWorkspaceConnectorStatePatch,
)
from cubebox.mcp.dependencies import get_effective_connector_service
from cubebox.mcp.effective import EffectiveConnectorService, EffectiveConnectorState
```

Add helper:

```python
def _effective_to_out(state: EffectiveConnectorState) -> MCPEffectiveConnectorOut:
    template = None
    if state.template is not None:
        template = MCPConnectorTemplateOut(
            template_id=state.template.template_id,
            slug=state.template.slug,
            name=state.template.name,
            provider=state.template.provider,
            status=state.template.status,
        )
    return MCPEffectiveConnectorOut(
        template=template,
        install=MCPConnectorInstallOut(
            install_id=state.install.install_id,
            name=state.install.name,
            server_url=state.install.server_url,
            transport=state.install.transport,
            auth_method=state.install.auth_method,
            credential_scope=state.install.credential_scope,
            install_status=state.install.install_status,
            origin=state.install.origin,
            owner_workspace_id=state.install.owner_workspace_id,
            tool_count=len(state.install.tools_cache),
        ),
        workspace_state=MCPWorkspaceConnectorStateOut(
            enabled=state.workspace_state.enabled,
            credential_policy=state.workspace_state.credential_policy,
        ),
        credential_policy=state.credential_policy,
        credential_availability=state.credential_availability,
        credential_source=state.credential_source,
        credential_shared_by=state.credential_shared_by,
        usable=state.usable,
        reason=state.reason,
    )
```

Add endpoints near the top of the router:

```python
@router.get("/connectors", response_model=MCPEffectiveConnectorListOut)
async def list_effective_connectors(
    workspace_id: str,
    ctx: RequestContext = Depends(require_member),
    effective: EffectiveConnectorService = Depends(get_effective_connector_service),
) -> MCPEffectiveConnectorListOut:
    rows = await effective.list_for_workspace_user(
        workspace_id=workspace_id,
        user_id=ctx.user.id,
        include_unusable=True,
    )
    return MCPEffectiveConnectorListOut(items=[_effective_to_out(row) for row in rows])


@router.patch(
    "/connectors/{install_id}/state",
    response_model=MCPEffectiveConnectorOut,
)
async def patch_effective_connector_state(
    workspace_id: str,
    install_id: str,
    body: MCPWorkspaceConnectorStatePatch,
    svc: MCPServerService = Depends(get_mcp_service),
    ctx: RequestContext = Depends(require_member),
    effective: EffectiveConnectorService = Depends(get_effective_connector_service),
) -> MCPEffectiveConnectorOut:
    server = await svc.server_repo.get(install_id)
    if server is None or server.owner_workspace_id is not None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})

    if body.enabled is not None:
        if body.enabled:
            await svc.override_repo.upsert(
                workspace_id=workspace_id,
                mcp_server_id=install_id,
                enabled=True,
                updated_by_user_id=ctx.user.id,
            )
        else:
            await svc.override_repo.delete(
                workspace_id=workspace_id,
                mcp_server_id=install_id,
            )

    if body.credential_policy is not None:
        override = await svc.override_repo.get_for_workspace_and_server(
            workspace_id=workspace_id,
            mcp_server_id=install_id,
        )
        if override is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={"code": "mcp_connector_state_required"},
            )
        override.credential_mode = body.credential_policy
        override.updated_by_user_id = ctx.user.id
        svc.server_repo.session.add(override)
        await svc.server_repo.session.commit()

    rows = await effective.list_for_workspace_user(
        workspace_id=workspace_id,
        user_id=ctx.user.id,
        include_unusable=True,
    )
    for row in rows:
        if row.install.install_id == install_id:
            return _effective_to_out(row)
    raise HTTPException(404, detail={"code": "mcp_install_not_found"})
```

- [ ] **Step 6: Tighten workspace settings schema literals**

Edit `backend/cubebox/api/schemas/ws_settings.py`.

Add import:

```python
from typing import Literal
```

Replace:

```python
    credential_mode: str = "org"
```

with:

```python
    credential_mode: Literal["org", "workspace", "user", "none"] = "org"
```

Replace:

```python
    credential_mode: str | None = None
```

with:

```python
    credential_mode: Literal["org", "workspace", "user", "none"] | None = None
```

- [ ] **Step 7: Delegate settings MCP list to effective service**

Edit `backend/cubebox/api/routes/v1/ws_settings.py`.

Replace `list_workspace_mcp`'s body with:

```python
    from cubebox.mcp.dependencies import get_effective_connector_service

    effective = await get_effective_connector_service(session=session, ctx=ctx)
    rows = await effective.list_for_workspace_user(
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
        include_unusable=True,
    )
    org_servers: list[MCPServerItem] = []
    workspace_servers: list[MCPServerItem] = []
    for row in rows:
        item = MCPServerItem(
            server_id=row.install.install_id,
            name=row.install.name,
            server_url=row.install.server_url,
            transport=row.install.transport,
            enabled=row.workspace_state.enabled,
            scope=row.install.origin,
            credential_mode=row.credential_policy,
            credential_source=(
                row.credential_source
                if row.credential_availability == "available"
                else "needs_setup"
                if row.credential_availability == "missing"
                else None
            ),
            credential_shared_by=row.credential_shared_by,
        )
        if row.install.origin == "org":
            org_servers.append(item)
        else:
            workspace_servers.append(item)
    return WorkspaceMCPOut(org_servers=org_servers, workspace_servers=workspace_servers)
```

- [ ] **Step 8: Run API tests**

Run:

```bash
cd backend
uv run pytest -q tests/e2e/test_mcp_effective_connectors.py
uv run pytest -q tests/e2e/test_ws_settings.py
```

Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add backend/cubebox/api/schemas/mcp.py \
        backend/cubebox/api/routes/v1/ws_mcp.py \
        backend/cubebox/api/schemas/ws_settings.py \
        backend/cubebox/api/routes/v1/ws_settings.py \
        backend/tests/e2e/conftest.py \
        backend/tests/e2e/test_mcp_effective_connectors.py
git commit -m "feat(mcp): expose effective workspace connector state"
```

---

## Task 5: Runtime Uses Effective Service and OAuth Refresh

**Files:**

- Modify: `backend/cubebox/mcp/effective.py`
- Modify: `backend/cubebox/mcp/cubepi_discovery.py`
- Modify: `backend/cubebox/mcp/cubepi_runtime.py`
- Modify: `backend/cubebox/streams/run_manager.py`
- Modify: `backend/tests/unit/test_mcp_cubepi_runtime.py`
- Modify: `backend/tests/unit/mcp/test_oauth_token_manager.py`

- [ ] **Step 1: Add runtime token resolver tests**

Append to `backend/tests/unit/test_mcp_cubepi_runtime.py`:

```python
@pytest.mark.asyncio
async def test_load_only_discovers_usable_effective_connectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubebox.mcp.effective import (
        ConnectorInstallView,
        EffectiveConnectorState,
        WorkspaceConnectorStateView,
    )

    usable = EffectiveConnectorState(
        template=None,
        install=ConnectorInstallView(
            install_id="s1",
            name="good",
            server_url="http://good",
            transport="streamable_http",
            auth_method="none",
            credential_scope="none",
            install_status="active",
            origin="workspace",
            owner_workspace_id="ws-1",
            credential_id=None,
        ),
        workspace_state=WorkspaceConnectorStateView(enabled=True, credential_policy=None),
        credential_policy="none",
        credential_availability="not_required",
        credential_source=None,
        credential_shared_by=None,
        usable=True,
        reason="usable",
    )
    unusable = dataclasses.replace(usable, usable=False, reason="credential_missing")

    class _Effective:
        async def list_for_workspace_user(
            self, *, workspace_id: str, user_id: str, include_unusable: bool
        ) -> list[EffectiveConnectorState]:
            return [usable, unusable]

    async def _fake_loader(
        url: str,
        *,
        headers: dict[str, str] | None,
        timeout: float,
        transport: str,
    ) -> list[object]:
        assert url == "http://good"
        return [_FakeTool(name="search")]

    monkeypatch.setattr("cubebox.mcp.cubepi_runtime.load_mcp_tools_http", _fake_loader)

    tools, _citation_configs = await load_workspace_mcp_tools_for_cubepi(
        session=None,  # type: ignore[arg-type]
        workspace_id="ws-1",
        org_id="org-1",
        user_id="user-1",
        cred_service=None,  # type: ignore[arg-type]
        signer=None,  # type: ignore[arg-type]
        effective_service=_Effective(),  # type: ignore[arg-type]
        token_manager=None,
    )

    assert [tool.name for tool in tools] == ["good__search"]
```

- [ ] **Step 2: Run the runtime test and confirm signature failure**

Run:

```bash
cd backend
uv run pytest -q tests/unit/test_mcp_cubepi_runtime.py::test_load_only_discovers_usable_effective_connectors
```

Expected: FAIL because `load_workspace_mcp_tools_for_cubepi` does not accept
`effective_service`.

- [ ] **Step 3: Add runtime spec conversion to effective service**

Append to `backend/cubebox/mcp/effective.py`:

```python
@dataclass(frozen=True, slots=True)
class RuntimeConnectorSpec:
    server_id: str
    server_name: str
    url: str
    transport: str
    headers: dict[str, str]
    tool_citations: dict[str, dict[str, Any]]
    credential_policy: CredentialPolicy
    auth_method: str
    credential_id: str | None


def effective_state_to_runtime_spec(state: EffectiveConnectorState) -> RuntimeConnectorSpec:
    return RuntimeConnectorSpec(
        server_id=state.install.install_id,
        server_name=state.install.name,
        url=state.install.server_url,
        transport=state.install.transport,
        headers=dict(state.install.headers),
        tool_citations=dict(state.install.tool_citations),
        credential_policy=state.credential_policy,
        auth_method=state.install.auth_method,
        credential_id=state.install.credential_id,
    )
```

- [ ] **Step 4: Change runtime loader signature and filtering**

Edit `backend/cubebox/mcp/cubepi_runtime.py`.

Add imports:

```python
from cubebox.mcp.effective import (
    EffectiveConnectorService,
    RuntimeConnectorSpec,
    effective_state_to_runtime_spec,
)
from cubebox.mcp.oauth.token_manager import OAuthTokenManager
```

Change `load_workspace_mcp_tools_for_cubepi` signature to include:

```python
    effective_service: EffectiveConnectorService | None = None,
    token_manager: OAuthTokenManager | None = None,
```

Replace the call to `discover_workspace_mcp_servers_for_cubepi` with:

```python
    if effective_service is None:
        servers = await discover_workspace_mcp_servers_for_cubepi(
            session=session,
            workspace_id=workspace_id,
            org_id=org_id,
            user_id=user_id,
            cred_service=cred_service,
            signer=signer,
            token_manager=token_manager,
        )
    else:
        states = await effective_service.list_for_workspace_user(
            workspace_id=workspace_id,
            user_id=user_id,
            include_unusable=False,
        )
        servers = [
            CubepiMCPServerSpec(
                server_id=spec.server_id,
                server_name=spec.server_name,
                url=spec.url,
                transport=cast(Any, spec.transport),
                headers=spec.headers,
                tool_citations=spec.tool_citations,
            )
            for spec in [effective_state_to_runtime_spec(state) for state in states]
        ]
```

Add `cast` to the typing imports if it is not present.

- [ ] **Step 5: Add OAuth token manager pass-through in discovery**

Edit `backend/cubebox/mcp/cubepi_discovery.py`.

Import:

```python
from cubebox.mcp.oauth.token_manager import OAuthTokenManager
```

Add parameter to `discover_workspace_mcp_servers_for_cubepi`:

```python
    token_manager: OAuthTokenManager | None = None,
```

Pass it into `_resolve_token_for_cubepi`:

```python
                token_manager=token_manager,
```

Add parameter to `_resolve_token_for_cubepi`:

```python
    token_manager: OAuthTokenManager | None,
```

At the top of the user-scope branch, before falling back to direct vault decryption:

```python
        if auth_method == "oauth" and token_manager is not None:
            return await token_manager.get_access_token(
                server_id=server_id,
                user_id=user_id,
            )
```

This keeps OAuth refresh in one manager instead of decrypting stale access tokens directly.

- [ ] **Step 6: Wire token manager and effective service in run manager**

Edit `backend/cubebox/streams/run_manager.py`.

Inside the MCP tools block, import:

```python
import httpx

from cubebox.mcp.dependencies import _build_token_manager_for_org
from cubebox.mcp.effective import EffectiveConnectorService
from cubebox.mcp.oauth.metadata import OAuthMetadataDiscovery
from cubebox.repositories.mcp import (
    MCPServerRepository,
    UserMCPCredentialRepository,
    WorkspaceMCPCredentialRepository,
)
from cubebox.repositories.mcp_catalog import MCPCatalogConnectorRepository
```

Before calling `load_workspace_mcp_tools_for_cubepi`, construct:

```python
                oauth_http_client = getattr(
                    self._app.state,
                    "_mcp_oauth_http_client",
                    None,
                )
                if oauth_http_client is None:
                    oauth_http_client = httpx.AsyncClient(timeout=30.0)
                    setattr(
                        self._app.state,
                        "_mcp_oauth_http_client",
                        oauth_http_client,
                    )

                oauth_metadata = getattr(
                    self._app.state,
                    "_mcp_oauth_metadata_discovery",
                    None,
                )
                if oauth_metadata is None:
                    oauth_metadata = OAuthMetadataDiscovery(oauth_http_client)
                    setattr(
                        self._app.state,
                        "_mcp_oauth_metadata_discovery",
                        oauth_metadata,
                    )

                token_manager = _build_token_manager_for_org(
                    session=mcp_session,
                    backend=self._app.state.encryption_backend,
                    redis=self._app.state.redis,
                    http_client=oauth_http_client,
                    metadata=oauth_metadata,
                    org_id=ctx.org_id,
                )
```

Then pass:

```python
                effective_service = EffectiveConnectorService(
                    session=mcp_session,
                    server_repo=MCPServerRepository(mcp_session, org_id=ctx.org_id),
                    catalog_repo=MCPCatalogConnectorRepository(mcp_session),
                    ws_cred_repo=WorkspaceMCPCredentialRepository(mcp_session, org_id=ctx.org_id),
                    user_cred_repo=UserMCPCredentialRepository(mcp_session, org_id=ctx.org_id),
                )
```

and call the loader with:

```python
                    effective_service=effective_service,
                    token_manager=token_manager,
```

- [ ] **Step 7: Run runtime tests**

Run:

```bash
cd backend
uv run pytest -q tests/unit/test_mcp_cubepi_runtime.py
uv run pytest -q tests/unit/mcp/test_oauth_token_manager.py
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add backend/cubebox/mcp/effective.py \
        backend/cubebox/mcp/cubepi_discovery.py \
        backend/cubebox/mcp/cubepi_runtime.py \
        backend/cubebox/streams/run_manager.py \
        backend/tests/unit/test_mcp_cubepi_runtime.py \
        backend/tests/unit/mcp/test_oauth_token_manager.py
git commit -m "feat(mcp): load runtime tools from effective connector state"
```

---

## Task 6: Template Aliases and Grant-Oriented Compatibility

**Files:**

- Modify: `backend/cubebox/api/routes/v1/mcp_catalog.py`
- Modify: `backend/cubebox/api/routes/v1/ws_mcp.py`
- Modify: `backend/tests/unit/test_ws_mcp_routes.py`
- Modify: `backend/tests/e2e/test_mcp_effective_connectors.py`

- [ ] **Step 1: Add route registration tests**

Append to `backend/tests/unit/test_ws_mcp_routes.py`:

```python
def test_workspace_mcp_effective_routes_are_registered() -> None:
    from cubebox.api.app import create_app

    app = create_app()
    paths = {route.path for route in app.routes}

    assert "/api/v1/ws/{workspace_id}/mcp/connectors" in paths
    assert "/api/v1/ws/{workspace_id}/mcp/connectors/{install_id}/state" in paths
    assert "/api/v1/ws/{workspace_id}/mcp/templates" in paths
```

- [ ] **Step 2: Add template alias endpoint**

Edit `backend/cubebox/api/routes/v1/mcp_catalog.py`.

Below `list_catalog`, add:

```python
@catalog_member_router.get("/templates", response_model=MCPCatalogListOut)
async def list_templates(
    workspace_id: str = Path(..., max_length=20),
    q: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    svc: MCPCatalogService = Depends(get_member_catalog_service),
) -> MCPCatalogListOut:
    dtos = await svc.list_for_member(workspace_id, q=q, provider=provider)
    return MCPCatalogListOut(items=[_connector_to_out(dto) for dto in dtos])
```

This preserves the existing response shape while moving product language off `catalog`.

- [ ] **Step 3: Add grant alias routes for user and workspace credentials**

Edit `backend/cubebox/api/routes/v1/ws_mcp.py`.

For the existing user credential handlers, add route decorators above the same functions:

```python
@router.get("/connectors/{server_id}/grants/me", response_model=MCPCredentialStatus)
```

```python
@router.put("/connectors/{server_id}/grants/me", response_model=MCPCredentialStatus)
```

```python
@router.delete("/connectors/{server_id}/grants/me", status_code=status.HTTP_204_NO_CONTENT)
```

For workspace credential handlers, add:

```python
@router.get("/connectors/{server_id}/grants/workspace", response_model=MCPCredentialStatus)
```

```python
@router.put("/connectors/{server_id}/grants/workspace", response_model=MCPCredentialStatus)
```

```python
@router.delete(
    "/connectors/{server_id}/grants/workspace",
    status_code=status.HTTP_204_NO_CONTENT,
)
```

- [ ] **Step 4: Run route registration and E2E tests**

Run:

```bash
cd backend
uv run pytest -q tests/unit/test_ws_mcp_routes.py::test_workspace_mcp_effective_routes_are_registered
uv run pytest -q tests/e2e/test_mcp_effective_connectors.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/api/routes/v1/mcp_catalog.py \
        backend/cubebox/api/routes/v1/ws_mcp.py \
        backend/tests/unit/test_ws_mcp_routes.py \
        backend/tests/e2e/test_mcp_effective_connectors.py
git commit -m "feat(mcp): add template and grant route aliases"
```

---

## Task 7: Frontend Core API and State Types

**Files:**

- Modify: `frontend/packages/core/src/types/mcp.ts`
- Modify: `frontend/packages/core/src/api/mcp.ts`
- Modify: `frontend/packages/core/src/types/workspace-settings.ts`
- Modify: `frontend/packages/core/src/api/workspace-settings.ts`
- Modify: `frontend/packages/core/src/stores/workspaceSettingsStore.ts`
- Modify: `frontend/packages/core/__tests__/api/mcp.test.ts`

- [ ] **Step 1: Add API tests**

Append to `frontend/packages/core/__tests__/api/mcp.test.ts`:

```typescript
it('lists effective workspace connectors', async () => {
  const { client, fetchMock } = makeClient({
    ok: true,
    json: async () => ({ items: [] }),
  })

  const out = await wsListEffectiveConnectors(client, 'ws-x')

  expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/ws/ws-x/mcp/connectors')
  expect(out).toEqual({ items: [] })
})

it('patches effective workspace connector state', async () => {
  const { client, fetchMock } = makeClient({
    ok: true,
    json: async () => ({
      install: { install_id: 'mcp-1' },
      workspace_state: { enabled: true, credential_policy: 'user' },
    }),
  })

  await wsPatchConnectorState(client, 'ws-x', 'mcp-1', {
    enabled: true,
    credential_policy: 'user',
  })

  expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/ws/ws-x/mcp/connectors/mcp-1/state')
  expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({
    enabled: true,
    credential_policy: 'user',
  })
})
```

- [ ] **Step 2: Run tests and confirm missing exports**

Run:

```bash
cd frontend
pnpm --filter @cubebox/core test -- mcp.test.ts
```

Expected: FAIL because `wsListEffectiveConnectors` and `wsPatchConnectorState` are
not exported.

- [ ] **Step 3: Add effective connector types**

Append to `frontend/packages/core/src/types/mcp.ts`:

```typescript
export type MCPEffectiveReason =
  | 'usable'
  | 'template_inactive'
  | 'install_deleted'
  | 'workspace_disabled'
  | 'credential_missing'
  | 'unsupported_transport'

export type MCPCredentialAvailability = 'available' | 'missing' | 'not_required'

export interface MCPConnectorTemplate {
  template_id: string | null
  slug: string | null
  name: string
  provider: string | null
  status: string
}

export interface MCPConnectorInstall {
  install_id: string
  name: string
  server_url: string
  transport: MCPTransport
  auth_method: MCPAuthMethod
  credential_scope: MCPCredentialScope
  install_status: 'active' | 'deleted'
  origin: 'org' | 'workspace'
  owner_workspace_id: string | null
  tool_count: number
}

export interface MCPWorkspaceConnectorState {
  enabled: boolean
  credential_policy: MCPCredentialScope | null
}

export interface MCPEffectiveConnector {
  template: MCPConnectorTemplate | null
  install: MCPConnectorInstall
  workspace_state: MCPWorkspaceConnectorState
  credential_policy: MCPCredentialScope
  credential_availability: MCPCredentialAvailability
  credential_source: 'org' | 'workspace' | 'user' | null
  credential_shared_by: string | null
  usable: boolean
  reason: MCPEffectiveReason
}

export interface MCPEffectiveConnectorList {
  items: MCPEffectiveConnector[]
}

export interface MCPWorkspaceConnectorStatePatch {
  enabled?: boolean
  credential_policy?: MCPCredentialScope
}
```

- [ ] **Step 4: Add API helpers**

Edit `frontend/packages/core/src/api/mcp.ts`.

Add imports:

```typescript
  MCPEffectiveConnector,
  MCPEffectiveConnectorList,
  MCPWorkspaceConnectorStatePatch,
```

Add functions:

```typescript
export async function wsListEffectiveConnectors(
  client: ApiClient,
  wsId: string,
): Promise<MCPEffectiveConnectorList> {
  const res = await client.get(`/api/v1/ws/${wsId}/mcp/connectors`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPEffectiveConnectorList
}

export async function wsPatchConnectorState(
  client: ApiClient,
  wsId: string,
  installId: string,
  body: MCPWorkspaceConnectorStatePatch,
): Promise<MCPEffectiveConnector> {
  const res = await client.patch(`/api/v1/ws/${wsId}/mcp/connectors/${installId}/state`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPEffectiveConnector
}
```

- [ ] **Step 5: Extend workspace settings types**

Edit `frontend/packages/core/src/types/workspace-settings.ts`.

Change:

```typescript
export type MCPCredentialMode = 'org' | 'workspace' | 'user'
```

to:

```typescript
export type MCPCredentialMode = 'org' | 'workspace' | 'user' | 'none'
```

Add import:

```typescript
import type { MCPEffectiveConnector } from './mcp'
```

Add to `WorkspaceMCP`:

```typescript
  connectors?: MCPEffectiveConnector[]
```

- [ ] **Step 6: Route workspace settings store through normalized helpers**

Edit `frontend/packages/core/src/api/workspace-settings.ts`.

Keep existing functions for compatibility, but make `patchWorkspaceMCPCredentialMode`
call the connector state endpoint when the `ApiClient` has a workspace id in scope:

```typescript
export async function patchWorkspaceMCPCredentialMode(
  client: ApiClient,
  serverId: string,
  credentialMode: MCPCredentialMode,
): Promise<{ server_id: string; credential_mode: MCPCredentialMode }> {
  const res = await client.patch(`/api/v1/mcp/connectors/${serverId}/state`, {
    credential_policy: credentialMode,
  })
  if (!res.ok) throw await toApiError(res)
  const body = await res.json()
  return {
    server_id: body.install.install_id,
    credential_mode: body.credential_policy,
  }
}
```

- [ ] **Step 7: Run frontend core tests and typecheck**

Run:

```bash
cd frontend
pnpm --filter @cubebox/core test -- mcp.test.ts
pnpm --filter @cubebox/core type-check
```

Expected: both commands pass.

- [ ] **Step 8: Commit**

```bash
git add frontend/packages/core/src/types/mcp.ts \
        frontend/packages/core/src/api/mcp.ts \
        frontend/packages/core/src/types/workspace-settings.ts \
        frontend/packages/core/src/api/workspace-settings.ts \
        frontend/packages/core/src/stores/workspaceSettingsStore.ts \
        frontend/packages/core/__tests__/api/mcp.test.ts
git commit -m "feat(mcp): add effective connector types and API client"
```

---

## Task 8: Workspace UI Uses Four-Layer Semantics

**Files:**

- Modify: `frontend/packages/web/components/workspace-settings/McpPanel.tsx`
- Modify: `frontend/packages/web/components/mcp/MCPCatalogInstallPanel.tsx`
- Modify: `frontend/packages/web/messages/en.json`
- Modify: `frontend/packages/web/messages/zh.json`
- Modify: `frontend/packages/web/__tests__/e2e/mcp/ws-mcp.spec.ts`

- [ ] **Step 1: Add Playwright assertions for visible semantics**

Edit `frontend/packages/web/__tests__/e2e/mcp/ws-mcp.spec.ts`.

Add a test that navigates to workspace settings and asserts these user-facing strings:

```typescript
await expect(page.getByText('Connector templates')).toBeVisible()
await expect(page.getByText('Installed in this workspace')).toBeVisible()
await expect(page.getByText('Needs your credential')).toBeVisible()
```

For Chinese locale coverage, add the same assertion pattern using:

```typescript
await expect(page.getByText('连接器模板')).toBeVisible()
await expect(page.getByText('已安装到此工作区')).toBeVisible()
await expect(page.getByText('需要你的凭证')).toBeVisible()
```

- [ ] **Step 2: Update workspace panel terminology**

Edit `frontend/packages/web/components/workspace-settings/McpPanel.tsx`.

Use these display labels:

```typescript
const reasonLabel: Record<string, string> = {
  usable: t('state.usable'),
  template_inactive: t('state.templateInactive'),
  install_deleted: t('state.installDeleted'),
  workspace_disabled: t('state.workspaceDisabled'),
  credential_missing: t('state.credentialMissing'),
  unsupported_transport: t('state.unsupportedTransport'),
}
```

Use "Enable for workspace" for workspace state, "Credential policy" for mode,
and "Disconnect" for deleting a user grant. Do not display the word "override".

- [ ] **Step 3: Present catalog as templates**

Edit `frontend/packages/web/components/mcp/MCPCatalogInstallPanel.tsx`.

Change the heading and empty state keys to:

```typescript
const title = t('templates.title')
const empty = t('templates.empty')
```

Ensure actions still call the existing install helpers. The endpoint alias will be used by
core API after compatibility is verified.

- [ ] **Step 4: Add message keys**

Add under `mcp.wsPanel` in `frontend/packages/web/messages/en.json`:

```json
"templates": {
  "title": "Connector templates",
  "empty": "No connector templates match this filter."
},
"installedWorkspace": "Installed in this workspace",
"credentialPolicy": "Credential policy",
"enableWorkspace": "Enable for workspace",
"disconnect": "Disconnect",
"state": {
  "usable": "Ready",
  "templateInactive": "Template unavailable",
  "installDeleted": "Install removed",
  "workspaceDisabled": "Disabled in this workspace",
  "credentialMissing": "Needs your credential",
  "unsupportedTransport": "Unsupported transport"
}
```

Add under `mcp.wsPanel` in `frontend/packages/web/messages/zh.json`:

```json
"templates": {
  "title": "连接器模板",
  "empty": "没有匹配的连接器模板。"
},
"installedWorkspace": "已安装到此工作区",
"credentialPolicy": "凭证策略",
"enableWorkspace": "为工作区启用",
"disconnect": "断开连接",
"state": {
  "usable": "可用",
  "templateInactive": "模板不可用",
  "installDeleted": "安装已移除",
  "workspaceDisabled": "此工作区已停用",
  "credentialMissing": "需要你的凭证",
  "unsupportedTransport": "不支持的传输方式"
}
```

- [ ] **Step 5: Run frontend checks**

Run:

```bash
cd frontend
pnpm --filter @cubebox/web type-check
pnpm --filter @cubebox/web test:e2e -- mcp/ws-mcp.spec.ts
```

Expected: typecheck passes and the MCP workspace spec passes.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/components/workspace-settings/McpPanel.tsx \
        frontend/packages/web/components/mcp/MCPCatalogInstallPanel.tsx \
        frontend/packages/web/messages/en.json \
        frontend/packages/web/messages/zh.json \
        frontend/packages/web/__tests__/e2e/mcp/ws-mcp.spec.ts
git commit -m "feat(mcp): clarify workspace connector management UI"
```

---

## Task 9: Admin UI and Compatibility Cleanup

**Files:**

- Modify: `frontend/packages/web/app/admin/mcp/page.tsx`
- Modify: `frontend/packages/web/components/mcp/MCPConnectorList.tsx`
- Modify: `frontend/packages/web/components/mcp/MCPWorkspacesTab.tsx`
- Modify: `frontend/packages/web/components/mcp/MCPCredentialPanel.tsx`
- Modify: `frontend/packages/web/messages/en.json`
- Modify: `frontend/packages/web/messages/zh.json`
- Modify: `frontend/packages/web/__tests__/e2e/mcp/admin-mcp.spec.ts`
- Modify: `backend/tests/e2e/test_mcp_auto_enroll.py`

- [ ] **Step 1: Add admin E2E assertions for install vs workspace state**

Edit `frontend/packages/web/__tests__/e2e/mcp/admin-mcp.spec.ts`.

Add assertions for these visible labels:

```typescript
await expect(page.getByText('Connector installs')).toBeVisible()
await expect(page.getByText('Workspace state')).toBeVisible()
await expect(page.getByText('Org grant')).toBeVisible()
```

- [ ] **Step 2: Update admin labels**

In the admin MCP page and components, replace visible "Catalog" labels with
"Templates" and "Override" labels with "Workspace state".

Use this map for admin badges:

```typescript
const scopeLabel: Record<string, string> = {
  org: t('scope.orgInstall'),
  workspace: t('scope.workspaceInstall'),
  user: t('scope.userGrant'),
  none: t('scope.noGrantRequired'),
}
```

- [ ] **Step 3: Keep old endpoint names inside API helpers only**

Do not rename functions that still target old backend URLs in UI components. Keep old
endpoint terminology in `frontend/packages/core/src/api/mcp.ts` until backend compatibility
metrics show no remaining callers.

- [ ] **Step 4: Verify delete vs disconnect semantics**

Extend `backend/tests/e2e/test_mcp_auto_enroll.py` with:

```python
async def test_admin_delete_install_removes_workspace_state_not_user_grants(
    admin_client: tuple[httpx.AsyncClient, str],
    github_catalog_id: str,
) -> None:
    client, workspace_id = admin_client
    install = await client.post(
        f"/api/v1/admin/mcp/catalog/{github_catalog_id}/install",
        json={"auth_method": "static", "credential_plaintext": "ghp_test"},
    )
    assert install.status_code == 201, install.text
    install_id = install.json()["install_id"]

    delete_resp = await client.delete(f"/api/v1/admin/mcp/installs/{install_id}")
    assert delete_resp.status_code == 204, delete_resp.text

    rows = await client.get(f"/api/v1/ws/{workspace_id}/mcp/connectors")
    assert rows.status_code == 200, rows.text
    assert all(item["install"]["install_id"] != install_id for item in rows.json()["items"])
```

- [ ] **Step 5: Run admin checks**

Run:

```bash
cd backend
uv run pytest -q tests/e2e/test_mcp_auto_enroll.py
cd ../frontend
pnpm --filter @cubebox/web type-check
pnpm --filter @cubebox/web test:e2e -- mcp/admin-mcp.spec.ts
```

Expected: all commands pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/app/admin/mcp/page.tsx \
        frontend/packages/web/components/mcp/MCPConnectorList.tsx \
        frontend/packages/web/components/mcp/MCPWorkspacesTab.tsx \
        frontend/packages/web/components/mcp/MCPCredentialPanel.tsx \
        frontend/packages/web/messages/en.json \
        frontend/packages/web/messages/zh.json \
        frontend/packages/web/__tests__/e2e/mcp/admin-mcp.spec.ts \
        backend/tests/e2e/test_mcp_auto_enroll.py
git commit -m "feat(mcp): align admin terminology with connector model"
```

---

## Task 10: Final Verification and Risk Review

**Files:**

- Modify: this plan only if verification finds command or path drift.

- [ ] **Step 1: Run backend MCP slices**

Run:

```bash
cd backend
uv run pytest -q tests/unit/mcp tests/unit/test_mcp_*.py
uv run pytest -q tests/e2e/test_mcp_catalog_routes.py \
                 tests/e2e/test_mcp_effective_connectors.py \
                 tests/e2e/test_mcp_catalog_runtime.py \
                 tests/e2e/test_mcp_auto_enroll.py \
                 tests/e2e/test_mcp_user_credentials.py \
                 tests/e2e/test_mcp_bindings.py \
                 tests/e2e/test_ws_settings.py
```

Expected: all selected backend tests pass.

- [ ] **Step 2: Run backend quality gate**

Run:

```bash
cd backend
make check
```

Expected: format, lint, type-check, and tests pass.

- [ ] **Step 3: Run frontend quality gate**

Run:

```bash
cd frontend
pnpm --filter @cubebox/core type-check
pnpm --filter @cubebox/web type-check
pnpm --filter @cubebox/web test:e2e -- mcp/ws-mcp.spec.ts mcp/admin-mcp.spec.ts
```

Expected: all commands pass.

- [ ] **Step 4: Manual runtime smoke check**

Run:

```bash
./scripts/worktree-env show
cd backend
python main.py
```

In another shell:

```bash
cd frontend
pnpm dev
```

Open the `BASE_URL` printed by `.worktree.env`, install a no-auth MCP template in one
workspace, and confirm it appears as ready in workspace settings without creating a
credential grant.

- [ ] **Step 5: Review known design risks**

Confirm these are true before merging:

- `catalog` remains only as a compatibility name for old URLs and DB tables.
- Product copy uses template, install, workspace state, and grant.
- Runtime reads one effective-state path.
- `auth_method="none"` never creates a user credential requirement.
- `install_status` separates deletion from credential readiness.
- Workspace-local installs are workspace-scoped, not creator-private.
- Disconnecting a user grant does not uninstall the connector.

- [ ] **Step 6: Final commit if verification changed files**

```bash
git status --short
git add <changed-files>
git commit -m "test(mcp): verify four-layer connector management"
```

If `git status --short` is empty, no commit is needed.
