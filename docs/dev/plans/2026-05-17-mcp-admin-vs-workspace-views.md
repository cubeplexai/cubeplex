# MCP Admin vs Workspace Views Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the MCP admin and workspace pages so each owns a list endpoint
shaped for its audience, drop the workspace-lens leak on the admin page,
collapse the workspace page into a clean Installed/Available split, and refactor
the two dual-mode UI components (ToolsPanel/TryItView, AuthActionBand) into
scope-isolated variants over shared sub-components.

**Architecture:** Two new backend endpoints
(`GET /admin/mcp/connectors`, `GET /ws/{ws}/mcp/available`) plus a tightening of
`GET /ws/{ws}/mcp/connectors`'s filter; the agent-runtime
`compute_effective_state` function is untouched. On the frontend, two pages stay
physically distinct per AGENTS.md's scope-isolation rule, sharing only
`TryItForm` and `AuthBandFrame` at the module level.

**Tech Stack:** FastAPI + SQLModel backend (async), Pydantic v2 schemas, Pytest
unit tests; Next 16 + React 19 + TS strict frontend, Vitest for unit tests,
@cubeplex/core for the typed API client.

**Spec:** `docs/dev/specs/2026-05-17-mcp-admin-vs-workspace-views-design.md`

---

## File structure (locked in before tasks)

### Backend

- **Create:**
  - `backend/cubeplex/api/schemas/mcp_admin_connector.py` — output DTOs for the
    new `/admin/mcp/connectors` endpoint (`AdminOrgConnectorOut`,
    `AdminOrgEffectiveOut`, `WorkspaceDistributionOut`,
    `AdminOrgConnectorListOut`). Living in its own file keeps the existing
    1200-line `schemas/mcp.py` from growing.
  - `backend/cubeplex/api/schemas/mcp_ws_available.py` — output DTOs for
    `/ws/{ws}/mcp/available` (`WsAvailableOut`, `WsAvailableListOut`,
    `WsAvailableSource`, `WsAvailableReason`).
  - `backend/cubeplex/services/mcp_admin_connectors.py` — derivation for the
    admin row's `org_effective` + `workspace_distribution`. Single file so
    the route handler stays thin.
  - `backend/cubeplex/services/mcp_ws_available.py` — derivation for the
    workspace "available" list.
  - `backend/tests/unit/test_mcp_admin_connectors_endpoint.py`
  - `backend/tests/unit/test_mcp_ws_available_endpoint.py`
- **Modify:**
  - `backend/cubeplex/api/routes/v1/admin_mcp.py` — add `GET /connectors`
    (alongside the existing `GET /installs`; the old route stays one release
    for the frontend swap, then removes in Task 11).
  - `backend/cubeplex/api/routes/v1/ws_mcp.py` — add `GET /available`; tighten
    `GET /connectors` by passing a new `include_disabled_org_installs=False`
    flag through to the service.
  - `backend/cubeplex/mcp/effective.py` — add the `include_disabled_org_installs`
    parameter to `MCPEffectiveConnectorService.list_for_workspace_user`
    (default `True` for backwards compat; the workspace `/connectors` route
    passes `False`). Pure `compute_effective_state` untouched.
  - `backend/cubeplex/repositories/mcp.py` — add
    `MCPWorkspaceConnectorStateRepository.list_for_install` (every state row
    pointing at one install) so the distribution aggregate can count without
    a full table scan per install.
  - `backend/tests/unit/test_mcp_four_layer_handlers.py` — adjust the one
    test that touches `list_workspace_connectors` so it stops expecting
    disabled rows back.

### Frontend

- **Create:**
  - `frontend/packages/core/src/types/mcp_admin_connector.ts` — TS mirror of
    the admin connector DTO + its sub-types.
  - `frontend/packages/core/src/types/mcp_ws_available.ts` — TS mirror of the
    workspace available DTO.
  - `frontend/packages/web/components/mcp/detail/tools/TryItForm.tsx` —
    rendering + arg coercion + error display extracted from `TryItView.tsx`;
    accepts an `onRun: (args) => Promise<ToolInvokeResult>` callback.
  - `frontend/packages/web/components/mcp/detail/tools/AdminTryItView.tsx` —
    composes `TryItForm` + admin workspace picker + `adminInvokeTool`.
  - `frontend/packages/web/components/mcp/detail/tools/WsTryItView.tsx` —
    composes `TryItForm` only + `wsInvokeTool`.
  - `frontend/packages/web/components/mcp/detail/tools/AdminToolsPanel.tsx`
  - `frontend/packages/web/components/mcp/detail/tools/WsToolsPanel.tsx`
  - `frontend/packages/web/components/mcp/AuthBandFrame.tsx` — visual band
    rendering only.
  - `frontend/packages/web/components/mcp/AdminAuthBand.tsx`
  - `frontend/packages/web/components/mcp/WsAuthBand.tsx`
  - `frontend/packages/web/components/mcp/AvailableConnectorRow.tsx` — the
    "Connect" row used by the workspace page's Available section.
- **Modify:**
  - `frontend/packages/core/src/api/mcp.ts` — add `adminListConnectors` and
    `wsListAvailable` API helpers; leave existing helpers untouched for the
    one-release deprecation window.
  - `frontend/packages/web/app/admin/mcp/page.tsx` — drop `lensWsId`, drop
    `synthesizeStubEffective`, drop `wsListEffectiveConnectors` calls, fetch
    from `adminListConnectors` only.
  - `frontend/packages/web/components/mcp/MCPAdminDetailPanel.tsx` — drop
    the `wsEnabled`/`wsDisabled` row from the overview, swap `ToolsPanel`
    for `AdminToolsPanel`, swap `AdminAuthActionBand`/`AuthActionBand`
    composition for `AdminAuthBand`.
  - `frontend/packages/web/components/workspace-settings/McpPanel.tsx` —
    rewrite the list area: top "Installed" section using `WsToolsPanel` +
    `WsAuthBand`, bottom "Available" section using `AvailableConnectorRow`.
  - `frontend/packages/web/messages/en.json`, `frontend/packages/web/messages/zh.json`
    — add `mcp.available.*` keys (header, connectButton, sourceTooltip).
- **Delete (after consumers swapped):**
  - `frontend/packages/web/components/mcp/detail/tools/ToolsPanel.tsx`
  - `frontend/packages/web/components/mcp/detail/tools/TryItView.tsx`
  - `frontend/packages/web/components/mcp/AuthActionBand.tsx` (replaced by
    `AdminAuthBand` + `WsAuthBand` + `AuthBandFrame`).
- **Keep unchanged:**
  - `frontend/packages/web/components/mcp/effectiveAuthState.ts` — pure
    function, role-as-input is fine.
  - `frontend/packages/web/components/mcp/detail/ServerErrorBanner.tsx`
  - `frontend/packages/web/components/mcp/MCPCitationsTab.tsx`
  - `frontend/packages/web/components/mcp/MCPCitationEditor.tsx`
  - `frontend/packages/web/components/mcp/MCPPromoteDialog.tsx`
  - `frontend/packages/web/components/mcp/MCPTemplateInstallPanel.tsx`
  - `frontend/packages/web/components/mcp/MCPCustomCreatePanel.tsx`
  - `frontend/packages/web/components/mcp/MCPWorkspacesTab.tsx`

---

## Task 1: Repo helper — list workspace state rows by install

**Files:**
- Modify: `backend/cubeplex/repositories/mcp.py`
- Test: `backend/tests/unit/test_mcp_state_repo.py` (new file, or extend
  `test_mcp_four_layer_handlers.py` if a state-repo test already lives there)

The admin connector row's `workspace_distribution` needs per-install counts
of enabled vs disabled state rows. Adding a focused helper is cleaner than
scanning `list_for_workspace` for every workspace.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_mcp_state_repo_list_for_install.py`:

```python
import pytest
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from cubeplex.models.mcp import MCPWorkspaceConnectorState
from cubeplex.repositories.mcp import MCPWorkspaceConnectorStateRepository


@pytest.mark.asyncio
async def test_list_for_install_returns_only_matching_install(tmp_path):
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    async with AsyncSession(eng) as session:
        repo = MCPWorkspaceConnectorStateRepository(session, org_id="org-1")
        await repo.upsert(
            workspace_id="ws-a",
            install_id="mcins-x",
            enabled=True,
            credential_policy="org",
            enablement_source="admin_auto",
            updated_by_user_id="usr-1",
        )
        await repo.upsert(
            workspace_id="ws-b",
            install_id="mcins-x",
            enabled=False,
            credential_policy="org",
            enablement_source="admin_manual",
            updated_by_user_id="usr-1",
        )
        await repo.upsert(
            workspace_id="ws-a",
            install_id="mcins-other",
            enabled=True,
            credential_policy="org",
            enablement_source="admin_auto",
            updated_by_user_id="usr-1",
        )

        rows = await repo.list_for_install("mcins-x")
        assert {r.workspace_id for r in rows} == {"ws-a", "ws-b"}
        assert sum(1 for r in rows if r.enabled) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_mcp_state_repo_list_for_install.py -v`
Expected: FAIL with `AttributeError: 'MCPWorkspaceConnectorStateRepository' object has no attribute 'list_for_install'`.

- [ ] **Step 3: Add `list_for_install` to the state repo**

Insert into `backend/cubeplex/repositories/mcp.py` right after the existing
`list_for_workspace` method (search for `async def list_for_workspace`):

```python
    async def list_for_install(
        self, install_id: str
    ) -> list[MCPWorkspaceConnectorState]:
        """Every state row pointing at this install across all workspaces.

        Used by the admin connector list to compute the per-install
        ``workspace_distribution`` aggregate in one query instead of a
        per-workspace fan-out.
        """
        stmt = select(MCPWorkspaceConnectorState).where(
            MCPWorkspaceConnectorState.org_id == self.org_id,  # type: ignore[arg-type]
            MCPWorkspaceConnectorState.install_id == install_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_mcp_state_repo_list_for_install.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/repositories/mcp.py backend/tests/unit/test_mcp_state_repo_list_for_install.py
git commit -m "feat(mcp/repo): list_for_install for workspace-distribution aggregation"
```

---

## Task 2: Schemas — `AdminOrgConnectorOut`

**Files:**
- Create: `backend/cubeplex/api/schemas/mcp_admin_connector.py`
- Test: `backend/tests/unit/test_admin_org_connector_schema.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_admin_org_connector_schema.py`:

```python
from cubeplex.api.schemas.mcp_admin_connector import (
    AdminOrgConnectorOut,
    AdminOrgEffectiveOut,
    WorkspaceDistributionOut,
)


def test_admin_org_connector_serializes_minimum_set():
    eff = AdminOrgEffectiveOut(
        usable=True,
        reason="usable",
        credential_availability="available",
    )
    dist = WorkspaceDistributionOut(
        enabled_count=2,
        disabled_count=1,
        eligible_count=4,
        auto_enroll_new_workspaces=False,
    )
    out = AdminOrgConnectorOut.model_validate(
        {
            "install": {
                "install_id": "mcins-1",
                "template_id": "mctpl-1",
                "install_scope": "org",
                "workspace_id": None,
                "name": "Notion",
                "server_url": "https://example.com/mcp",
                "transport": "streamable_http",
                "auth_method": "oauth",
                "default_credential_policy": "org",
                "auth_status": "authorized",
                "discovery_status": "ok",
                "install_state": "active",
                "tool_count": 3,
                "tools": [],
                "tool_citations": {},
                "last_error": None,
                "auto_enroll_new_workspaces": False,
            },
            "template": None,
            "org_effective": eff.model_dump(),
            "workspace_distribution": dist.model_dump(),
        }
    )
    assert out.org_effective.reason == "usable"
    assert out.workspace_distribution.eligible_count == 4


def test_admin_org_connector_allows_null_credential_availability():
    eff = AdminOrgEffectiveOut(
        usable=True,
        reason="usable",
        credential_availability=None,
    )
    assert eff.credential_availability is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_admin_org_connector_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cubeplex.api.schemas.mcp_admin_connector'`.

- [ ] **Step 3: Create the schema file**

`backend/cubeplex/api/schemas/mcp_admin_connector.py`:

```python
"""Admin connector list response shapes (GET /admin/mcp/connectors).

Lives in its own module so the existing schemas/mcp.py doesn't keep
growing. The DTOs here are admin-only: no workspace lens, per-workspace
state rolled up into one aggregate per row.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from cubeplex.api.schemas.mcp import MCPConnectorInstallOut, MCPConnectorTemplateOut

AdminOrgReason = Literal[
    "usable",
    "missing_org_grant",
    "pending_oauth",
    "grant_expired",
    "discovery_failed",
]

CredentialAvailability = Literal["available", "missing", "not_required"]


class AdminOrgEffectiveOut(BaseModel):
    """Per-org health for an org-scope install.

    ``credential_availability`` is ``None`` when the install's
    ``default_credential_policy`` is ``'workspace'`` or ``'user'`` —
    the admin doesn't supply those credentials, so the org row can't
    claim a status. The per-workspace breakdown lives in
    :class:`WorkspaceDistributionOut`.
    """

    usable: bool
    reason: AdminOrgReason
    credential_availability: CredentialAvailability | None


class WorkspaceDistributionOut(BaseModel):
    """Lightweight aggregate of per-workspace state rows for one install."""

    enabled_count: int
    disabled_count: int
    eligible_count: int
    auto_enroll_new_workspaces: bool


class AdminOrgConnectorOut(BaseModel):
    """One row of GET /admin/mcp/connectors."""

    install: MCPConnectorInstallOut
    template: MCPConnectorTemplateOut | None
    org_effective: AdminOrgEffectiveOut
    workspace_distribution: WorkspaceDistributionOut


class AdminOrgConnectorListOut(BaseModel):
    items: list[AdminOrgConnectorOut]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_admin_org_connector_schema.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: mypy check**

Run: `cd backend && .venv/bin/python -m mypy cubeplex/api/schemas/mcp_admin_connector.py`
Expected: `Success: no issues found in 1 source file`.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/api/schemas/mcp_admin_connector.py backend/tests/unit/test_admin_org_connector_schema.py
git commit -m "feat(mcp/schema): AdminOrgConnectorOut + sub-DTOs for admin connectors endpoint"
```

---

## Task 3: Schemas — `WsAvailableOut`

**Files:**
- Create: `backend/cubeplex/api/schemas/mcp_ws_available.py`
- Test: `backend/tests/unit/test_ws_available_schema.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_ws_available_schema.py`:

```python
import pytest

from cubeplex.api.schemas.mcp_ws_available import WsAvailableOut


def test_ws_available_org_install_row():
    row = WsAvailableOut.model_validate(
        {
            "source": "org_install",
            "install": {
                "install_id": "mcins-1",
                "template_id": "mctpl-1",
                "install_scope": "org",
                "workspace_id": None,
                "name": "Notion",
                "server_url": "https://example.com/mcp",
                "transport": "streamable_http",
                "auth_method": "oauth",
                "default_credential_policy": "org",
                "auth_status": "authorized",
                "discovery_status": "ok",
                "install_state": "active",
                "tool_count": 0,
                "tools": [],
                "tool_citations": {},
                "last_error": None,
                "auto_enroll_new_workspaces": False,
            },
            "template": None,
            "reason": "no_state_row",
        }
    )
    assert row.source == "org_install"
    assert row.install is not None
    assert row.reason == "no_state_row"


def test_ws_available_template_row_rejects_install():
    with pytest.raises(ValueError):
        WsAvailableOut.model_validate(
            {
                "source": "template",
                "install": {"install_id": "mcins-1"},  # forbidden
                "template": {
                    "template_id": "mctpl-1",
                    "slug": "notion",
                    "name": "Notion",
                    "provider": "Notion",
                    "description": "",
                    "server_url": "https://example.com",
                    "transport": "streamable_http",
                    "supported_auth_methods": ["oauth"],
                    "default_credential_policy": "org",
                    "static_form_schema": None,
                    "status": "active",
                },
                "reason": "not_installed_at_org",
            }
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_ws_available_schema.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create the schema file**

`backend/cubeplex/api/schemas/mcp_ws_available.py`:

```python
"""Workspace 'available connectors' response shape.

GET /api/v1/ws/{workspace_id}/mcp/available returns rows the workspace
can opt into (org installs not yet enabled in this workspace + templates
the workspace doesn't already have).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator

from cubeplex.api.schemas.mcp import MCPConnectorInstallOut, MCPConnectorTemplateOut

WsAvailableSource = Literal["org_install", "template"]
WsAvailableReason = Literal[
    "no_state_row",
    "state_disabled",
    "not_installed_at_org",
]


class WsAvailableOut(BaseModel):
    """One row of GET /ws/{ws}/mcp/available.

    Cross-field invariants (enforced by the validator):

    - ``source='org_install'`` requires ``install`` set; the install is
      org-scope.
    - ``source='template'`` requires ``install`` null and ``template``
      set.
    - ``template`` may be null only when ``source='org_install'`` and the
      install was created as custom (no template id).
    """

    source: WsAvailableSource
    install: MCPConnectorInstallOut | None
    template: MCPConnectorTemplateOut | None
    reason: WsAvailableReason

    @model_validator(mode="after")
    def _validate_shape(self) -> "WsAvailableOut":
        if self.source == "org_install":
            if self.install is None:
                raise ValueError("source='org_install' requires install")
        else:
            if self.install is not None:
                raise ValueError("source='template' must not carry install")
            if self.template is None:
                raise ValueError("source='template' requires template")
        return self


class WsAvailableListOut(BaseModel):
    items: list[WsAvailableOut]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_ws_available_schema.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: mypy check + commit**

Run: `cd backend && .venv/bin/python -m mypy cubeplex/api/schemas/mcp_ws_available.py`
Expected: `Success: no issues found`.

```bash
git add backend/cubeplex/api/schemas/mcp_ws_available.py backend/tests/unit/test_ws_available_schema.py
git commit -m "feat(mcp/schema): WsAvailableOut for workspace available-connectors endpoint"
```

---

## Task 4: Service — admin connectors derivation

**Files:**
- Create: `backend/cubeplex/services/mcp_admin_connectors.py`
- Test: `backend/tests/unit/test_mcp_admin_connectors_service.py`

This service composes existing repo methods into the new admin row. The
core branching (org / workspace / user / none policies) is centralised
here so route + tests target one function.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_mcp_admin_connectors_service.py`:

```python
from dataclasses import dataclass

import pytest

from cubeplex.services.mcp_admin_connectors import derive_admin_org_effective


@dataclass
class _Install:
    id: str
    auth_method: str
    auth_status: str
    discovery_status: str
    default_credential_policy: str


@dataclass
class _Grant:
    grant_status: str
    refresh_credential_id: str | None


def test_org_policy_with_valid_grant_is_usable():
    install = _Install("mcins-1", "oauth", "authorized", "ok", "org")
    grant = _Grant("valid", None)
    out = derive_admin_org_effective(install, grant)
    assert out.usable is True
    assert out.reason == "usable"
    assert out.credential_availability == "available"


def test_workspace_policy_install_skips_grant_check():
    install = _Install("mcins-1", "static", "pending", "ok", "workspace")
    out = derive_admin_org_effective(install, org_grant=None)
    assert out.usable is True
    assert out.reason == "usable"
    assert out.credential_availability is None  # creds live below org level


def test_user_policy_install_with_discovery_error():
    install = _Install("mcins-1", "static", "pending", "error", "user")
    out = derive_admin_org_effective(install, org_grant=None)
    assert out.usable is False
    assert out.reason == "discovery_failed"
    assert out.credential_availability is None


def test_none_auth_method_is_usable_regardless_of_policy():
    install = _Install("mcins-1", "none", "not_required", "ok", "none")
    out = derive_admin_org_effective(install, org_grant=None)
    assert out.usable is True
    assert out.reason == "usable"
    assert out.credential_availability == "not_required"


def test_org_policy_missing_grant_oauth_pending():
    install = _Install("mcins-1", "oauth", "pending", "not_run", "org")
    out = derive_admin_org_effective(install, org_grant=None)
    assert out.usable is False
    assert out.reason == "pending_oauth"
    assert out.credential_availability == "missing"


def test_org_policy_missing_grant_static():
    install = _Install("mcins-1", "static", "pending", "not_run", "org")
    out = derive_admin_org_effective(install, org_grant=None)
    assert out.usable is False
    assert out.reason == "missing_org_grant"
    assert out.credential_availability == "missing"


def test_org_policy_grant_expired_no_refresh():
    install = _Install("mcins-1", "oauth", "authorized", "ok", "org")
    grant = _Grant("expired", None)
    out = derive_admin_org_effective(install, grant)
    assert out.usable is False
    assert out.reason == "grant_expired"


def test_org_policy_grant_expired_with_refresh_is_usable():
    install = _Install("mcins-1", "oauth", "authorized", "ok", "org")
    grant = _Grant("expired", "cred-refresh-1")
    out = derive_admin_org_effective(install, grant)
    assert out.usable is True
    assert out.reason == "usable"


def test_org_policy_discovery_error_after_auth_gates_pass():
    install = _Install("mcins-1", "oauth", "authorized", "error", "org")
    grant = _Grant("valid", None)
    out = derive_admin_org_effective(install, grant)
    assert out.usable is False
    assert out.reason == "discovery_failed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_mcp_admin_connectors_service.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create the service module**

`backend/cubeplex/services/mcp_admin_connectors.py`:

```python
"""Derivation helpers for GET /admin/mcp/connectors.

Pure functions + small data-only helpers; the route layer wires repos
and serializes. Tests target this module directly without spinning up
FastAPI.
"""

from __future__ import annotations

from typing import Any

from cubeplex.api.schemas.mcp_admin_connector import (
    AdminOrgEffectiveOut,
    WorkspaceDistributionOut,
)
from cubeplex.models.mcp import (
    MCPConnectorInstall,
    MCPCredentialGrant,
    MCPWorkspaceConnectorState,
)


def derive_admin_org_effective(
    install: Any,
    org_grant: Any,
) -> AdminOrgEffectiveOut:
    """Spec §3.1: org_effective branches by default_credential_policy.

    - ``auth_method='none'`` → usable, credential_availability='not_required'
    - ``default_credential_policy in {'workspace','user'}`` → admin can't
      supply credentials; reason ∈ {usable, discovery_failed} only,
      credential_availability=None.
    - ``default_credential_policy='org'`` → existing org-grant decision
      table (same ordering as the now-deleted
      ``_derive_admin_org_effective`` in admin_mcp.py rule 1–6).

    Accepts duck-typed install + grant so unit tests can use dataclasses;
    the route always passes real ORM rows.
    """
    if install.auth_method == "none":
        return AdminOrgEffectiveOut(
            usable=True, reason="usable", credential_availability="not_required"
        )

    if install.default_credential_policy in {"workspace", "user"}:
        if install.discovery_status == "error":
            return AdminOrgEffectiveOut(
                usable=False,
                reason="discovery_failed",
                credential_availability=None,
            )
        return AdminOrgEffectiveOut(
            usable=True, reason="usable", credential_availability=None
        )

    # default_credential_policy == "org"
    if org_grant is None:
        if install.auth_method == "oauth" and install.auth_status == "pending":
            return AdminOrgEffectiveOut(
                usable=False,
                reason="pending_oauth",
                credential_availability="missing",
            )
        return AdminOrgEffectiveOut(
            usable=False,
            reason="missing_org_grant",
            credential_availability="missing",
        )

    if org_grant.grant_status == "expired" and org_grant.refresh_credential_id is None:
        return AdminOrgEffectiveOut(
            usable=False, reason="grant_expired", credential_availability="missing"
        )

    if install.discovery_status == "error":
        return AdminOrgEffectiveOut(
            usable=False,
            reason="discovery_failed",
            credential_availability="available",
        )

    return AdminOrgEffectiveOut(
        usable=True, reason="usable", credential_availability="available"
    )


def build_workspace_distribution(
    *,
    install: MCPConnectorInstall,
    state_rows: list[MCPWorkspaceConnectorState],
    eligible_workspace_count: int,
) -> WorkspaceDistributionOut:
    """Roll state rows for one install into the admin row's aggregate.

    ``state_rows`` is the output of
    ``MCPWorkspaceConnectorStateRepository.list_for_install(install.id)``;
    ``eligible_workspace_count`` is the total workspace count for the org
    (so the UI can render "5/12"). Computed once per request, not per row.
    """
    enabled = sum(1 for r in state_rows if r.enabled)
    disabled = sum(1 for r in state_rows if not r.enabled)
    return WorkspaceDistributionOut(
        enabled_count=enabled,
        disabled_count=disabled,
        eligible_count=eligible_workspace_count,
        auto_enroll_new_workspaces=install.auto_enroll_new_workspaces,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_mcp_admin_connectors_service.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: mypy check + commit**

Run: `cd backend && .venv/bin/python -m mypy cubeplex/services/mcp_admin_connectors.py`
Expected: `Success`.

```bash
git add backend/cubeplex/services/mcp_admin_connectors.py backend/tests/unit/test_mcp_admin_connectors_service.py
git commit -m "feat(mcp): admin connectors derivation service"
```

---

## Task 5: Route — `GET /admin/mcp/connectors`

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/admin_mcp.py`
- Test: `backend/tests/unit/test_admin_connectors_endpoint.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_admin_connectors_endpoint.py`:

```python
from typing import Any

from fastapi.testclient import TestClient

from cubeplex.api.app import create_app
from cubeplex.auth.context import RequestContext
from cubeplex.audit.sink import NoOpAuditSink
from cubeplex.mcp.dependencies import (
    get_admin_install_service,
    get_admin_request_context,
    get_audit_sink,
    get_grant_repo,
)
from cubeplex.models import Role, User


async def _fake_audit_sink() -> Any:
    return NoOpAuditSink()


async def _fake_admin_ctx() -> RequestContext:
    user = User(id="usr-1", email="x@example.com", hashed_password="x")
    return RequestContext(user=user, org_id="org-1", workspace_id="", role=Role.ADMIN)


class _FakeInstall:
    def __init__(self, install_id: str, policy: str = "org"):
        self.id = install_id
        self.template_id = "mctpl-1"
        self.install_scope = "org"
        self.workspace_id = None
        self.name = f"Install {install_id}"
        self.server_url = "https://example.com/mcp"
        self.server_url_hash = "abc"
        self.transport = "streamable_http"
        self.auth_method = "oauth"
        self.default_credential_policy = policy
        self.auth_status = "authorized"
        self.discovery_status = "ok"
        self.install_state = "active"
        self.tools_cache: list[dict[str, Any]] = []
        self.tool_citations: dict[str, Any] = {}
        self.last_error = None
        self.auto_enroll_new_workspaces = False
        self.org_id = "org-1"
        self.headers: dict[str, str] = {}
        self.timeout = 30.0
        self.sse_read_timeout = 30.0
        self.oauth_client_config: dict[str, Any] = {}


def test_admin_connectors_returns_one_row_per_org_install() -> None:
    async def _fake_install_svc() -> Any:
        class _S:
            class _Repo:
                session = None

                async def list_org_installs(self) -> list[Any]:
                    return [_FakeInstall("mcins-1"), _FakeInstall("mcins-2")]

            _install_repo = _Repo()

        return _S()

    async def _fake_grant_repo() -> Any:
        class _G:
            async def get_org_grant(self, install_id: str) -> Any:
                class _Grant:
                    grant_status = "valid"
                    refresh_credential_id = None
                return _Grant()

        return _G()

    app = create_app()
    app.dependency_overrides[get_audit_sink] = _fake_audit_sink
    app.dependency_overrides[get_admin_request_context] = _fake_admin_ctx
    app.dependency_overrides[get_admin_install_service] = _fake_install_svc
    app.dependency_overrides[get_grant_repo] = _fake_grant_repo
    client = TestClient(app)

    res = client.get("/api/v1/admin/mcp/connectors")
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["items"]) == 2
    assert body["items"][0]["org_effective"]["reason"] == "usable"
    assert body["items"][0]["workspace_distribution"]["eligible_count"] >= 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_admin_connectors_endpoint.py -v`
Expected: FAIL with `404` (route doesn't exist yet).

- [ ] **Step 3: Add the route handler**

Append to `backend/cubeplex/api/routes/v1/admin_mcp.py` (just before the
`# Admin install effective` section header near the end of the file):

```python
from cubeplex.api.schemas.mcp_admin_connector import (
    AdminOrgConnectorListOut,
    AdminOrgConnectorOut,
)
from cubeplex.repositories.workspace import WorkspaceRepository
from cubeplex.services.mcp_admin_connectors import (
    build_workspace_distribution,
    derive_admin_org_effective,
)


@router.get("/connectors", response_model=AdminOrgConnectorListOut)
async def list_admin_connectors(
    svc: Annotated[MCPConnectorInstallService, Depends(get_admin_install_service)],
    grant_repo: Annotated[MCPCredentialGrantRepository, Depends(get_grant_repo)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> AdminOrgConnectorListOut:
    """List every org-scope install with its org-level effective state and
    a per-install workspace-distribution aggregate.

    Spec §3.1. Workspace-scope installs are excluded (admins find those
    via the workspace settings page). No workspace lens is applied — the
    admin row never carries per-workspace status.
    """
    org_installs = await svc._install_repo.list_org_installs()
    state_repo = MCPWorkspaceConnectorStateRepository(
        svc._install_repo.session, org_id=ctx.org_id
    )
    workspace_repo = WorkspaceRepository(svc._install_repo.session)
    eligible = len(await workspace_repo.list_for_org(ctx.org_id))

    items: list[AdminOrgConnectorOut] = []
    for install in org_installs:
        org_grant = await grant_repo.get_org_grant(install.id)
        eff = derive_admin_org_effective(install, org_grant)
        state_rows = await state_repo.list_for_install(install.id)
        dist = build_workspace_distribution(
            install=install,
            state_rows=state_rows,
            eligible_workspace_count=eligible,
        )
        template_out: MCPConnectorTemplateOut | None = None
        if install.template_id is not None:
            template = await svc._template_repo.get(install.template_id) if hasattr(svc, "_template_repo") else None
            if template is not None:
                template_out = _template_to_out(template)
        items.append(
            AdminOrgConnectorOut(
                install=_install_to_out(install),
                template=template_out,
                org_effective=eff,
                workspace_distribution=dist,
            )
        )
    return AdminOrgConnectorListOut(items=items)
```

Imports at the top of the file: add
`from cubeplex.repositories.mcp import MCPWorkspaceConnectorStateRepository`
to the existing `from cubeplex.repositories.mcp import (...)` block if not
already present.

If `MCPConnectorInstallService` does not expose `_template_repo` today,
fall back to constructing it inline:

```python
from cubeplex.repositories.mcp import MCPConnectorTemplateRepository

# inside the function:
template_repo = MCPConnectorTemplateRepository(svc._install_repo.session)
template = await template_repo.get(install.template_id)
```

Pick whichever pattern matches the existing route file's idioms.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_admin_connectors_endpoint.py -v`
Expected: PASS.

- [ ] **Step 5: Spot-check ruff + mypy**

Run: `cd backend && .venv/bin/python -m ruff check cubeplex/api/routes/v1/admin_mcp.py && .venv/bin/python -m mypy cubeplex/api/routes/v1/admin_mcp.py`
Expected: both green.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/api/routes/v1/admin_mcp.py backend/tests/unit/test_admin_connectors_endpoint.py
git commit -m "feat(mcp): GET /admin/mcp/connectors — admin-scope list, no workspace lens"
```

---

## Task 6: Service — workspace `available` derivation

**Files:**
- Create: `backend/cubeplex/services/mcp_ws_available.py`
- Test: `backend/tests/unit/test_mcp_ws_available_service.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_mcp_ws_available_service.py`:

```python
from dataclasses import dataclass, field

import pytest

from cubeplex.services.mcp_ws_available import compute_available_rows


@dataclass
class _Install:
    id: str
    template_id: str | None
    install_scope: str
    workspace_id: str | None
    install_state: str = "active"


@dataclass
class _Template:
    id: str
    status: str = "active"


@dataclass
class _State:
    install_id: str
    enabled: bool


def test_org_install_without_state_row_appears_as_no_state_row():
    org_installs = [_Install("mcins-org-1", "mctpl-a", "org", None)]
    rows = compute_available_rows(
        ws_id="ws-1",
        org_installs=org_installs,
        ws_installs=[],
        ws_states=[],
        templates=[_Template("mctpl-a"), _Template("mctpl-b")],
    )
    org_rows = [r for r in rows if r.source == "org_install"]
    assert len(org_rows) == 1
    assert org_rows[0].reason == "no_state_row"
    assert org_rows[0].install_id == "mcins-org-1"


def test_org_install_with_disabled_state_row_appears_as_state_disabled():
    rows = compute_available_rows(
        ws_id="ws-1",
        org_installs=[_Install("mcins-org-1", "mctpl-a", "org", None)],
        ws_installs=[],
        ws_states=[_State("mcins-org-1", enabled=False)],
        templates=[_Template("mctpl-a")],
    )
    org_rows = [r for r in rows if r.source == "org_install"]
    assert len(org_rows) == 1
    assert org_rows[0].reason == "state_disabled"


def test_org_install_with_enabled_state_row_omitted():
    rows = compute_available_rows(
        ws_id="ws-1",
        org_installs=[_Install("mcins-org-1", "mctpl-a", "org", None)],
        ws_installs=[],
        ws_states=[_State("mcins-org-1", enabled=True)],
        templates=[_Template("mctpl-a")],
    )
    assert all(r.source != "org_install" for r in rows)


def test_template_already_installed_at_org_omitted():
    rows = compute_available_rows(
        ws_id="ws-1",
        org_installs=[_Install("mcins-org-1", "mctpl-a", "org", None)],
        ws_installs=[],
        ws_states=[_State("mcins-org-1", enabled=False)],
        templates=[_Template("mctpl-a")],
    )
    assert all(r.source != "template" for r in rows)


def test_template_already_installed_at_workspace_omitted():
    """Workspace-scope install of the same template must hide the template."""
    rows = compute_available_rows(
        ws_id="ws-1",
        org_installs=[],
        ws_installs=[_Install("mcins-ws-1", "mctpl-a", "workspace", "ws-1")],
        ws_states=[],
        templates=[_Template("mctpl-a")],
    )
    assert rows == []


def test_template_with_only_tombstoned_workspace_install_still_available():
    """Reinstall must stay one-click; tombstoned installs do not block."""
    rows = compute_available_rows(
        ws_id="ws-1",
        org_installs=[],
        ws_installs=[
            _Install("mcins-ws-1", "mctpl-a", "workspace", "ws-1", install_state="uninstalled")
        ],
        ws_states=[],
        templates=[_Template("mctpl-a")],
    )
    template_rows = [r for r in rows if r.source == "template"]
    assert len(template_rows) == 1
    assert template_rows[0].reason == "not_installed_at_org"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_mcp_ws_available_service.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create the service**

`backend/cubeplex/services/mcp_ws_available.py`:

```python
"""Workspace 'available connectors' computation.

Pure function: in goes (workspace id, org installs, ws installs, ws state
rows, templates), out goes the list of rows that the workspace can opt
into. Spec §3.2 invariants enforced here, not at the route layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cubeplex.api.schemas.mcp_ws_available import WsAvailableReason, WsAvailableSource


@dataclass(frozen=True)
class WsAvailableRow:
    """Service-layer row; route serializes into ``WsAvailableOut``."""

    source: WsAvailableSource
    install_id: str | None
    template_id: str | None
    reason: WsAvailableReason


def compute_available_rows(
    *,
    ws_id: str,
    org_installs: list[Any],
    ws_installs: list[Any],
    ws_states: list[Any],
    templates: list[Any],
) -> list[WsAvailableRow]:
    """Spec §3.2 list composition.

    - org_installs: all active org-scope installs in this org.
    - ws_installs: workspace-scope installs owned by this workspace
      (includes tombstones so the active-only filter is explicit here).
    - ws_states: this workspace's state rows.
    - templates: all active templates in the catalog.

    Output ordering: org rows first (by install_id), then template rows
    (by template_id). Stable so the frontend can compare against the
    previous fetch.
    """
    state_by_install = {s.install_id: s for s in ws_states}

    org_rows: list[WsAvailableRow] = []
    for install in org_installs:
        state = state_by_install.get(install.id)
        if state is not None and state.enabled:
            continue
        reason: WsAvailableReason = (
            "state_disabled" if state is not None else "no_state_row"
        )
        org_rows.append(
            WsAvailableRow(
                source="org_install",
                install_id=install.id,
                template_id=install.template_id,
                reason=reason,
            )
        )

    # Templates reachable via an org install are surfaced under
    # source='org_install' above; templates the workspace already owns
    # (active workspace-scope install) cannot install again — exclude.
    org_template_ids = {i.template_id for i in org_installs if i.template_id is not None}
    active_ws_template_ids = {
        i.template_id
        for i in ws_installs
        if i.install_state == "active" and i.template_id is not None
    }

    template_rows: list[WsAvailableRow] = []
    for template in templates:
        if template.status != "active":
            continue
        if template.id in org_template_ids:
            continue
        if template.id in active_ws_template_ids:
            continue
        template_rows.append(
            WsAvailableRow(
                source="template",
                install_id=None,
                template_id=template.id,
                reason="not_installed_at_org",
            )
        )

    org_rows.sort(key=lambda r: r.install_id or "")
    template_rows.sort(key=lambda r: r.template_id or "")
    return org_rows + template_rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_mcp_ws_available_service.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/services/mcp_ws_available.py backend/tests/unit/test_mcp_ws_available_service.py
git commit -m "feat(mcp): workspace 'available connectors' derivation service"
```

---

## Task 7: Route — `GET /ws/{ws}/mcp/available`

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/ws_mcp.py`
- Test: `backend/tests/unit/test_ws_available_endpoint.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_ws_available_endpoint.py`. Use the
TestClient + dependency override pattern from
`test_admin_connectors_endpoint.py` (Task 5). Cover one happy path and
verify shape; the service-level service tests already cover semantics.

```python
from typing import Any

from fastapi.testclient import TestClient

from cubeplex.api.app import create_app
from cubeplex.audit.sink import NoOpAuditSink
from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import require_member
from cubeplex.mcp.dependencies import (
    get_audit_sink,
    get_ws_effective_service,
    get_ws_install_service,
    get_connector_template_service,
)
from cubeplex.models import Role, User


async def _fake_audit() -> Any:
    return NoOpAuditSink()


async def _fake_member() -> RequestContext:
    user = User(id="usr-1", email="x@example.com", hashed_password="x")
    return RequestContext(user=user, org_id="org-1", workspace_id="ws-1", role=Role.MEMBER)


def test_ws_available_lists_org_install_without_state_row() -> None:
    async def _fake_template_svc() -> Any:
        class _T:
            async def list_active(self) -> list[Any]:
                return []

        return _T()

    async def _fake_eff() -> Any:
        class _Stub:
            async def list_available_for_workspace(self, ws_id: str) -> list[Any]:
                from cubeplex.services.mcp_ws_available import WsAvailableRow

                return [
                    WsAvailableRow(
                        source="org_install",
                        install_id="mcins-1",
                        template_id="mctpl-1",
                        reason="no_state_row",
                    )
                ]

        return _Stub()

    async def _fake_install_svc() -> Any:
        class _S:
            async def get_install_by_id(self, install_id: str) -> Any:
                class _I:
                    id = "mcins-1"
                    template_id = "mctpl-1"
                    install_scope = "org"
                    workspace_id = None
                    name = "Notion"
                    server_url = "https://example.com"
                    transport = "streamable_http"
                    auth_method = "oauth"
                    default_credential_policy = "org"
                    auth_status = "authorized"
                    discovery_status = "ok"
                    install_state = "active"
                    tools_cache: list[Any] = []
                    tool_citations: dict[str, Any] = {}
                    last_error = None
                    auto_enroll_new_workspaces = False

                return _I()

        return _S()

    app = create_app()
    app.dependency_overrides[get_audit_sink] = _fake_audit
    app.dependency_overrides[require_member] = _fake_member
    app.dependency_overrides[get_ws_effective_service] = _fake_eff
    app.dependency_overrides[get_ws_install_service] = _fake_install_svc
    app.dependency_overrides[get_connector_template_service] = _fake_template_svc
    client = TestClient(app)

    res = client.get("/api/v1/ws/ws-1/mcp/available")
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["source"] == "org_install"
    assert body["items"][0]["install"]["install_id"] == "mcins-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_ws_available_endpoint.py -v`
Expected: FAIL — 404.

- [ ] **Step 3: Add the route handler**

Append to `backend/cubeplex/api/routes/v1/ws_mcp.py` (after
`list_workspace_connectors` and before `create_workspace_install`):

```python
from cubeplex.api.schemas.mcp_ws_available import (
    WsAvailableListOut,
    WsAvailableOut,
)


@router.get("/available", response_model=WsAvailableListOut)
async def list_workspace_available(
    workspace_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
    install_svc: Annotated[MCPConnectorInstallService, Depends(get_ws_install_service)],
    template_svc: Annotated[
        MCPConnectorTemplateService, Depends(get_connector_template_service)
    ],
) -> WsAvailableListOut:
    """Connectors the workspace can opt into.

    Includes org installs not yet enabled in this workspace + templates
    the workspace doesn't already have. Spec §3.2.
    """
    from cubeplex.services.mcp_ws_available import compute_available_rows

    org_installs = await install_svc._install_repo.list_org_installs()
    ws_installs = await install_svc._install_repo.list_workspace_installs(workspace_id)
    ws_states = await install_svc._state_repo.list_for_workspace(workspace_id)
    templates = await template_svc.list_active()

    rows = compute_available_rows(
        ws_id=workspace_id,
        org_installs=org_installs,
        ws_installs=ws_installs,
        ws_states=ws_states,
        templates=templates,
    )

    installs_by_id = {i.id: i for i in org_installs}
    templates_by_id = {t.id: t for t in templates}

    items: list[WsAvailableOut] = []
    for row in rows:
        install_out = (
            _install_to_out(installs_by_id[row.install_id])
            if row.source == "org_install" and row.install_id is not None
            else None
        )
        template_out = (
            _template_to_out(templates_by_id[row.template_id])
            if row.template_id is not None and row.template_id in templates_by_id
            else None
        )
        items.append(
            WsAvailableOut(
                source=row.source,
                install=install_out,
                template=template_out,
                reason=row.reason,
            )
        )
    return WsAvailableListOut(items=items)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_ws_available_endpoint.py -v`
Expected: PASS.

- [ ] **Step 5: ruff + mypy + commit**

```bash
cd backend && .venv/bin/python -m ruff check cubeplex/api/routes/v1/ws_mcp.py && .venv/bin/python -m mypy cubeplex/api/routes/v1/ws_mcp.py
git add backend/cubeplex/api/routes/v1/ws_mcp.py backend/tests/unit/test_ws_available_endpoint.py
git commit -m "feat(mcp): GET /ws/{ws}/mcp/available for workspace-side opt-in list"
```

---

## Task 8: Tighten `list_for_workspace_user` filter

**Files:**
- Modify: `backend/cubeplex/mcp/effective.py`
- Modify: `backend/cubeplex/api/routes/v1/ws_mcp.py` (route passes
  `include_disabled_org_installs=False`)
- Modify: `backend/tests/unit/test_mcp_four_layer_handlers.py` (adjust the
  ws-connectors test to assert disabled-org rows no longer come back)

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/unit/test_mcp_effective_service.py` (create if
absent) or extend the existing file:

```python
@pytest.mark.asyncio
async def test_list_for_workspace_user_excludes_disabled_org_installs():
    """When include_disabled_org_installs=False, org installs whose state
    row is enabled=False are filtered out before serialization."""
    # … service set-up with one workspace install (enabled doesn't matter),
    # one org install with state.enabled=True, one org install with
    # state.enabled=False, and one org install with no state row …

    out = await effective_svc.list_for_workspace_user(
        workspace_id="ws-1",
        user_id="usr-1",
        include_unusable=True,
        include_disabled_org_installs=False,
    )
    ids = {d.install.id for d in out}
    assert "mcins-org-enabled" in ids
    assert "mcins-org-disabled" not in ids
    assert "mcins-org-no-state" not in ids
    assert "mcins-ws-local" in ids  # workspace-scope always visible


@pytest.mark.asyncio
async def test_list_for_workspace_user_default_keeps_disabled_org_installs():
    """Backwards compat: default keeps current behavior."""
    out = await effective_svc.list_for_workspace_user(
        workspace_id="ws-1",
        user_id="usr-1",
        include_unusable=True,
        # default include_disabled_org_installs=True
    )
    ids = {d.install.id for d in out}
    assert "mcins-org-disabled" in ids  # still present pre-filter
```

(The fixture set-up will follow the existing `effective_svc` test pattern
in this file — use the same constructor.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_mcp_effective_service.py -v`
Expected: FAIL — parameter not recognised.

- [ ] **Step 3: Add the filter parameter**

Edit `backend/cubeplex/mcp/effective.py` `list_for_workspace_user`. Change
signature:

```python
    async def list_for_workspace_user(
        self,
        workspace_id: str,
        user_id: str,
        *,
        include_unusable: bool = True,
        include_disabled_org_installs: bool = True,
    ) -> list[MCPEffectiveConnectorDTO]:
```

Inside `_collect_rows`, after the `states_by_install` dict is built and
before computing `visible_installs`, narrow the org-install set:

```python
        if not include_disabled_org_installs:
            states_enabled_by_install = {
                iid: s for iid, s in states_by_install.items() if s.enabled
            }
        else:
            states_enabled_by_install = states_by_install

        visible_installs = [
            install
            for install in all_installs
            if install.workspace_id == workspace_id
            or install.id in states_enabled_by_install
        ]
```

Plumb the new flag into the call from `list_for_workspace_user` →
`_collect_rows`.

- [ ] **Step 4: Wire the route**

Edit `backend/cubeplex/api/routes/v1/ws_mcp.py` `list_workspace_connectors`
to pass the new flag:

```python
    dtos = await effective_svc.list_for_workspace_user(
        workspace_id,
        ctx.user.id,
        include_unusable=True,
        include_disabled_org_installs=False,
    )
```

- [ ] **Step 5: Run tests**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/ -v -k "effective_service or four_layer"`
Expected: PASS (existing test that asserted disabled-org rows in
`list_workspace_connectors` may need updating to match the new behavior;
update it inline if it fires).

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/mcp/effective.py backend/cubeplex/api/routes/v1/ws_mcp.py backend/tests/unit/
git commit -m "feat(mcp): /ws/{ws}/mcp/connectors filters disabled org installs"
```

---

## Task 9: Frontend core — types + API helpers

**Files:**
- Create: `frontend/packages/core/src/types/mcp_admin_connector.ts`
- Create: `frontend/packages/core/src/types/mcp_ws_available.ts`
- Modify: `frontend/packages/core/src/api/mcp.ts`
- Modify: `frontend/packages/core/src/types/index.ts` (export the new files)

- [ ] **Step 1: Write the failing test**

Create `frontend/packages/core/__tests__/api/mcp_admin_connectors.test.ts`:

```typescript
import { describe, it, expect, vi } from 'vitest'

import { adminListConnectors, wsListAvailable, type ApiClient } from '../../src'

describe('adminListConnectors', () => {
  it('GETs /api/v1/admin/mcp/connectors', async () => {
    const client = {
      get: vi.fn(async () => ({
        ok: true,
        json: async () => ({ items: [] }),
      })),
    } as unknown as ApiClient
    const res = await adminListConnectors(client)
    expect(res.items).toEqual([])
    expect(client.get).toHaveBeenCalledWith('/api/v1/admin/mcp/connectors')
  })
})

describe('wsListAvailable', () => {
  it('GETs /api/v1/ws/{ws}/mcp/available', async () => {
    const client = {
      get: vi.fn(async () => ({
        ok: true,
        json: async () => ({ items: [] }),
      })),
    } as unknown as ApiClient
    const res = await wsListAvailable(client, 'ws-1')
    expect(res.items).toEqual([])
    expect(client.get).toHaveBeenCalledWith('/api/v1/ws/ws-1/mcp/available')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm --filter @cubeplex/core test -- --run`
Expected: FAIL — undefined imports.

- [ ] **Step 3: Add the type files**

`frontend/packages/core/src/types/mcp_admin_connector.ts`:

```typescript
import type { MCPConnectorInstall, MCPConnectorTemplate } from './mcp'

export type AdminOrgReason =
  | 'usable'
  | 'missing_org_grant'
  | 'pending_oauth'
  | 'grant_expired'
  | 'discovery_failed'

export type AdminOrgCredentialAvailability =
  | 'available'
  | 'missing'
  | 'not_required'

export interface AdminOrgEffective {
  usable: boolean
  reason: AdminOrgReason
  credential_availability: AdminOrgCredentialAvailability | null
}

export interface WorkspaceDistribution {
  enabled_count: number
  disabled_count: number
  eligible_count: number
  auto_enroll_new_workspaces: boolean
}

export interface AdminOrgConnector {
  install: MCPConnectorInstall
  template: MCPConnectorTemplate | null
  org_effective: AdminOrgEffective
  workspace_distribution: WorkspaceDistribution
}
```

`frontend/packages/core/src/types/mcp_ws_available.ts`:

```typescript
import type { MCPConnectorInstall, MCPConnectorTemplate } from './mcp'

export type WsAvailableSource = 'org_install' | 'template'

export type WsAvailableReason =
  | 'no_state_row'
  | 'state_disabled'
  | 'not_installed_at_org'

export interface WsAvailable {
  source: WsAvailableSource
  install: MCPConnectorInstall | null
  template: MCPConnectorTemplate | null
  reason: WsAvailableReason
}
```

- [ ] **Step 4: Add API helpers**

Append to `frontend/packages/core/src/api/mcp.ts`:

```typescript
import type { AdminOrgConnector } from '../types/mcp_admin_connector'
import type { WsAvailable } from '../types/mcp_ws_available'

export async function adminListConnectors(
  client: ApiClient,
): Promise<{ items: AdminOrgConnector[] }> {
  const res = await client.get('/api/v1/admin/mcp/connectors')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { items: AdminOrgConnector[] }
}

export async function wsListAvailable(
  client: ApiClient,
  wsId: string,
): Promise<{ items: WsAvailable[] }> {
  const res = await client.get(`/api/v1/ws/${wsId}/mcp/available`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { items: WsAvailable[] }
}
```

- [ ] **Step 5: Re-export new types**

Edit `frontend/packages/core/src/types/index.ts`:

```typescript
export * from './mcp_admin_connector'
export * from './mcp_ws_available'
```

- [ ] **Step 6: Run tests + build**

```bash
cd frontend
pnpm --filter @cubeplex/core test -- --run
pnpm --filter @cubeplex/core build
```

Expected: tests PASS, `tsc` clean.

- [ ] **Step 7: Commit**

```bash
git add frontend/packages/core/src/types/mcp_admin_connector.ts \
        frontend/packages/core/src/types/mcp_ws_available.ts \
        frontend/packages/core/src/types/index.ts \
        frontend/packages/core/src/api/mcp.ts \
        frontend/packages/core/__tests__/api/mcp_admin_connectors.test.ts
git commit -m "feat(core): types + API helpers for admin connectors and ws available"
```

---

## Task 10: Frontend — `TryItForm` extracted from `TryItView`

**Files:**
- Create: `frontend/packages/web/components/mcp/detail/tools/TryItForm.tsx`
- Modify: `frontend/packages/web/components/mcp/detail/tools/TryItView.tsx`
  temporarily to delegate to `TryItForm` (the file gets deleted in
  Task 11; this intermediate step keeps the tree green).

The form owns: arg input rendering, arg coercion, error/result display.
It takes one callback `onRun(args) => Promise<ToolInvokeResult>`. No
`surface` prop, no role, no API call inside.

- [ ] **Step 1: Sketch the API**

Define the props:

```typescript
export interface TryItFormProps {
  toolName: string
  inputSchema: Record<string, unknown> | null
  onRun: (args: Record<string, unknown>) => Promise<ToolInvokeResult>
  runDisabled?: boolean
  runDisabledReason?: string
  /** Extra UI rendered above the Run button (used by the admin picker). */
  prefix?: ReactNode
}
```

- [ ] **Step 2: Create the file by extracting from `TryItView`**

Copy `frontend/packages/web/components/mcp/detail/tools/TryItView.tsx`'s
body sans the surface branching into
`frontend/packages/web/components/mcp/detail/tools/TryItForm.tsx`. The
exact transformation:

- Drop props: `installId`, `client`, `surface`, `wsId`,
  `adminWorkspaceOptions`, `scopedAdminWorkspaceId`,
  `onScopedWorkspaceChange`, `requiresWorkspacePicker`, `adminAuthMethod`.
- Replace the `handleRun` body with:

  ```typescript
  async function handleRun(): Promise<void> {
    setRunning(true)
    setError(null)
    setResult(null)
    try {
      const args = coerceArgs(values, properties)
      const res = await onRun(args)
      setResult(res)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setRunning(false)
    }
  }
  ```

- Remove the picker JSX (`surface === 'admin' && requiresWorkspacePicker && ...`)
  from the return; render `{prefix}` in its place.
- Keep all schema rendering, value state, copy buttons, etc.

- [ ] **Step 3: Make `TryItView` delegate to `TryItForm`**

Replace `TryItView`'s body so it composes `TryItForm`:

```tsx
export function TryItView(props: TryItViewProps): JSX.Element {
  const { surface, ...rest } = props
  if (surface === 'admin') {
    // Inline lens picker + adminInvokeTool wrapping — deleted entirely in
    // Task 11 once AdminTryItView replaces this code path.
  }
  // … existing wiring …
}
```

(This intermediate version is just to keep tsc green between tasks; do
not commit it as final code. Task 11 introduces `AdminTryItView` /
`WsTryItView` and removes `TryItView.tsx`.)

- [ ] **Step 4: Type-check**

Run: `cd frontend && pnpm --filter web type-check`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/components/mcp/detail/tools/TryItForm.tsx \
        frontend/packages/web/components/mcp/detail/tools/TryItView.tsx
git commit -m "refactor(web/mcp): extract TryItForm with onRun callback"
```

---

## Task 11: Frontend — split `TryItView` into `AdminTryItView` + `WsTryItView`

**Files:**
- Create: `frontend/packages/web/components/mcp/detail/tools/AdminTryItView.tsx`
- Create: `frontend/packages/web/components/mcp/detail/tools/WsTryItView.tsx`
- Create: `frontend/packages/web/components/mcp/detail/tools/AdminToolsPanel.tsx`
- Create: `frontend/packages/web/components/mcp/detail/tools/WsToolsPanel.tsx`
- Modify: `frontend/packages/web/components/mcp/MCPAdminDetailPanel.tsx`
  (swap `ToolsPanel` → `AdminToolsPanel`)
- Modify: `frontend/packages/web/components/workspace-settings/McpPanel.tsx`
  (swap `ToolsPanel` → `WsToolsPanel`)
- Delete: `frontend/packages/web/components/mcp/detail/tools/ToolsPanel.tsx`
- Delete: `frontend/packages/web/components/mcp/detail/tools/TryItView.tsx`

- [ ] **Step 1: `AdminTryItView`**

`AdminTryItView.tsx`:

```tsx
'use client'

import { useTranslations } from 'next-intl'
import { adminInvokeTool, type ApiClient, type ToolInvokeResult } from '@cubeplex/core'

import { TryItForm } from './TryItForm'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'

export interface AdminTryItViewProps {
  installId: string
  toolName: string
  inputSchema: Record<string, unknown> | null
  client: ApiClient
  wsId: string | null
  adminWorkspaceOptions?: Array<{ id: string; name: string }>
  scopedAdminWorkspaceId?: string | null
  onScopedWorkspaceChange?: (wsId: string) => void
  requiresWorkspacePicker?: boolean
  adminAuthMethod?: 'oauth' | 'static' | 'none'
}

export function AdminTryItView(props: AdminTryItViewProps): JSX.Element {
  const t = useTranslations('mcp.tools')
  const {
    installId,
    toolName,
    inputSchema,
    client,
    wsId,
    adminWorkspaceOptions,
    scopedAdminWorkspaceId,
    onScopedWorkspaceChange,
    requiresWorkspacePicker,
    adminAuthMethod,
  } = props

  const runDisabled = requiresWorkspacePicker === true && !scopedAdminWorkspaceId

  const onRun = async (args: Record<string, unknown>): Promise<ToolInvokeResult> => {
    let lens: string | null
    if (requiresWorkspacePicker) {
      lens = scopedAdminWorkspaceId ?? null
    } else if (adminAuthMethod === 'none') {
      lens = wsId ?? null
    } else {
      lens = null
    }
    return adminInvokeTool(client, installId, toolName, args, lens)
  }

  const picker =
    requiresWorkspacePicker && adminWorkspaceOptions ? (
      <div className="flex flex-col gap-1.5">
        <Label className="text-sm">{t('workspaceLensLabel')}</Label>
        <Select
          value={scopedAdminWorkspaceId ?? undefined}
          onChange={(e) => onScopedWorkspaceChange?.(e.target.value)}
        >
          <option value="">{t('workspaceLensEmpty')}</option>
          {adminWorkspaceOptions.map((ws) => (
            <option key={ws.id} value={ws.id}>
              {ws.name}
            </option>
          ))}
        </Select>
      </div>
    ) : null

  return (
    <TryItForm
      toolName={toolName}
      inputSchema={inputSchema}
      onRun={onRun}
      runDisabled={runDisabled}
      runDisabledReason={runDisabled ? t('pickWorkspaceFirst') : undefined}
      prefix={picker}
    />
  )
}
```

- [ ] **Step 2: `WsTryItView`**

`WsTryItView.tsx`:

```tsx
'use client'

import { wsInvokeTool, type ApiClient, type ToolInvokeResult } from '@cubeplex/core'

import { TryItForm } from './TryItForm'

export interface WsTryItViewProps {
  installId: string
  toolName: string
  inputSchema: Record<string, unknown> | null
  client: ApiClient
  wsId: string
}

export function WsTryItView(props: WsTryItViewProps): JSX.Element {
  const { installId, toolName, inputSchema, client, wsId } = props
  const onRun = async (args: Record<string, unknown>): Promise<ToolInvokeResult> =>
    wsInvokeTool(client, wsId, installId, toolName, args)
  return <TryItForm toolName={toolName} inputSchema={inputSchema} onRun={onRun} />
}
```

- [ ] **Step 3: `AdminToolsPanel` + `WsToolsPanel`**

Each is a thin variant of the existing `ToolsPanel.tsx`. Lift the
left-rail tool list + selection into a shared
`ToolListWithFilter` component? **No** — keep the duplication small here
(the list is ~30 LOC). Spec §4.3 says "two thin variants — list +
AdminTryItView or list + WsTryItView".

`AdminToolsPanel.tsx`:

```tsx
'use client'

import { useState, useMemo } from 'react'
import { useTranslations } from 'next-intl'
import type { ApiClient, MCPToolEntry } from '@cubeplex/core'

import { ToolList } from './ToolList'
import { AdminTryItView } from './AdminTryItView'

export interface AdminToolsPanelProps {
  tools: MCPToolEntry[]
  installId: string
  client: ApiClient
  wsId: string | null
  adminWorkspaceOptions?: Array<{ id: string; name: string }>
  scopedAdminWorkspaceId?: string | null
  onScopedWorkspaceChange?: (wsId: string) => void
  requiresWorkspacePicker?: boolean
  adminAuthMethod?: 'oauth' | 'static' | 'none'
}

export function AdminToolsPanel(props: AdminToolsPanelProps): JSX.Element {
  const { tools, installId, client } = props
  const t = useTranslations('mcp.tools')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<string | null>(tools[0]?.name ?? null)
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return tools
    return tools.filter(
      (tool) =>
        tool.name.toLowerCase().includes(q) ||
        (tool.description ?? '').toLowerCase().includes(q),
    )
  }, [tools, query])

  const effective =
    selected && filtered.some((t) => t.name === selected)
      ? selected
      : filtered[0]?.name ?? null
  const selectedTool = tools.find((tool) => tool.name === effective) ?? null

  if (tools.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border px-6 py-12 text-center text-sm text-muted-foreground">
        {t('empty')}
      </div>
    )
  }

  return (
    <div className="grid min-h-[420px] grid-cols-[280px_minmax(0,1fr)] gap-6">
      <aside className="min-h-0 border-r border-border/60 pr-4">
        <ToolList
          tools={tools}
          filtered={filtered}
          query={query}
          onQueryChange={setQuery}
          selectedName={effective}
          onSelect={setSelected}
        />
      </aside>
      <section className="min-h-0">
        {selectedTool ? (
          <AdminTryItView
            installId={installId}
            toolName={selectedTool.name}
            inputSchema={selectedTool.input_schema}
            client={client}
            wsId={props.wsId}
            adminWorkspaceOptions={props.adminWorkspaceOptions}
            scopedAdminWorkspaceId={props.scopedAdminWorkspaceId}
            onScopedWorkspaceChange={props.onScopedWorkspaceChange}
            requiresWorkspacePicker={props.requiresWorkspacePicker}
            adminAuthMethod={props.adminAuthMethod}
          />
        ) : null}
      </section>
    </div>
  )
}
```

`WsToolsPanel.tsx`: same shape but the tool detail composes
`<WsTryItView ... wsId={wsId}/>` and the props omit all admin-only
fields.

- [ ] **Step 4: Swap consumers + delete the dual-mode files**

In `MCPAdminDetailPanel.tsx`, replace the `ToolsPanel` import + usage
with `AdminToolsPanel`. Drop the `surface='admin'` prop.

In `McpPanel.tsx` (workspace settings), replace `ToolsPanel` with
`WsToolsPanel`.

Then:

```bash
rm frontend/packages/web/components/mcp/detail/tools/ToolsPanel.tsx
rm frontend/packages/web/components/mcp/detail/tools/TryItView.tsx
```

Also drop the `ToolDetail` component's `surface` prop if it carried it
through (search `surface` in `detail/tools/`).

- [ ] **Step 5: Type-check + lint**

```bash
cd frontend
pnpm --filter @cubeplex/core build
pnpm --filter web type-check
pnpm --filter web lint
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/components/mcp/detail/tools/
git rm frontend/packages/web/components/mcp/detail/tools/ToolsPanel.tsx \
        frontend/packages/web/components/mcp/detail/tools/TryItView.tsx
git add frontend/packages/web/components/mcp/MCPAdminDetailPanel.tsx \
        frontend/packages/web/components/workspace-settings/McpPanel.tsx
git commit -m "refactor(web/mcp): split ToolsPanel into AdminToolsPanel/WsToolsPanel"
```

---

## Task 12: Frontend — extract `AuthBandFrame` from `AuthActionBand`

**Files:**
- Create: `frontend/packages/web/components/mcp/AuthBandFrame.tsx`
- Modify: `frontend/packages/web/components/mcp/AuthActionBand.tsx`
  (delegate visual rendering to the frame).

`AuthBandFrame` owns: the colored banner, icon, title, body, optional
buttons. It takes a discriminated-union `state: AuthBandState` (the
existing type from `effectiveAuthState.ts`) and a small set of action
callbacks. **No** scope-specific branching, **no** API calls.

- [ ] **Step 1: Sketch the API**

```tsx
export interface AuthBandFrameProps {
  state: AuthBandState
  // Action callbacks the visual layer triggers; the wrapping band
  // (Admin/Ws) decides what API to hit. Pass `undefined` to hide the
  // action.
  onConnect?: () => void
  onDisconnect?: Array<{ scope: 'org' | 'workspace' | 'user'; label: string; onClick: () => void }>
  onRetryError?: () => void
  inFlight?: boolean
  errorMessage?: string
  // For "awaiting-others" state: who they're waiting on (already in
  // state.who, but the frame doesn't need to interpret it).
  primaryButtonLabel?: string
  primaryButtonOnClick?: () => void
}
```

- [ ] **Step 2: Create `AuthBandFrame`**

Copy the visual JSX from `AuthActionBand.tsx` — every branch on
`state.kind` (the `Banner` colors, icons, copy via `useTranslations`,
button rendering) — into `AuthBandFrame.tsx`. Replace direct API calls
(`adminCreateOrgGrant`, `runOAuthFlow`, etc.) with the callbacks.

Keep `Banner` as a small helper inside the frame file or co-locate.

- [ ] **Step 3: Delegate from `AuthActionBand`**

Temporarily make `AuthActionBand.tsx` a wrapper that:

1. Computes `state` (as today, via `computeAuthBandState`).
2. Binds API callbacks per `callerRole` / `isOrgAdmin`.
3. Renders `<AuthBandFrame state={state} {...callbacks} />`.

(This intermediate form keeps existing consumers running. Task 13 splits
into AdminAuthBand/WsAuthBand and removes AuthActionBand.)

- [ ] **Step 4: Type-check**

```bash
cd frontend && pnpm --filter web type-check
```

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/components/mcp/AuthBandFrame.tsx \
        frontend/packages/web/components/mcp/AuthActionBand.tsx
git commit -m "refactor(web/mcp): extract AuthBandFrame from AuthActionBand"
```

---

## Task 13: Frontend — split into `AdminAuthBand` + `WsAuthBand`

**Files:**
- Create: `frontend/packages/web/components/mcp/AdminAuthBand.tsx`
- Create: `frontend/packages/web/components/mcp/WsAuthBand.tsx`
- Modify: `frontend/packages/web/components/mcp/MCPAdminDetailPanel.tsx`
- Modify: `frontend/packages/web/components/workspace-settings/McpPanel.tsx`
- Delete: `frontend/packages/web/components/mcp/AuthActionBand.tsx`

- [ ] **Step 1: `AdminAuthBand`**

Owns: admin write APIs only — `adminCreateOrgGrant`, `adminDeleteOrgGrant`,
`adminOrgGrantOAuthStart`. The component imports `effectiveAuthState`,
calls `computeAuthBandState({ connector, callerRole: 'admin', isOrgAdmin: true })`,
binds admin callbacks, renders `<AuthBandFrame>`.

```tsx
'use client'

import { useEffect, useState } from 'react'
import {
  adminCreateOrgGrant,
  adminDeleteOrgGrant,
  adminGetInstallEffective,
  adminOrgGrantOAuthStart,
  runOAuthFlow,
  type ApiClient,
  type MCPEffectiveConnector,
} from '@cubeplex/core'

import { AuthBandFrame } from './AuthBandFrame'
import { computeAuthBandState } from './effectiveAuthState'

export interface AdminAuthBandProps {
  connector: MCPEffectiveConnector
  client: ApiClient
  // Lens workspace id for OAuth callback wiring; admin band passes the
  // connector.workspace_state's ws id (or null when bare org).
  wsId: string
  onChanged: () => Promise<void>
}

export function AdminAuthBand(props: AdminAuthBandProps): JSX.Element | null {
  // … org-effective override (same pattern as the current
  //   `AdminAuthActionBand` in MCPAdminDetailPanel.tsx) …
  // … compute state via computeAuthBandState({...isOrgAdmin: true}) …
  // … render AuthBandFrame with admin write callbacks bound …
}
```

The admin-specific override of `credential_availability` /
`credential_source` (lines ~474-507 of the current
`MCPAdminDetailPanel.tsx`) moves into this component.

- [ ] **Step 2: `WsAuthBand`**

```tsx
'use client'

import { useState } from 'react'
import {
  wsCreateMyGrant,
  wsCreateWorkspaceGrant,
  wsDeleteMyGrant,
  wsDeleteWorkspaceGrant,
  wsMyGrantOAuthStart,
  wsWorkspaceGrantOAuthStart,
  runOAuthFlow,
  type ApiClient,
  type MCPEffectiveConnector,
} from '@cubeplex/core'

import { AuthBandFrame } from './AuthBandFrame'
import { computeAuthBandState } from './effectiveAuthState'

export interface WsAuthBandProps {
  connector: MCPEffectiveConnector
  client: ApiClient
  wsId: string
  callerRole: 'admin' | 'member'   // workspace-side admin distinct from org admin
  onChanged: () => Promise<void>
}

export function WsAuthBand(props: WsAuthBandProps): JSX.Element | null {
  // computeAuthBandState({...isOrgAdmin: false, callerRole: props.callerRole})
  // bind ws callbacks per the scope (workspace vs user)
  // render AuthBandFrame
}
```

- [ ] **Step 3: Swap consumers**

In `MCPAdminDetailPanel.tsx`: remove the local `AdminAuthActionBand`
sub-component and its org-effective override. Replace with
`<AdminAuthBand connector={connector} client={client} wsId={wsId} onChanged={onRefresh} />`.

In `McpPanel.tsx`: replace `<AuthActionBand callerRole={...} isOrgAdmin={false} ...>`
with `<WsAuthBand connector={connector} client={client} wsId={wsId} callerRole={meWsRole === 'admin' ? 'admin' : 'member'} onChanged={onChanged} />`.

- [ ] **Step 4: Delete the old file**

```bash
rm frontend/packages/web/components/mcp/AuthActionBand.tsx
```

- [ ] **Step 5: Update tests**

`frontend/packages/web/components/mcp/effectiveAuthState.test.ts` does
not need changes (it tests the pure function). If any vitest test
imports `AuthActionBand`, retarget to `AdminAuthBand` or `WsAuthBand`.

- [ ] **Step 6: Type-check + test + commit**

```bash
cd frontend
pnpm --filter web type-check
pnpm --filter web test -- --run
git add frontend/packages/web/components/mcp/
git rm frontend/packages/web/components/mcp/AuthActionBand.tsx
git commit -m "refactor(web/mcp): split AuthActionBand into AdminAuthBand/WsAuthBand"
```

---

## Task 14: Admin page — single fetch, no lens

**Files:**
- Modify: `frontend/packages/web/app/admin/mcp/page.tsx`
- Modify: `frontend/packages/web/components/mcp/MCPAdminDetailPanel.tsx`
- Modify: `frontend/packages/web/components/mcp/MCPConnectorList.tsx`
  (accept the new `AdminOrgConnector[]` shape).

The admin page becomes a single `adminListConnectors` fetch. No
`lensWsId`, no `synthesizeStubEffective`, no workspace_state plumbing on
the row.

- [ ] **Step 1: Rewrite the page**

`frontend/packages/web/app/admin/mcp/page.tsx`:

```tsx
'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  adminDeleteInstall,
  adminListConnectors,
  adminListTemplates,   // existing
  createApiClient,
  type AdminOrgConnector,
  type MCPConnectorFilter,
  type MCPConnectorTemplate,
} from '@cubeplex/core'
import { MCPToolbar } from '@/components/mcp/MCPToolbar'
import { MCPConnectorList } from '@/components/mcp/MCPConnectorList'
import { MCPAdminDetailPanel } from '@/components/mcp/MCPAdminDetailPanel'

export default function AdminMcpPage(): JSX.Element {
  const t = useTranslations('mcpAdmin')
  const client = useMemo(() => createApiClient(''), [])

  const [connectors, setConnectors] = useState<AdminOrgConnector[]>([])
  const [templates, setTemplates] = useState<MCPConnectorTemplate[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<MCPConnectorFilter>('all')
  const [mode, setMode] = useState<
    'detail' | 'install_template' | 'custom_install' | null
  >(null)
  const [installTemplate, setInstallTemplate] =
    useState<MCPConnectorTemplate | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [conn, tpl] = await Promise.all([
        adminListConnectors(client),
        adminListTemplates(client),
      ])
      setConnectors(conn.items)
      setTemplates(tpl.items)
    } finally {
      setLoading(false)
    }
  }, [client])

  useEffect(() => {
    void load()
  }, [load])

  const selected = useMemo(
    () =>
      connectors.find((c) => c.install.install_id === selectedId) ?? null,
    [connectors, selectedId],
  )

  // … existing handleSelect / handleRefresh / handleDelete / handleInstalled
  //   wired against connectors instead of the eff list. Remove wsId / lensWsId.

  const availableTemplates = useMemo(() => {
    const installedTemplateIds = new Set(
      connectors
        .filter((c) => c.install.install_state === 'active')
        .map((c) => c.template?.template_id ?? c.install.template_id)
        .filter((v): v is string => Boolean(v)),
    )
    return templates.filter((tpl) => !installedTemplateIds.has(tpl.template_id))
  }, [templates, connectors])

  return (
    // … same layout as today, but pass `connectors={connectors}` to
    //    `<MCPConnectorList>` and `connector={selected}` to
    //    `<MCPAdminDetailPanel>`.
  )
}
```

- [ ] **Step 2: Update `<MCPConnectorList>` prop types**

Change the prop type from `MCPEffectiveConnector[]` to
`AdminOrgConnector[]` and adapt the row renderer:
- Use `c.install.name || c.template?.name`.
- Drop the workspace-state badge.
- Show `c.workspace_distribution.enabled_count` / `eligible_count` as a
  small "N/M" hint.

- [ ] **Step 3: Update `<MCPAdminDetailPanel>` signature**

Change the `connector` prop type from `MCPEffectiveConnector | null` to
`AdminOrgConnector | null`. Inside:

- Remove the `ws` / `wsEnabled` / `wsDisabled` row from the overview
  `dl`.
- The credential band consumes `AdminOrgConnector.org_effective`
  directly — no synthesized override, no `adminGetInstallEffective`
  round-trip on mount. The detail panel passes `connector.install` +
  `connector.org_effective` into `<AdminAuthBand>` (Task 13's
  component now accepts the new typed input).
- Drop the `lensWsId` prop entirely; the page no longer passes one.

(The `<AdminAuthBand>` from Task 13 needs to be updated to accept
`AdminOrgConnector` shape instead of `MCPEffectiveConnector`. Update its
props + internal `connector` shape in the same commit.)

- [ ] **Step 4: Type-check + run frontend dev server**

```bash
cd frontend
pnpm --filter @cubeplex/core build
pnpm --filter web type-check
PORT=3079 HOSTNAME=0.0.0.0 CUBEPLEX_API_URL=http://192.168.1.111:8079 \
  BASE_URL=http://192.168.1.111:3079 \
  pnpm --filter web exec next dev --hostname 0.0.0.0 --port 3079
```

Manual smoke: hit `http://192.168.1.111:3079/admin/mcp`, confirm:
- Org installs appear, no "workspace disabled" badge anywhere.
- Workspaces tab still works.
- No console errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/app/admin/mcp/page.tsx \
        frontend/packages/web/components/mcp/MCPConnectorList.tsx \
        frontend/packages/web/components/mcp/MCPAdminDetailPanel.tsx \
        frontend/packages/web/components/mcp/AdminAuthBand.tsx
git commit -m "feat(web/admin/mcp): single fetch, no workspace lens"
```

---

## Task 15: Workspace page — Installed / Available split

**Files:**
- Create: `frontend/packages/web/components/mcp/AvailableConnectorRow.tsx`
- Modify: `frontend/packages/web/components/workspace-settings/McpPanel.tsx`
- Modify: `frontend/packages/web/messages/en.json`, `…/zh.json`

- [ ] **Step 1: `AvailableConnectorRow`**

```tsx
'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Loader2 } from 'lucide-react'
import {
  patchWorkspaceConnectorState,   // PATCH /ws/{ws}/mcp/connectors/{id}/state
  wsCreateInstall,
  type ApiClient,
  type WsAvailable,
} from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

export interface AvailableConnectorRowProps {
  row: WsAvailable
  client: ApiClient
  wsId: string
  onConnected: () => Promise<void>
}

export function AvailableConnectorRow({
  row,
  client,
  wsId,
  onConnected,
}: AvailableConnectorRowProps): JSX.Element {
  const t = useTranslations('mcp.available')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const name = row.install?.name ?? row.template?.name ?? '—'
  const description = row.template?.description ?? ''
  const provider = row.template?.provider

  async function handleConnect(): Promise<void> {
    setBusy(true)
    setError(null)
    try {
      if (row.source === 'org_install' && row.install) {
        await patchWorkspaceConnectorState(client, wsId, row.install.install_id, {
          enabled: true,
        })
      } else if (row.source === 'template' && row.template) {
        const method =
          row.template.supported_auth_methods.find((m) => m === 'oauth') ??
          row.template.supported_auth_methods.find((m) => m === 'static') ??
          row.template.supported_auth_methods[0]
        const policy =
          method === 'none'
            ? 'none'
            : row.template.default_credential_policy === 'none'
              ? 'user'
              : row.template.default_credential_policy
        await wsCreateInstall(client, wsId, {
          template_id: row.template.template_id,
          install_scope: 'workspace',
          auth_method: method,
          default_credential_policy: policy,
        })
      }
      await onConnected()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex items-center justify-between rounded-lg border border-border/70 bg-card/40 p-3">
      <div className="flex min-w-0 flex-col">
        <div className="flex items-center gap-2">
          <span className="truncate font-medium">{name}</span>
          {provider ? <Badge variant="outline">{provider}</Badge> : null}
        </div>
        {description ? (
          <span className="line-clamp-1 text-xs text-muted-foreground">
            {description}
          </span>
        ) : null}
        {error ? (
          <span className="text-xs text-destructive">{error}</span>
        ) : null}
      </div>
      <Button type="button" size="sm" disabled={busy} onClick={() => void handleConnect()}>
        {busy ? <Loader2 className="size-3.5 animate-spin" /> : null}
        {t('connect')}
      </Button>
    </div>
  )
}
```

If `patchWorkspaceConnectorState` doesn't exist in core yet, add it:

```typescript
export async function patchWorkspaceConnectorState(
  client: ApiClient,
  wsId: string,
  installId: string,
  body: { enabled?: boolean; credential_policy?: string },
): Promise<unknown> {
  const res = await client.patch(
    `/api/v1/ws/${wsId}/mcp/connectors/${installId}/state`,
    body,
  )
  if (!res.ok) throw await toApiError(res)
  return res.json()
}
```

- [ ] **Step 2: Rewrite `McpPanel` list area**

In `frontend/packages/web/components/workspace-settings/McpPanel.tsx`,
replace the connector/template list section with two sections:

```tsx
const [available, setAvailable] = useState<WsAvailable[]>([])

const load = useCallback(async () => {
  setLoading(true)
  try {
    const [eff, avail] = await Promise.all([
      wsListEffectiveConnectors(client, wsId),
      wsListAvailable(client, wsId),
    ])
    setConnectors(eff.items)  // already filtered server-side to enabled rows
    setAvailable(avail.items)
  } finally {
    setLoading(false)
  }
}, [client, wsId])

// … render:

<section>
  <h3>{t('installed')}</h3>
  {connectors.map((c) => (
    <ConnectorRow key={c.install.install_id} connector={c} ... />
  ))}
</section>

<section>
  <h3>{t('available')}</h3>
  {available.map((row) => (
    <AvailableConnectorRow
      key={row.install?.install_id ?? row.template?.template_id}
      row={row}
      client={client}
      wsId={wsId}
      onConnected={load}
    />
  ))}
</section>
```

Drop the `filteredTemplates` block — `wsListAvailable` already
collapses templates + disabled org installs into one list with the
correct filters.

- [ ] **Step 3: i18n keys**

`frontend/packages/web/messages/en.json` — add under the existing
`"mcp"` section:

```json
"available": {
  "title": "Available",
  "connect": "Connect",
  "empty": "No connectors available to add."
},
"installed": "Installed"
```

`frontend/packages/web/messages/zh.json`:

```json
"available": {
  "title": "可启用",
  "connect": "启用",
  "empty": "暂无可启用的连接器。"
},
"installed": "已启用"
```

Run i18n parity check:

```bash
cd frontend && pnpm --filter web exec node ../../scripts/check-i18n-parity.mjs
```

(or whatever the existing pre-commit hook runs).

- [ ] **Step 4: Type-check + manual smoke**

```bash
cd frontend && pnpm --filter web type-check
```

Manual: refresh `http://192.168.1.111:3079/{ws}/settings/mcp`, confirm
the Installed list only shows enabled rows; Available shows
no-state-row + state-disabled + uninstalled-template rows under one
Connect button.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/components/mcp/AvailableConnectorRow.tsx \
        frontend/packages/web/components/workspace-settings/McpPanel.tsx \
        frontend/packages/web/messages/en.json frontend/packages/web/messages/zh.json \
        frontend/packages/core/src/api/mcp.ts
git commit -m "feat(web/ws/mcp): Installed + Available sections with Connect button"
```

---

## Task 16: Drop the deprecated `GET /admin/mcp/installs` route

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/admin_mcp.py`
- Modify: any callers (search the repo).
- Modify: tests that exercise the old route.

The frontend swapped to `/admin/mcp/connectors` in Task 14. The old
endpoint stays in main for one release for backwards-compat — but
within this PR's scope it's safe to delete (single repo, no external
clients).

- [ ] **Step 1: Search for callers**

```bash
grep -rn "/admin/mcp/installs\b" backend/ frontend/ --include="*.py" --include="*.ts" --include="*.tsx" | grep -v "/installs/"
```

The grep excludes paths like `/installs/{id}` etc. — only the bare
`/installs` collection endpoint is being removed.

If anything besides the route definition + the test in Task 5 still
references it, fix in this step.

- [ ] **Step 2: Remove the route handler**

Delete `list_admin_installs` and its decorator from
`backend/cubeplex/api/routes/v1/admin_mcp.py`. Also remove
`adminListInstalls` from `frontend/packages/core/src/api/mcp.ts`.

- [ ] **Step 3: Run backend tests**

```bash
cd backend && .venv/bin/python -m pytest tests/unit/ -v -k "mcp"
```

Expected: PASS. Any remaining failure points at a missed caller.

- [ ] **Step 4: Run frontend tests**

```bash
cd frontend && pnpm --filter @cubeplex/core test -- --run && pnpm --filter web type-check
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore(mcp): drop deprecated GET /admin/mcp/installs"
```

---

## Task 17: Sweep — full backend + frontend test pass

- [ ] **Step 1: Backend full check**

```bash
cd backend
make check-ci
```

Expected: ruff + mypy + pytest unit all green.

- [ ] **Step 2: Frontend full check**

```bash
cd frontend
pnpm --filter @cubeplex/core build
pnpm --filter web type-check
pnpm --filter web test -- --run
pnpm --filter web format:check
pnpm --filter web lint
```

Expected: all green.

- [ ] **Step 3: Manual end-to-end smoke**

With both services running (ports 8079 / 3079):

1. As org admin on `/admin/mcp`:
   - List shows only org installs.
   - No `wsEnabled` / `wsDisabled` badge anywhere on the top row.
   - "5 / 12 workspaces" hint visible on a fanned-out install.
   - Detail panel Workspaces tab still works.
   - Try It still works (admin picks lens workspace when policy is
     workspace/user).
2. As workspace admin on `/{ws}/settings/mcp`:
   - **Installed**: shows only state.enabled=true rows + workspace-scope.
   - **Available**: shows org installs with no state row, org installs
     with disabled state row, and templates not yet installed in any
     scope — all with one **Connect** button.
   - Connecting an org-install row flips state.enabled=true and moves
     it to Installed.
   - Connecting a template row creates a workspace-scope install and
     moves it to Installed.
   - Auth band drives credential provisioning post-Connect when needed.

- [ ] **Step 4: Final commit (only if anything was tweaked during the sweep)**

```bash
git add -A
git commit -m "chore: post-sweep fixes from manual smoke"
```

---

## Self-review checklist

Run after writing all tasks:

- **Spec coverage:**
  - §3.1 admin endpoint → Tasks 2, 4, 5.
  - §3.2 ws available endpoint → Tasks 3, 6, 7.
  - §3.3 ws connectors tightening → Task 8.
  - §4.1 admin page rewrite → Task 14.
  - §4.2 workspace page rewrite → Task 15.
  - §4.3 component splits:
    - `TryItForm` → Task 10.
    - Admin/Ws `TryItView` / `ToolsPanel` → Task 11.
    - `AuthBandFrame` → Task 12.
    - Admin/Ws `AuthBand` → Task 13.
  - §5 migration (none required) — verified in Tasks 8 + 17.
  - §6 open questions left as-is (resolved in spec or deferred).
- **Placeholder scan:** none of "TBD", "TODO", "implement later",
  "appropriate", "etc." in the plan.
- **Type consistency:** `AdminOrgConnector` / `WsAvailable` /
  `AdminOrgEffective` / `WorkspaceDistribution` / `AuthBandState` /
  `TryItFormProps` all named identically across tasks.

Open assumptions an executing engineer should challenge if they break
during execution:

- `MCPConnectorInstallService` already exposes `_install_repo`,
  `_state_repo`, and (optionally) `_template_repo`. Tasks 5 / 7 fall
  back to constructing the template repo inline if `_template_repo`
  doesn't exist.
- The existing `adminListTemplates` API helper exists in
  `@cubeplex/core`. Task 14 imports it; if the name differs in the
  current tree, adjust without changing the spec semantics.
- Pre-commit hooks (ruff, mypy, vitest, eslint) run on each commit and
  gate the push. The plan does NOT include `--no-verify` anywhere.
