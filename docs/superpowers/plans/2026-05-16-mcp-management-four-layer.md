# MCP Management Four-Layer Direct Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current MCP catalog/server/override model with the final four-layer model: connector templates, connector installs, workspace connector states, and credential grants.

**Architecture:** Use a direct schema and API replacement because the product has not shipped. Create target tables and model classes, remove old catalog/override routes, make `mcp_credential_grants` the single grant table, and make `MCPEffectiveConnectorService` the only source of runtime usability.

**Tech Stack:** FastAPI, SQLModel, Alembic, PostgreSQL, Redis, OAuthTokenManager, Next.js, Zustand, TypeScript, pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-05-15-mcp-management-four-layer-design.md`

**Implementation Policy:**

- No forward compatibility with old MCP product/API names.
- Remove `catalog` and `override` from public API paths, frontend API helpers, UI copy,
  services, repositories, and new tests.
- Use target table names now:
  `mcp_connector_templates`, `mcp_connector_installs`, `mcp_workspace_connector_states`,
  `mcp_credential_grants`.
- Existing local MCP data can be dropped by migration. The product has no released
  external contract.
- Keep the credential vault table; grant rows point to vault credential ids.
- Keep `backend/cubebox/models/mcp.py` as the MCP model module, but replace the model
  classes inside it with the four final MCP-prefixed nouns.

---

## File Structure

**Backend — create:**

- `backend/alembic/versions/f2a6c7d8e901_normalize_mcp_four_layer_tables.py`
- `backend/cubebox/mcp/effective.py`
- `backend/cubebox/services/mcp_templates.py`
- `backend/cubebox/services/mcp_installs.py`
- `backend/tests/unit/mcp/test_effective_state.py`
- `backend/tests/unit/mcp/test_effective_service.py`
- `backend/tests/e2e/test_mcp_four_layer_routes.py`
- `backend/tests/e2e/test_mcp_four_layer_runtime.py`

**Backend — modify:**

- `backend/cubebox/models/mcp.py` — final four model classes.
- `backend/cubebox/repositories/mcp.py` — final four repositories.
- Rename `backend/cubebox/mcp/catalog_seed.py` to
  `backend/cubebox/mcp/template_seed.py`.
- Rename `backend/cubebox/seeders/mcp_catalog_seeder.py` to
  `backend/cubebox/seeders/mcp_template_seeder.py`.
- Rename `backend/cubebox/cli/seed_mcp_catalog.py` to
  `backend/cubebox/cli/seed_mcp_templates.py`.
- `backend/cubebox/mcp/dependencies.py` — new template/install/effective providers.
- `backend/cubebox/api/schemas/mcp.py` — template/install/state/grant schemas.
- `backend/cubebox/api/routes/v1/admin_mcp.py` — admin template/install/grant routes.
- `backend/cubebox/api/routes/v1/ws_mcp.py` — workspace template/install/state/grant routes.
- `backend/cubebox/api/app.py` — stop mounting old catalog router.
- `backend/cubebox/mcp/cubepi_discovery.py` — use effective runtime specs.
- `backend/cubebox/mcp/cubepi_runtime.py` — load only usable effective connectors.
- `backend/cubebox/streams/run_manager.py` — pass OAuth token manager to runtime.

**Backend — delete:**

- `backend/cubebox/api/routes/v1/mcp_catalog.py`
- `backend/cubebox/repositories/mcp_catalog.py`
- `backend/cubebox/services/mcp_catalog.py`

**Frontend — create or rename:**

- Rename `frontend/packages/web/components/mcp/MCPCatalogInstallPanel.tsx` to
  `frontend/packages/web/components/mcp/MCPTemplateInstallPanel.tsx`.

**Frontend — modify:**

- `frontend/packages/core/src/types/mcp.ts`
- `frontend/packages/core/src/api/mcp.ts`
- `frontend/packages/core/src/types/workspace-settings.ts`
- `frontend/packages/core/src/api/workspace-settings.ts`
- `frontend/packages/core/src/stores/workspaceSettingsStore.ts`
- `frontend/packages/core/__tests__/api/mcp.test.ts`
- `frontend/packages/web/components/workspace-settings/McpPanel.tsx`
- `frontend/packages/web/components/mcp/MCPConnectorList.tsx`
- `frontend/packages/web/components/mcp/MCPAdminDetailPanel.tsx`
- `frontend/packages/web/components/mcp/MCPWorkspacesTab.tsx`
- `frontend/packages/web/messages/en.json`
- `frontend/packages/web/messages/zh.json`
- `frontend/packages/web/__tests__/e2e/mcp/ws-mcp.spec.ts`
- `frontend/packages/web/__tests__/e2e/mcp/admin-mcp.spec.ts`

---

## Task 1: Target Prefixed Schema And Model Classes

**Files:**

- Create: `backend/alembic/versions/f2a6c7d8e901_normalize_mcp_four_layer_tables.py`
- Modify: `backend/cubebox/models/mcp.py`
- Modify: `backend/tests/unit/test_mcp_models.py`

- [ ] **Step 1: Write model tests for final prefixed table names**

Append to `backend/tests/unit/test_mcp_models.py`:

```python
def test_mcp_four_layer_table_names() -> None:
    from cubebox.models import (
        MCPConnectorInstall,
        MCPConnectorTemplate,
        MCPCredentialGrant,
        MCPWorkspaceConnectorState,
    )

    assert MCPConnectorTemplate.__tablename__ == "mcp_connector_templates"
    assert MCPConnectorInstall.__tablename__ == "mcp_connector_installs"
    assert MCPWorkspaceConnectorState.__tablename__ == "mcp_workspace_connector_states"
    assert MCPCredentialGrant.__tablename__ == "mcp_credential_grants"


def test_no_auth_install_defaults_to_none_policy() -> None:
    from cubebox.models import MCPConnectorInstall

    row = MCPConnectorInstall(
        org_id="org-1",
        name="NoAuth",
        server_url="https://noauth.example.com/mcp",
        server_url_hash="hash",
        transport="streamable_http",
        auth_method="none",
        default_credential_policy="none",
        created_by_user_id="user-1",
    )

    assert row.auth_method == "none"
    assert row.default_credential_policy == "none"
    assert row.install_state == "active"
    assert row.auth_status == "not_required"
    assert row.discovery_status == "not_run"
```

- [ ] **Step 2: Run the tests and confirm the missing model failure**

Run:

```bash
cd backend
uv run pytest -q tests/unit/test_mcp_models.py::test_mcp_four_layer_table_names \
                 tests/unit/test_mcp_models.py::test_no_auth_install_defaults_to_none_policy
```

Expected: FAIL because the four final model classes are not exported.

- [ ] **Step 3: Replace MCP model classes**

Edit `backend/cubebox/models/mcp.py` so it defines these final classes:

```python
"""MCP connector management models."""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, Column, Index, UniqueConstraint, text
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase


class MCPConnectorTemplate(CubeboxBase, table=True):
    _PREFIX: ClassVar[str] = "ctpl"
    __tablename__ = "mcp_connector_templates"
    __table_args__ = (UniqueConstraint("slug", name="uq_mcp_connector_template_slug"),)

    slug: str = Field(max_length=64, index=True)
    name: str = Field(max_length=128)
    description: str = Field(max_length=2048)
    provider: str = Field(max_length=64)
    server_url: str = Field(max_length=2048)
    transport: str = Field(max_length=16)
    supported_auth_methods: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )
    default_credential_policy: str = Field(default="user", max_length=16)
    oauth_dcr_supported: bool | None = Field(default=None)
    oauth_default_scope: str | None = Field(default=None, max_length=512)
    oauth_static_client_id: str | None = Field(default=None, max_length=256)
    oauth_static_client_secret_credential_id: str | None = Field(
        default=None,
        foreign_key="credentials.id",
        max_length=20,
    )
    static_form_schema: list[dict[str, Any]] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    static_auth_header_template: str | None = Field(default=None, max_length=256)
    template_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default=text("'{}'")),
    )
    tool_citation_defaults: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default=text("'{}'")),
    )
    status: str = Field(default="active", max_length=16)


class MCPConnectorInstall(CubeboxBase, table=True):
    _PREFIX: ClassVar[str] = "cins"
    __tablename__ = "mcp_connector_installs"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "server_url_hash",
            name="uq_mcp_connector_install_url",
        ),
        UniqueConstraint(
            "org_id",
            "workspace_id",
            "name",
            name="uq_mcp_connector_install_name",
        ),
        Index(
            "uq_mcp_connector_install_per_template",
            "org_id",
            text("COALESCE(workspace_id, '_org')"),
            "template_id",
            unique=True,
            postgresql_where=text("template_id IS NOT NULL AND install_state = 'active'"),
        ),
    )

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    template_id: str | None = Field(
        default=None,
        foreign_key="mcp_connector_templates.id",
        max_length=20,
        index=True,
    )
    install_scope: str = Field(default="org", max_length=16)
    workspace_id: str | None = Field(
        default=None,
        foreign_key="workspaces.id",
        max_length=20,
        index=True,
    )
    name: str = Field(max_length=64)
    server_url: str = Field(max_length=2048)
    server_url_hash: str = Field(max_length=64)
    transport: str = Field(max_length=16)
    auth_method: str = Field(max_length=16)
    default_credential_policy: str = Field(max_length=16)
    auth_status: str = Field(default="pending", max_length=16)
    discovery_status: str = Field(default="not_run", max_length=16)
    install_state: str = Field(
        default="active",
        max_length=16,
        sa_column_kwargs={"server_default": text("'active'")},
    )
    oauth_client_config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    headers: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    tools_cache: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    tool_citations: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default=text("'{}'")),
    )
    last_error: str | None = Field(default=None, max_length=2048)
    last_discovered_at: datetime | None = None
    timeout: float = Field(default=30.0)
    sse_read_timeout: float = Field(default=300.0)
    auto_enroll_new_workspaces: bool = Field(
        default=True,
        sa_column_kwargs={"server_default": text("true")},
    )
    created_by_user_id: str = Field(foreign_key="users.id", max_length=20)


class MCPWorkspaceConnectorState(CubeboxBase, table=True):
    _PREFIX: ClassVar[str] = "wcst"
    __tablename__ = "mcp_workspace_connector_states"
    __table_args__ = (
        UniqueConstraint("workspace_id", "install_id", name="uq_mcp_workspace_connector_state"),
    )

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    workspace_id: str = Field(foreign_key="workspaces.id", max_length=20, index=True)
    install_id: str = Field(foreign_key="mcp_connector_installs.id", max_length=20, index=True)
    enabled: bool = Field(default=True)
    credential_policy: str = Field(max_length=16)
    enablement_source: str = Field(default="workspace_manual", max_length=32)
    updated_by_user_id: str = Field(foreign_key="users.id", max_length=20)


class MCPCredentialGrant(CubeboxBase, table=True):
    _PREFIX: ClassVar[str] = "cgrn"
    __tablename__ = "mcp_credential_grants"
    __table_args__ = (
        UniqueConstraint(
            "install_id",
            "grant_scope",
            "workspace_id",
            "user_id",
            name="uq_mcp_credential_grant_scope",
        ),
    )

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    install_id: str = Field(foreign_key="mcp_connector_installs.id", max_length=20, index=True)
    grant_scope: str = Field(max_length=16)
    workspace_id: str | None = Field(
        default=None,
        foreign_key="workspaces.id",
        max_length=20,
        index=True,
    )
    user_id: str | None = Field(
        default=None,
        foreign_key="users.id",
        max_length=20,
        index=True,
    )
    credential_id: str = Field(foreign_key="credentials.id", max_length=20)
    refresh_credential_id: str | None = Field(default=None, foreign_key="credentials.id")
    expires_at: datetime | None = None
    grant_status: str = Field(default="valid", max_length=16)
    created_by_user_id: str = Field(foreign_key="users.id", max_length=20)
```

Update `backend/cubebox/models/__init__.py` to export the four classes and remove old
MCP model exports.

- [ ] **Step 4: Add destructive migration to target tables**

Create `backend/alembic/versions/f2a6c7d8e901_normalize_mcp_four_layer_tables.py`.

Use revision id `f2a6c7d8e901` and set `down_revision` to the current head before this
branch. The migration should:

```python
def upgrade() -> None:
    op.drop_table("user_mcp_credentials")
    op.drop_table("workspace_mcp_credentials")
    op.drop_table("workspace_mcp_overrides")
    op.drop_table("mcp_servers")
    op.drop_table("mcp_catalog_connectors")

    # Create mcp_connector_templates, mcp_connector_installs,
    # mcp_workspace_connector_states, and mcp_credential_grants with the
    # columns defined in backend/cubebox/models/mcp.py.
```

Create the four tables with the columns from `backend/cubebox/models/mcp.py`. Keep these
constraints:

```python
op.create_check_constraint(
    "ck_mcp_connector_installs_scope",
    "mcp_connector_installs",
    "install_scope IN ('org', 'workspace')",
)
op.create_check_constraint(
    "ck_mcp_connector_installs_auth_method",
    "mcp_connector_installs",
    "auth_method IN ('oauth', 'static', 'none')",
)
op.create_check_constraint(
    "ck_mcp_workspace_connector_states_policy",
    "mcp_workspace_connector_states",
    "credential_policy IN ('org', 'workspace', 'user', 'none')",
)
op.create_check_constraint(
    "ck_mcp_credential_grants_scope",
    "mcp_credential_grants",
    "grant_scope IN ('org', 'workspace', 'user')",
)
```

For `downgrade()`, drop the four target tables and recreate the old MCP tables only if
the repository convention requires reversible migrations. If reversible migrations are not
required for destructive internal changes, raise:

```python
raise RuntimeError("Destructive MCP four-layer migration is not reversible")
```

- [ ] **Step 5: Run migration and model tests**

Run:

```bash
cd backend
uv run alembic upgrade head
uv run pytest -q tests/unit/test_mcp_models.py::test_mcp_four_layer_table_names \
                 tests/unit/test_mcp_models.py::test_no_auth_install_defaults_to_none_policy
```

Expected: migration succeeds and tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/models/mcp.py \
        backend/cubebox/models/__init__.py \
        backend/alembic/versions/f2a6c7d8e901_normalize_mcp_four_layer_tables.py \
        backend/tests/unit/test_mcp_models.py
git commit -m "feat(mcp): add four-layer connector schema"
```

---

## Task 2: Repositories And Template Seeding

**Files:**

- Modify: `backend/cubebox/repositories/mcp.py`
- Create: `backend/cubebox/services/mcp_templates.py`
- Rename: `backend/cubebox/mcp/catalog_seed.py` to
  `backend/cubebox/mcp/template_seed.py`
- Rename: `backend/cubebox/seeders/mcp_catalog_seeder.py` to
  `backend/cubebox/seeders/mcp_template_seeder.py`
- Rename: `backend/cubebox/cli/seed_mcp_catalog.py` to
  `backend/cubebox/cli/seed_mcp_templates.py`
- Modify: `backend/tests/unit/test_catalog_seed.py`
- Modify: `backend/tests/unit/test_mcp_repositories.py`

- [ ] **Step 1: Write repository tests for final nouns**

Append to `backend/tests/unit/test_mcp_repositories.py`:

```python
async def test_connector_template_repository_upserts_by_slug(session: AsyncSession) -> None:
    from cubebox.repositories.mcp import MCPConnectorTemplateRepository

    repo = MCPConnectorTemplateRepository(session)
    row = await repo.upsert_by_slug(
        slug="github",
        name="GitHub",
        description="GitHub MCP server.",
        provider="GitHub",
        server_url="https://github.example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["oauth", "static"],
        default_credential_policy="user",
    )

    assert row.slug == "github"
    assert row.default_credential_policy == "user"


async def test_credential_grant_repository_scopes_user_grants(
    session: AsyncSession,
) -> None:
    from cubebox.models import MCPCredentialGrant
    from cubebox.repositories.mcp import MCPCredentialGrantRepository

    repo = MCPCredentialGrantRepository(session, org_id="org-1")
    await repo.add(
        MCPCredentialGrant(
            org_id="org-1",
            install_id="cins-1",
            grant_scope="user",
            user_id="user-1",
            credential_id="cred-1",
            created_by_user_id="user-1",
        )
    )

    assert await repo.get_user_grant(install_id="cins-1", user_id="user-1") is not None
    assert await repo.get_user_grant(install_id="cins-1", user_id="user-2") is None
```

- [ ] **Step 2: Run tests and confirm missing repositories**

Run:

```bash
cd backend
uv run pytest -q tests/unit/test_mcp_repositories.py::test_connector_template_repository_upserts_by_slug \
                 tests/unit/test_mcp_repositories.py::test_credential_grant_repository_scopes_user_grants
```

Expected: FAIL because the final repositories do not exist.

- [ ] **Step 3: Replace repository classes**

Edit `backend/cubebox/repositories/mcp.py` so it exports these concrete methods:

- `MCPConnectorTemplateRepository.get(template_id: str) -> MCPConnectorTemplate | None`
- `MCPConnectorTemplateRepository.get_by_slug(slug: str) -> MCPConnectorTemplate | None`
- `MCPConnectorTemplateRepository.list_active() -> list[MCPConnectorTemplate]`
- `MCPConnectorTemplateRepository.upsert_by_slug(slug: str, name: str, description: str,
  provider: str, server_url: str, transport: str, supported_auth_methods: list[str],
  default_credential_policy: str) -> MCPConnectorTemplate`
- `MCPConnectorInstallRepository.get(install_id: str) -> MCPConnectorInstall | None`
- `MCPConnectorInstallRepository.list_org_installs() -> list[MCPConnectorInstall]`
- `MCPConnectorInstallRepository.list_workspace_installs(workspace_id: str)
  -> list[MCPConnectorInstall]`
- `MCPConnectorInstallRepository.add(install: MCPConnectorInstall) -> MCPConnectorInstall`
- `MCPConnectorInstallRepository.update(install: MCPConnectorInstall) -> MCPConnectorInstall`
- `MCPWorkspaceConnectorStateRepository.get(workspace_id: str, install_id: str)
  -> MCPWorkspaceConnectorState | None`
- `MCPWorkspaceConnectorStateRepository.list_for_workspace(workspace_id: str)
  -> list[MCPWorkspaceConnectorState]`
- `MCPWorkspaceConnectorStateRepository.upsert(workspace_id: str, install_id: str,
  enabled: bool, credential_policy: str, enablement_source: str,
  updated_by_user_id: str) -> MCPWorkspaceConnectorState`
- `MCPCredentialGrantRepository.add(grant: MCPCredentialGrant) -> MCPCredentialGrant`
- `MCPCredentialGrantRepository.get_org_grant(install_id: str) -> MCPCredentialGrant | None`
- `MCPCredentialGrantRepository.get_workspace_grant(install_id: str, workspace_id: str)
  -> MCPCredentialGrant | None`
- `MCPCredentialGrantRepository.get_user_grant(install_id: str, user_id: str)
  -> MCPCredentialGrant | None`
- `MCPCredentialGrantRepository.delete_scope(install_id: str, grant_scope: str,
  workspace_id: str | None, user_id: str | None) -> None`

Use the existing org-scoped repository pattern: every non-template repository receives
`org_id` in `__init__` and filters by it in every query.

- [ ] **Step 4: Create template service**

Create `backend/cubebox/services/mcp_templates.py`:

```python
"""Connector template service."""

from cubebox.models import MCPConnectorTemplate
from cubebox.repositories.mcp import MCPConnectorTemplateRepository


class MCPConnectorTemplateService:
    def __init__(self, repo: MCPConnectorTemplateRepository) -> None:
        self._repo = repo

    async def list_active(self) -> list[MCPConnectorTemplate]:
        return await self._repo.list_active()

    async def get_active(self, template_id: str) -> MCPConnectorTemplate:
        row = await self._repo.get(template_id)
        if row is None or row.status != "active":
            raise ValueError("connector_template_not_found")
        return row
```

- [ ] **Step 5: Rename seed concepts to templates**

Run:

```bash
git mv backend/cubebox/mcp/catalog_seed.py backend/cubebox/mcp/template_seed.py
git mv backend/cubebox/seeders/mcp_catalog_seeder.py \
       backend/cubebox/seeders/mcp_template_seeder.py
git mv backend/cubebox/cli/seed_mcp_catalog.py backend/cubebox/cli/seed_mcp_templates.py
```

In `backend/cubebox/mcp/template_seed.py`, rename `CatalogSeedEntry` to
`MCPConnectorTemplateSeedEntry`. Rename fields:

```python
default_credential_scope -> default_credential_policy
static_form_fields -> static_form_schema
cred_metadata -> template_metadata
tool_citations -> tool_citation_defaults
```

Update `backend/cubebox/seeders/mcp_template_seeder.py` and
`backend/cubebox/cli/seed_mcp_templates.py` to call `MCPConnectorTemplateRepository`.

- [ ] **Step 6: Run repository and seed tests**

Run:

```bash
cd backend
uv run pytest -q tests/unit/test_mcp_repositories.py tests/unit/test_catalog_seed.py
```

Expected: all tests pass.

- [ ] **Step 7: Delete old repository/service files**

Run:

```bash
git rm backend/cubebox/repositories/mcp_catalog.py backend/cubebox/services/mcp_catalog.py
```

- [ ] **Step 8: Commit**

```bash
git add backend/cubebox/repositories/mcp.py \
        backend/cubebox/services/mcp_templates.py \
        backend/cubebox/mcp/template_seed.py \
        backend/cubebox/seeders/mcp_template_seeder.py \
        backend/cubebox/cli/seed_mcp_templates.py \
        backend/tests/unit/test_catalog_seed.py \
        backend/tests/unit/test_mcp_repositories.py
git commit -m "feat(mcp): replace catalog repositories with connector templates"
```

---

## Task 3: Install, Workspace State, And Grant Services

**Files:**

- Create: `backend/cubebox/services/mcp_installs.py`
- Modify: `backend/cubebox/mcp/dependencies.py`
- Modify: `backend/tests/unit/test_mcp_service_invariants.py`
- Create: `backend/tests/e2e/test_mcp_four_layer_routes.py`

- [ ] **Step 1: Add service invariant tests**

Append to `backend/tests/unit/test_mcp_service_invariants.py`:

```python
def test_auth_method_none_resolves_not_required_defaults() -> None:
    from cubebox.services.mcp_installs import install_defaults_for_auth_method

    defaults = install_defaults_for_auth_method("none", "user")

    assert defaults.auth_status == "not_required"
    assert defaults.credential_policy == "none"


def test_static_auth_uses_requested_policy() -> None:
    from cubebox.services.mcp_installs import install_defaults_for_auth_method

    defaults = install_defaults_for_auth_method("static", "workspace")

    assert defaults.auth_status == "pending"
    assert defaults.credential_policy == "workspace"
```

- [ ] **Step 2: Run invariant tests and confirm missing service**

Run:

```bash
cd backend
uv run pytest -q tests/unit/test_mcp_service_invariants.py::test_auth_method_none_resolves_not_required_defaults \
                 tests/unit/test_mcp_service_invariants.py::test_static_auth_uses_requested_policy
```

Expected: FAIL because `cubebox.services.mcp_installs` does not exist.

- [ ] **Step 3: Create install service primitives**

Create `backend/cubebox/services/mcp_installs.py`:

```python
"""Connector install, workspace state, and grant service."""

from dataclasses import dataclass
from datetime import UTC, datetime

from cubebox.mcp._constants import CREDENTIAL_KIND_MCP
from cubebox.mcp._constants import server_url_hash
from cubebox.models import (
    MCPConnectorInstall,
    MCPConnectorTemplate,
    MCPCredentialGrant,
    MCPWorkspaceConnectorState,
)
from cubebox.repositories.mcp import (
    MCPConnectorInstallRepository,
    MCPCredentialGrantRepository,
    MCPWorkspaceConnectorStateRepository,
)
from cubebox.services.credential import CredentialService


@dataclass(frozen=True, slots=True)
class MCPInstallDefaults:
    auth_status: str
    credential_policy: str


def install_defaults_for_auth_method(
    auth_method: str,
    requested_policy: str,
) -> MCPInstallDefaults:
    if auth_method == "none":
        return MCPInstallDefaults(auth_status="not_required", credential_policy="none")
    return MCPInstallDefaults(auth_status="pending", credential_policy=requested_policy)


class MCPConnectorInstallService:
    def __init__(
        self,
        *,
        install_repo: MCPConnectorInstallRepository,
        state_repo: MCPWorkspaceConnectorStateRepository,
        grant_repo: MCPCredentialGrantRepository,
        cred_service: CredentialService,
        org_id: str,
        actor_user_id: str,
    ) -> None:
        self._install_repo = install_repo
        self._state_repo = state_repo
        self._grant_repo = grant_repo
        self._cred_service = cred_service
        self._org_id = org_id
        self._actor_user_id = actor_user_id

    async def create_from_template_for_workspace(
        self,
        *,
        template: MCPConnectorTemplate,
        workspace_id: str,
        auth_method: str,
        credential_policy: str,
    ) -> MCPConnectorInstall:
        defaults = install_defaults_for_auth_method(auth_method, credential_policy)
        install = await self._install_repo.add(
            MCPConnectorInstall(
                org_id=self._org_id,
                template_id=template.id,
                install_scope="workspace",
                workspace_id=workspace_id,
                name=template.name,
                server_url=template.server_url,
                server_url_hash=server_url_hash(template.server_url),
                transport=template.transport,
                auth_method=auth_method,
                default_credential_policy=defaults.credential_policy,
                auth_status=defaults.auth_status,
                discovery_status="not_run",
                tool_citations=dict(template.tool_citation_defaults or {}),
                created_by_user_id=self._actor_user_id,
            )
        )
        await self._state_repo.upsert(
            workspace_id=workspace_id,
            install_id=install.id,
            enabled=True,
            credential_policy=defaults.credential_policy,
            enablement_source="workspace_manual",
            updated_by_user_id=self._actor_user_id,
        )
        return install

    async def create_static_grant(
        self,
        *,
        install_id: str,
        grant_scope: str,
        plaintext: str,
        workspace_id: str | None = None,
        user_id: str | None = None,
        name: str | None = None,
    ) -> MCPCredentialGrant:
        credential_id = await self._cred_service.create(
            kind=CREDENTIAL_KIND_MCP,
            name=name or f"mcp:{install_id}:{grant_scope}",
            plaintext=plaintext,
        )
        return await self._grant_repo.add(
            MCPCredentialGrant(
                org_id=self._org_id,
                install_id=install_id,
                grant_scope=grant_scope,
                workspace_id=workspace_id,
                user_id=user_id,
                credential_id=credential_id,
                grant_status="valid",
                created_by_user_id=self._actor_user_id,
            )
        )

    async def uninstall(self, install_id: str) -> MCPConnectorInstall:
        install = await self._install_repo.get(install_id)
        if install is None:
            raise ValueError("connector_install_not_found")
        install.install_state = "uninstalled"
        install.auth_status = "disconnected"
        install.updated_at = datetime.now(UTC)
        return await self._install_repo.update(install)
```

- [ ] **Step 4: Add dependency providers**

Edit `backend/cubebox/mcp/dependencies.py` and add:

```python
async def get_connector_template_service(
    session: AsyncSession = Depends(get_session),
) -> MCPConnectorTemplateService:
    return MCPConnectorTemplateService(MCPConnectorTemplateRepository(session))


async def get_connector_install_service(
    session: AsyncSession = Depends(get_session),
    cred_service: CredentialService = Depends(get_credential_service),
    ctx: RequestContext = Depends(require_member),
) -> MCPConnectorInstallService:
    return MCPConnectorInstallService(
        install_repo=MCPConnectorInstallRepository(session, org_id=ctx.org_id),
        state_repo=MCPWorkspaceConnectorStateRepository(session, org_id=ctx.org_id),
        grant_repo=MCPCredentialGrantRepository(session, org_id=ctx.org_id),
        cred_service=cred_service,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
    )
```

- [ ] **Step 5: Run service tests**

Run:

```bash
cd backend
uv run pytest -q tests/unit/test_mcp_service_invariants.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/services/mcp_installs.py \
        backend/cubebox/mcp/dependencies.py \
        backend/tests/unit/test_mcp_service_invariants.py
git commit -m "feat(mcp): add install state and grant services"
```

---

## Task 4: Final API Routes Only

**Files:**

- Modify: `backend/cubebox/api/schemas/mcp.py`
- Modify: `backend/cubebox/api/routes/v1/admin_mcp.py`
- Modify: `backend/cubebox/api/routes/v1/ws_mcp.py`
- Modify: `backend/cubebox/api/app.py`
- Delete: `backend/cubebox/api/routes/v1/mcp_catalog.py`
- Modify: `backend/tests/unit/test_admin_mcp_routes.py`
- Modify: `backend/tests/unit/test_ws_mcp_routes.py`
- Modify: `backend/tests/e2e/test_mcp_four_layer_routes.py`

- [ ] **Step 1: Add route registration tests**

Replace the MCP route assertions in `backend/tests/unit/test_ws_mcp_routes.py` with:

```python
def test_workspace_mcp_four_layer_routes_are_registered() -> None:
    from cubebox.api.app import create_app

    app = create_app()
    paths = {route.path for route in app.routes}

    assert "/api/v1/ws/{workspace_id}/mcp/templates" in paths
    assert "/api/v1/ws/{workspace_id}/mcp/installs" in paths
    assert "/api/v1/ws/{workspace_id}/mcp/installs/{install_id}/state" in paths
    assert "/api/v1/ws/{workspace_id}/mcp/installs/{install_id}/grants/me" in paths
    assert "/api/v1/ws/{workspace_id}/mcp/installs/{install_id}/grants/workspace" in paths
    assert "/api/v1/ws/{workspace_id}/mcp/connectors" in paths
    assert "/api/v1/ws/{workspace_id}/mcp/catalog" not in paths
    assert "/api/v1/ws/{workspace_id}/mcp/org-installs/{install_id}/override" not in paths
```

Replace the MCP route assertions in `backend/tests/unit/test_admin_mcp_routes.py` with:

```python
def test_admin_mcp_four_layer_routes_are_registered() -> None:
    from cubebox.api.app import create_app

    app = create_app()
    paths = {route.path for route in app.routes}

    assert "/api/v1/admin/mcp/templates" in paths
    assert "/api/v1/admin/mcp/templates/{template_id}/installs" in paths
    assert "/api/v1/admin/mcp/installs" in paths
    assert "/api/v1/admin/mcp/installs/{install_id}" in paths
    assert "/api/v1/admin/mcp/installs/{install_id}/grants/org" in paths
    assert "/api/v1/admin/mcp/catalog/{catalog_id}/install" not in paths
    assert "/api/v1/admin/mcp/servers/{server_id}/overrides" not in paths
```

- [ ] **Step 2: Run route tests and confirm old route names still exist**

Run:

```bash
cd backend
uv run pytest -q tests/unit/test_ws_mcp_routes.py tests/unit/test_admin_mcp_routes.py
```

Expected: FAIL because current routes still expose old MCP paths.

- [ ] **Step 3: Replace MCP schemas**

Edit `backend/cubebox/api/schemas/mcp.py` so public schemas use final names.
Use `Literal["org", "workspace", "user", "none"]` for credential policy fields and
`Literal["oauth", "static", "none"]` for auth method fields.

The response schemas must include these fields:

- `MCPConnectorTemplateOut`: `template_id`, `slug`, `name`, `provider`, `description`,
  `server_url`, `transport`, `supported_auth_methods`, `default_credential_policy`,
  `static_form_schema`, `status`.
- `MCPConnectorInstallOut`: `install_id`, `template_id`, `install_scope`, `workspace_id`,
  `name`, `server_url`, `transport`, `auth_method`, `default_credential_policy`,
  `auth_status`, `discovery_status`, `install_state`, `tool_count`, `last_error`.
- `MCPWorkspaceConnectorStateOut`: `workspace_id`, `install_id`, `enabled`,
  `credential_policy`, `enablement_source`.
- `MCPCredentialGrantStatusOut`: `install_id`, `grant_scope`, `workspace_id`, `user_id`,
  `grant_status`, `has_value`, `expires_at`.
- `MCPEffectiveConnectorOut`: `template`, `install`, `workspace_state`, `credential_policy`,
  `credential_availability`, `credential_source`, `usable`, `reason`.

- [ ] **Step 4: Replace admin routes**

Edit `backend/cubebox/api/routes/v1/admin_mcp.py` to expose these routes:

- `GET /api/v1/admin/mcp/templates`
- `POST /api/v1/admin/mcp/templates/{template_id}/installs`
- `GET /api/v1/admin/mcp/installs`
- `DELETE /api/v1/admin/mcp/installs/{install_id}`
- `PUT /api/v1/admin/mcp/installs/{install_id}/grants/org`

Remove server/override route handlers from this module.

- [ ] **Step 5: Replace workspace routes**

Edit `backend/cubebox/api/routes/v1/ws_mcp.py` to expose these routes:

- `GET /api/v1/ws/{workspace_id}/mcp/templates`
- `POST /api/v1/ws/{workspace_id}/mcp/installs`
- `PATCH /api/v1/ws/{workspace_id}/mcp/installs/{install_id}/state`
- `PUT /api/v1/ws/{workspace_id}/mcp/installs/{install_id}/grants/me`
- `PUT /api/v1/ws/{workspace_id}/mcp/installs/{install_id}/grants/workspace`
- `GET /api/v1/ws/{workspace_id}/mcp/connectors`

Remove old server/catalog/org-install override handlers from this module.

- [ ] **Step 6: Remove old route module from app**

Edit `backend/cubebox/api/app.py` and remove the import/mount for
`cubebox.api.routes.v1.mcp_catalog`.

Run:

```bash
git rm backend/cubebox/api/routes/v1/mcp_catalog.py
```

- [ ] **Step 7: Run route tests**

Run:

```bash
cd backend
uv run pytest -q tests/unit/test_ws_mcp_routes.py tests/unit/test_admin_mcp_routes.py
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add backend/cubebox/api/schemas/mcp.py \
        backend/cubebox/api/routes/v1/admin_mcp.py \
        backend/cubebox/api/routes/v1/ws_mcp.py \
        backend/cubebox/api/app.py \
        backend/tests/unit/test_admin_mcp_routes.py \
        backend/tests/unit/test_ws_mcp_routes.py
git commit -m "feat(mcp): replace catalog routes with four-layer API"
```

---

## Task 5: Effective State Service And Runtime

**Files:**

- Create: `backend/cubebox/mcp/effective.py`
- Modify: `backend/cubebox/mcp/cubepi_discovery.py`
- Modify: `backend/cubebox/mcp/cubepi_runtime.py`
- Modify: `backend/cubebox/streams/run_manager.py`
- Create: `backend/tests/unit/mcp/test_effective_state.py`
- Create: `backend/tests/unit/mcp/test_effective_service.py`
- Modify: `backend/tests/unit/test_mcp_cubepi_runtime.py`

- [ ] **Step 1: Add effective state tests**

Create `backend/tests/unit/mcp/test_effective_state.py`:

```python
from cubebox.mcp.effective import MCPEffectiveInput, MCPGrantInput, compute_effective_state


def test_no_auth_connector_is_usable_without_grant() -> None:
    result = compute_effective_state(
        MCPEffectiveInput(
            template_status="active",
            install_state="active",
            workspace_enabled=True,
            auth_method="none",
            credential_policy="none",
            grant=None,
            transport="streamable_http",
        )
    )

    assert result.usable is True
    assert result.reason == "usable"
    assert result.credential_availability == "not_required"


def test_user_policy_requires_user_grant() -> None:
    result = compute_effective_state(
        MCPEffectiveInput(
            template_status="active",
            install_state="active",
            workspace_enabled=True,
            auth_method="static",
            credential_policy="user",
            grant=None,
            transport="streamable_http",
        )
    )

    assert result.usable is False
    assert result.reason == "credential_missing"


def test_valid_user_grant_makes_user_policy_usable() -> None:
    result = compute_effective_state(
        MCPEffectiveInput(
            template_status="active",
            install_state="active",
            workspace_enabled=True,
            auth_method="static",
            credential_policy="user",
            grant=MCPGrantInput(scope="user", status="valid"),
            transport="streamable_http",
        )
    )

    assert result.usable is True
    assert result.reason == "usable"
```

- [ ] **Step 2: Implement pure effective-state model**

Create `backend/cubebox/mcp/effective.py`:

```python
from dataclasses import dataclass
from typing import Literal

CredentialPolicy = Literal["org", "workspace", "user", "none"]
MCPEffectiveReason = Literal[
    "usable",
    "template_inactive",
    "install_uninstalled",
    "workspace_disabled",
    "credential_missing",
    "credential_invalid",
    "unsupported_transport",
]


@dataclass(frozen=True, slots=True)
class MCPGrantInput:
    scope: str
    status: str


@dataclass(frozen=True, slots=True)
class MCPEffectiveInput:
    template_status: str | None
    install_state: str
    workspace_enabled: bool
    auth_method: str
    credential_policy: CredentialPolicy
    grant: MCPGrantInput | None
    transport: str


@dataclass(frozen=True, slots=True)
class MCPEffectiveResult:
    usable: bool
    reason: MCPEffectiveReason
    credential_availability: str


def compute_effective_state(value: MCPEffectiveInput) -> MCPEffectiveResult:
    if value.template_status is not None and value.template_status != "active":
        return MCPEffectiveResult(False, "template_inactive", "missing")
    if value.install_state != "active":
        return MCPEffectiveResult(False, "install_uninstalled", "missing")
    if value.transport not in {"streamable_http", "sse"}:
        return MCPEffectiveResult(False, "unsupported_transport", "missing")
    if not value.workspace_enabled:
        return MCPEffectiveResult(False, "workspace_disabled", "missing")
    if value.auth_method == "none" or value.credential_policy == "none":
        return MCPEffectiveResult(True, "usable", "not_required")
    if value.grant is None:
        return MCPEffectiveResult(False, "credential_missing", "missing")
    if value.grant.status != "valid" or value.grant.scope != value.credential_policy:
        return MCPEffectiveResult(False, "credential_invalid", "missing")
    return MCPEffectiveResult(True, "usable", "available")
```

- [ ] **Step 3: Add DB-backed effective service**

Extend `backend/cubebox/mcp/effective.py` with `MCPEffectiveConnectorService`.

It should:

- load active org installs plus workspace installs;
- load `MCPWorkspaceConnectorState` for each install and workspace;
- load the required grant from `MCPCredentialGrantRepository`;
- return unusable rows when `include_unusable=True`;
- return only usable rows to runtime.

The service exposes two methods:

- `list_for_workspace_user(workspace_id: str, user_id: str, include_unusable: bool)
  -> list[MCPEffectiveConnectorDTO]`
- `list_runtime_specs(workspace_id: str, user_id: str) -> list[MCPRuntimeConnectorSpec]`

- [ ] **Step 4: Wire OAuth refresh in runtime token resolution**

Edit `backend/cubebox/mcp/cubepi_runtime.py` so `load_workspace_mcp_tools_for_cubepi`
accepts:

```python
effective_service: MCPEffectiveConnectorService
token_manager: OAuthTokenManager | None
```

For OAuth grants, call:

```python
token = await token_manager.get_access_token(
    install_id=spec.install_id,
    grant_scope=spec.grant_scope,
    workspace_id=workspace_id,
    user_id=user_id,
)
```

For static grants, decrypt `spec.credential_id` through `CredentialService`.
For no-auth connectors, sign the short-lived Cubebox identity token.

- [ ] **Step 5: Wire run manager**

Edit `backend/cubebox/streams/run_manager.py` to construct `MCPEffectiveConnectorService`
inside the MCP tools block and pass it to `load_workspace_mcp_tools_for_cubepi`.
Construct `OAuthTokenManager` using the same `_build_token_manager_for_org` helper used by
admin MCP dependencies.

- [ ] **Step 6: Run effective and runtime tests**

Run:

```bash
cd backend
uv run pytest -q tests/unit/mcp/test_effective_state.py \
                 tests/unit/mcp/test_effective_service.py \
                 tests/unit/test_mcp_cubepi_runtime.py
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/cubebox/mcp/effective.py \
        backend/cubebox/mcp/cubepi_discovery.py \
        backend/cubebox/mcp/cubepi_runtime.py \
        backend/cubebox/streams/run_manager.py \
        backend/tests/unit/mcp/test_effective_state.py \
        backend/tests/unit/mcp/test_effective_service.py \
        backend/tests/unit/test_mcp_cubepi_runtime.py
git commit -m "feat(mcp): derive runtime connectors from effective state"
```

---

## Task 6: Backend E2E Coverage

**Files:**

- Create: `backend/tests/e2e/test_mcp_four_layer_routes.py`
- Create: `backend/tests/e2e/test_mcp_four_layer_runtime.py`
- Modify: `backend/tests/e2e/conftest.py`

- [ ] **Step 1: Add template fixtures**

Append to `backend/tests/e2e/conftest.py`:

```python
@pytest_asyncio.fixture
async def noauth_template_id(db_session: AsyncSession) -> AsyncIterator[str]:
    from cubebox.repositories.mcp import MCPConnectorTemplateRepository

    row = await MCPConnectorTemplateRepository(db_session).upsert_by_slug(
        slug="noauth-e2e",
        name="NoAuth E2E",
        description="No auth connector.",
        provider="Cubebox",
        server_url="https://noauth-e2e.example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["none"],
        default_credential_policy="none",
    )
    await db_session.commit()
    yield row.id
```

- [ ] **Step 2: Add no-auth route E2E**

Create `backend/tests/e2e/test_mcp_four_layer_routes.py`:

```python
import httpx
import pytest


@pytest.mark.usefixtures("stub_discover_tools")
async def test_workspace_installs_noauth_template_and_gets_usable_connector(
    client: httpx.AsyncClient,
    workspace_id: str,
    noauth_template_id: str,
) -> None:
    install = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/installs",
        json={"template_id": noauth_template_id, "auth_method": "none"},
    )
    assert install.status_code == 201, install.text

    connectors = await client.get(f"/api/v1/ws/{workspace_id}/mcp/connectors")
    assert connectors.status_code == 200, connectors.text
    row = next(
        item
        for item in connectors.json()["items"]
        if item["install"]["install_id"] == install.json()["install_id"]
    )
    assert row["workspace_state"]["enabled"] is True
    assert row["credential_policy"] == "none"
    assert row["credential_availability"] == "not_required"
    assert row["usable"] is True
```

- [ ] **Step 3: Add user grant isolation E2E**

Append to `backend/tests/e2e/test_mcp_four_layer_routes.py`:

```python
@pytest.mark.usefixtures("stub_discover_tools")
async def test_user_grant_policy_does_not_fall_back_to_org_grant(
    admin_client: tuple[httpx.AsyncClient, str],
    github_template_id: str,
) -> None:
    client, workspace_id = admin_client
    install = await client.post(
        f"/api/v1/admin/mcp/templates/{github_template_id}/installs",
        json={
            "auth_method": "static",
            "credential_policy": "org",
            "credential_plaintext": "org-token",
        },
    )
    assert install.status_code == 201, install.text
    install_id = install.json()["install_id"]

    state = await client.patch(
        f"/api/v1/ws/{workspace_id}/mcp/installs/{install_id}/state",
        json={"enabled": True, "credential_policy": "user"},
    )
    assert state.status_code == 200, state.text

    connectors = await client.get(f"/api/v1/ws/{workspace_id}/mcp/connectors")
    row = next(
        item
        for item in connectors.json()["items"]
        if item["install"]["install_id"] == install_id
    )
    assert row["credential_policy"] == "user"
    assert row["credential_availability"] == "missing"
    assert row["usable"] is False
```

- [ ] **Step 4: Run backend E2E tests**

Run:

```bash
cd backend
uv run pytest -q tests/e2e/test_mcp_four_layer_routes.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/e2e/conftest.py \
        backend/tests/e2e/test_mcp_four_layer_routes.py \
        backend/tests/e2e/test_mcp_four_layer_runtime.py
git commit -m "test(mcp): cover four-layer connector flows"
```

---

## Task 7: Frontend Core API And Types

**Files:**

- Modify: `frontend/packages/core/src/types/mcp.ts`
- Modify: `frontend/packages/core/src/api/mcp.ts`
- Modify: `frontend/packages/core/src/types/workspace-settings.ts`
- Modify: `frontend/packages/core/src/api/workspace-settings.ts`
- Modify: `frontend/packages/core/src/stores/workspaceSettingsStore.ts`
- Modify: `frontend/packages/core/__tests__/api/mcp.test.ts`

- [ ] **Step 1: Replace core type names**

Edit `frontend/packages/core/src/types/mcp.ts`.

Remove `MCPCatalogConnector`, `MCPCatalogListResponse`, `MCPOrgInstallOverrideRequest`,
and old server override types. Add:

```typescript
export interface MCPConnectorTemplate {
  template_id: string
  slug: string
  name: string
  provider: string
  description: string
  server_url: string
  transport: MCPTransport
  supported_auth_methods: MCPAuthMethod[]
  default_credential_policy: MCPCredentialScope
  static_form_schema: MCPTemplateStaticFormField[] | null
  status: 'active' | 'deprecated' | 'disabled'
}

export interface MCPConnectorInstall {
  install_id: string
  template_id: string | null
  install_scope: 'org' | 'workspace'
  workspace_id: string | null
  name: string
  auth_method: MCPAuthMethod
  default_credential_policy: MCPCredentialScope
  auth_status: string
  discovery_status: string
  install_state: 'active' | 'uninstalled'
}

export interface MCPWorkspaceConnectorState {
  workspace_id: string
  install_id: string
  enabled: boolean
  credential_policy: MCPCredentialScope
}

export interface MCPEffectiveConnector {
  template: MCPConnectorTemplate | null
  install: MCPConnectorInstall
  workspace_state: MCPWorkspaceConnectorState
  credential_policy: MCPCredentialScope
  credential_availability: 'available' | 'missing' | 'not_required'
  credential_source: 'org' | 'workspace' | 'user' | null
  usable: boolean
  reason: string
}
```

- [ ] **Step 2: Replace API helpers**

Edit `frontend/packages/core/src/api/mcp.ts`.

Add helpers for final paths:

```typescript
export async function wsListTemplates(client: ApiClient, wsId: string) {
  const res = await client.get(`/api/v1/ws/${wsId}/mcp/templates`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { items: MCPConnectorTemplate[] }
}

export async function wsCreateInstall(client: ApiClient, wsId: string, body: unknown) {
  const res = await client.post(`/api/v1/ws/${wsId}/mcp/installs`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPConnectorInstall
}

export async function wsPatchInstallState(
  client: ApiClient,
  wsId: string,
  installId: string,
  body: Partial<MCPWorkspaceConnectorState>,
) {
  const res = await client.patch(`/api/v1/ws/${wsId}/mcp/installs/${installId}/state`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPWorkspaceConnectorState
}

export async function wsListEffectiveConnectors(client: ApiClient, wsId: string) {
  const res = await client.get(`/api/v1/ws/${wsId}/mcp/connectors`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { items: MCPEffectiveConnector[] }
}
```

Remove helpers whose names include `Catalog` or `Override`.

- [ ] **Step 3: Add API path tests**

Append to `frontend/packages/core/__tests__/api/mcp.test.ts`:

```typescript
it('uses template and install paths for workspace MCP', async () => {
  const { client, fetchMock } = makeClient({
    ok: true,
    json: async () => ({ items: [] }),
  })

  await wsListTemplates(client, 'ws-x')
  expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/ws/ws-x/mcp/templates')
})

it('does not use catalog or override paths', async () => {
  const source = await import('../../src/api/mcp')
  const exportedNames = Object.keys(source)

  expect(exportedNames.some((name) => name.includes('Catalog'))).toBe(false)
  expect(exportedNames.some((name) => name.includes('Override'))).toBe(false)
})
```

- [ ] **Step 4: Run frontend core checks**

Run:

```bash
cd frontend
pnpm --filter @cubebox/core test -- mcp.test.ts
pnpm --filter @cubebox/core type-check
```

Expected: both commands pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/core/src/types/mcp.ts \
        frontend/packages/core/src/api/mcp.ts \
        frontend/packages/core/src/types/workspace-settings.ts \
        frontend/packages/core/src/api/workspace-settings.ts \
        frontend/packages/core/src/stores/workspaceSettingsStore.ts \
        frontend/packages/core/__tests__/api/mcp.test.ts
git commit -m "feat(mcp): switch frontend core to four-layer API"
```

---

## Task 8: Frontend UI Terminology And Flows

**Files:**

- Rename: `frontend/packages/web/components/mcp/MCPCatalogInstallPanel.tsx`
  to `frontend/packages/web/components/mcp/MCPTemplateInstallPanel.tsx`
- Modify: `frontend/packages/web/components/workspace-settings/McpPanel.tsx`
- Modify: `frontend/packages/web/components/mcp/MCPConnectorList.tsx`
- Modify: `frontend/packages/web/components/mcp/MCPAdminDetailPanel.tsx`
- Modify: `frontend/packages/web/components/mcp/MCPWorkspacesTab.tsx`
- Modify: `frontend/packages/web/messages/en.json`
- Modify: `frontend/packages/web/messages/zh.json`
- Modify: `frontend/packages/web/__tests__/e2e/mcp/ws-mcp.spec.ts`
- Modify: `frontend/packages/web/__tests__/e2e/mcp/admin-mcp.spec.ts`

- [ ] **Step 1: Rename the install panel file**

Run:

```bash
git mv frontend/packages/web/components/mcp/MCPCatalogInstallPanel.tsx \
       frontend/packages/web/components/mcp/MCPTemplateInstallPanel.tsx
```

Update imports that referenced `MCPCatalogInstallPanel`.

- [ ] **Step 2: Replace UI copy**

Use these final product labels:

```text
Connector templates
Connector installs
Workspace state
Credential policy
Org grant
Workspace grant
My grant
Connect
Disconnect
Uninstall
Needs your credential
Ready
```

Remove visible UI copy containing `Catalog`, `Override`, `catalog`, or `override`.

- [ ] **Step 3: Add message keys**

Add to `frontend/packages/web/messages/en.json` under `mcp`:

```json
"templates": "Connector templates",
"installs": "Connector installs",
"workspaceState": "Workspace state",
"credentialPolicy": "Credential policy",
"orgGrant": "Org grant",
"workspaceGrant": "Workspace grant",
"myGrant": "My grant",
"needsCredential": "Needs your credential",
"ready": "Ready"
```

Add to `frontend/packages/web/messages/zh.json` under `mcp`:

```json
"templates": "连接器模板",
"installs": "连接器安装",
"workspaceState": "工作区状态",
"credentialPolicy": "凭证策略",
"orgGrant": "组织授权",
"workspaceGrant": "工作区授权",
"myGrant": "我的授权",
"needsCredential": "需要你的凭证",
"ready": "可用"
```

- [ ] **Step 4: Add E2E copy assertions**

Append to `frontend/packages/web/__tests__/e2e/mcp/ws-mcp.spec.ts`:

```typescript
await expect(page.getByText('Connector templates')).toBeVisible()
await expect(page.getByText('Workspace state')).toBeVisible()
await expect(page.getByText('Credential policy')).toBeVisible()
await expect(page.getByText('Override')).toHaveCount(0)
await expect(page.getByText('Catalog')).toHaveCount(0)
```

Append to `frontend/packages/web/__tests__/e2e/mcp/admin-mcp.spec.ts`:

```typescript
await expect(page.getByText('Connector installs')).toBeVisible()
await expect(page.getByText('Org grant')).toBeVisible()
await expect(page.getByText('Override')).toHaveCount(0)
await expect(page.getByText('Catalog')).toHaveCount(0)
```

- [ ] **Step 5: Run frontend checks**

Run:

```bash
cd frontend
pnpm --filter @cubebox/web type-check
pnpm --filter @cubebox/web test:e2e -- mcp/ws-mcp.spec.ts mcp/admin-mcp.spec.ts
```

Expected: typecheck passes and both MCP E2E specs pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/components/workspace-settings/McpPanel.tsx \
        frontend/packages/web/components/mcp/MCPTemplateInstallPanel.tsx \
        frontend/packages/web/components/mcp/MCPConnectorList.tsx \
        frontend/packages/web/components/mcp/MCPAdminDetailPanel.tsx \
        frontend/packages/web/components/mcp/MCPWorkspacesTab.tsx \
        frontend/packages/web/messages/en.json \
        frontend/packages/web/messages/zh.json \
        frontend/packages/web/__tests__/e2e/mcp/ws-mcp.spec.ts \
        frontend/packages/web/__tests__/e2e/mcp/admin-mcp.spec.ts
git commit -m "feat(mcp): update UI to connector template model"
```

---

## Task 9: Final Cleanup And Verification

**Files:**

- Modify files found by the search commands in this task.

- [ ] **Step 1: Search for removed public concepts**

Run:

```bash
rg -n "Catalog|catalog|Override|override|mcp_catalog|workspace_mcp_overrides" \
  backend/cubebox frontend/packages/core frontend/packages/web \
  -g '!**/.venv/**' -g '!**/node_modules/**'
```

Expected remaining matches are limited to migration filenames, migration comments, or tests
that assert old routes are absent.

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

- [ ] **Step 4: Manual smoke test**

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

Open the worktree `BASE_URL`, install a no-auth connector template into a workspace,
and confirm the connector appears as `Ready` without creating any credential grant.

- [ ] **Step 5: Commit verification cleanup**

Run:

```bash
git status --short
```

If files changed during cleanup:

```bash
git add backend/cubebox frontend/packages
git commit -m "chore(mcp): remove old catalog and override surfaces"
```
