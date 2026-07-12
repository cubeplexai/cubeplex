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
- Use MCP-specific public ID prefixes now:
  `mctpl`, `mcins`, `mcwcs`, `mcgrn`.
- Existing local MCP data can be dropped by migration. The product has no released
  external contract.
- Keep the credential vault table; grant rows point to vault credential ids.
- Keep `backend/cubeplex/models/mcp.py` as the MCP model module, but replace the model
  classes inside it with the four final MCP-prefixed nouns.

**Plan granularity:**

This plan describes design points, key steps, and core logic — not full source.
Subagents fill in actual SQLModel definitions, schema bodies, route handlers,
and service code by following existing patterns in the codebase (cubepi conventions,
`OrgScopedMixin` style, the `mcp_oauth` module's existing handlers, etc.). Test
snippets and field/route inventories are **contracts** the implementation must
satisfy; they are not boilerplate to be pasted verbatim.

---

## File Structure

**Backend — create:**

- `backend/alembic/versions/<autogen-hash>_normalize_mcp_four_layer_tables.py`
  (produced by `alembic revision --autogenerate`; do not hard-code the hash)
- `backend/cubeplex/mcp/effective.py`
- `backend/cubeplex/services/mcp_templates.py`
- `backend/cubeplex/services/mcp_installs.py`
- `backend/tests/unit/test_mcp_effective_state.py`
- `backend/tests/unit/test_mcp_effective_service.py`
- `backend/tests/e2e/test_mcp_four_layer_routes.py`
- `backend/tests/e2e/test_mcp_four_layer_runtime.py`

**Backend — modify:**

- `backend/cubeplex/models/mcp.py` — final four model classes.
- `backend/cubeplex/repositories/mcp.py` — final four repositories.
- Rename `backend/cubeplex/mcp/catalog_seed.py` to
  `backend/cubeplex/mcp/template_seed.py`.
- Rename `backend/cubeplex/seeders/mcp_catalog_seeder.py` to
  `backend/cubeplex/seeders/mcp_template_seeder.py`.
- Rename `backend/cubeplex/cli/seed_mcp_catalog.py` to
  `backend/cubeplex/cli/seed_mcp_templates.py`.
- `backend/cubeplex/mcp/dependencies.py` — new template/install/effective providers.
- `backend/cubeplex/api/schemas/mcp.py` — template/install/state/grant schemas.
- `backend/cubeplex/api/routes/v1/admin_mcp.py` — admin template/install/grant routes
  (and the public `GET /api/v1/mcp/templates` route, mounted unscoped).
- `backend/cubeplex/api/routes/v1/ws_mcp.py` — workspace template/install/state/grant routes.
- `backend/cubeplex/api/routes/v1/mcp_oauth.py` — align start/callback to new grant table
  + new oauth/start paths.
- `backend/cubeplex/api/app.py` — stop mounting old catalog router.
- `backend/cubeplex/mcp/cubepi_discovery.py` — use effective runtime specs.
- `backend/cubeplex/mcp/cubepi_runtime.py` — load only usable effective connectors.
- `backend/cubeplex/streams/run_manager.py` — pass OAuth token manager to runtime.

**Backend — delete:**

- `backend/cubeplex/api/routes/v1/mcp_catalog.py`
- `backend/cubeplex/repositories/mcp_catalog.py`
- `backend/cubeplex/services/mcp_catalog.py`

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

- Create: `backend/alembic/versions/<autogen-hash>_normalize_mcp_four_layer_tables.py`
  (via `alembic revision --autogenerate`)
- Modify: `backend/cubeplex/models/mcp.py`
- Modify: `backend/tests/unit/test_mcp_models.py`

- [ ] **Step 1: Write model tests for final prefixed table names**

Append to `backend/tests/unit/test_mcp_models.py`:

```python
def test_mcp_four_layer_table_names() -> None:
    from cubeplex.models import (
        MCPConnectorInstall,
        MCPConnectorTemplate,
        MCPCredentialGrant,
        MCPWorkspaceConnectorState,
    )

    assert MCPConnectorTemplate.__tablename__ == "mcp_connector_templates"
    assert MCPConnectorInstall.__tablename__ == "mcp_connector_installs"
    assert MCPWorkspaceConnectorState.__tablename__ == "mcp_workspace_connector_states"
    assert MCPCredentialGrant.__tablename__ == "mcp_credential_grants"
    assert MCPConnectorTemplate._PREFIX == "mctpl"
    assert MCPConnectorInstall._PREFIX == "mcins"
    assert MCPWorkspaceConnectorState._PREFIX == "mcwcs"
    assert MCPCredentialGrant._PREFIX == "mcgrn"


def test_no_auth_install_defaults_to_none_policy() -> None:
    from cubeplex.models import MCPConnectorInstall

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

Rewrite `backend/cubeplex/models/mcp.py` with four SQLModel `table=True` classes.
Follow existing conventions (`CubeplexBase`, `_PREFIX`, `Field(foreign_key=...)`,
JSON columns via `sa_column=Column(JSON, ...)`). Do **not** inherit `OrgScopedMixin`
on any of these — installs/grants have nullable `workspace_id` and grants have
nullable `user_id`, which `OrgScopedMixin`'s NOT NULL contract forbids. Each model
declares its own `org_id`/`workspace_id` fields explicitly.

Required shape per model:

**`MCPConnectorTemplate`** (`mctpl`, table `mcp_connector_templates`, global — no `org_id`):

- Identifying: `slug` (unique, indexed), `name`, `description`, `provider`.
- Runtime defaults: `server_url`, `transport`, `supported_auth_methods` (JSON list),
  `default_credential_policy`.
- OAuth metadata (nullable): `oauth_dcr_supported`, `oauth_default_scope`,
  `oauth_static_client_id`, `oauth_static_client_secret_credential_id`
  (FK → `credentials.id`; points at a system credential row with `org_id IS NULL`
  per the existing vault scope-model pattern).
- Static auth metadata (nullable): `static_form_schema` (JSON), `static_auth_header_template`.
- Free-form metadata (JSON, non-null, default `{}`): `template_metadata`,
  `tool_citation_defaults`.
- Lifecycle: `status` (`active` | `deprecated` | `disabled`, default `active`).
- Unique constraint on `slug`.

**`MCPConnectorInstall`** (`mcins`, table `mcp_connector_installs`):

- Scope: `org_id` (FK, indexed, required), `workspace_id` (FK, indexed, **nullable** —
  required iff `install_scope=workspace`), `install_scope` (`org` | `workspace`),
  `template_id` (FK, nullable — `NULL` means custom install).
- Identity: `name`, `server_url`, `server_url_hash` (64-char), `transport`.
- Auth: `auth_method` (`oauth` | `static` | `none`), `default_credential_policy`
  (`org` | `workspace` | `user` | `none`).
- State: `auth_status` (`not_required` | `pending` | `authorized` | `disconnected` | `error`),
  `discovery_status` (`not_run` | `success` | `error`), `install_state`
  (`active` | `uninstalled`, server_default `'active'`).
- Caches/config (JSON): `oauth_client_config`, `headers`, `tools_cache`, `tool_citations`.
- Diagnostics: `last_error`, `last_discovered_at`, `timeout`, `sse_read_timeout`.
- Distribution: `auto_enroll_new_workspaces` (bool, server_default `true`) —
  flips `WorkspaceConnectorState` auto-creation when a new workspace lands in the org.
- Audit: `created_by_user_id` (FK).
- Constraints (Postgres treats NULL as distinct for plain `UNIQUE`, so use
  partial unique indexes per scope rather than multi-column `UniqueConstraint`
  with nullable columns — otherwise two org-scope installs with the same URL
  both pass uniqueness):
  - Partial index `uq_mcp_connector_install_url_org` on `(org_id, server_url_hash)`
    where `workspace_id IS NULL AND install_state = 'active'`.
  - Partial index `uq_mcp_connector_install_url_ws` on
    `(org_id, workspace_id, server_url_hash)` where
    `workspace_id IS NOT NULL AND install_state = 'active'`.
  - Partial index `uq_mcp_connector_install_name_org` on `(org_id, name)` where
    `workspace_id IS NULL AND install_state = 'active'`.
  - Partial index `uq_mcp_connector_install_name_ws` on
    `(org_id, workspace_id, name)` where
    `workspace_id IS NOT NULL AND install_state = 'active'`.
  - Partial index `uq_mcp_connector_install_per_template_org` on
    `(org_id, template_id)` where
    `workspace_id IS NULL AND template_id IS NOT NULL AND install_state = 'active'`.
  - Partial index `uq_mcp_connector_install_per_template_ws` on
    `(org_id, workspace_id, template_id)` where
    `workspace_id IS NOT NULL AND template_id IS NOT NULL AND install_state = 'active'`.
  - All install_state-gated indexes intentionally exclude `uninstalled` rows so
    a tombstoned install does not block reinstalling the same template/URL/name.
  - Check constraints (`ck_*`): `install_scope IN ('org','workspace')`,
    `auth_method IN ('oauth','static','none')`.

**`MCPWorkspaceConnectorState`** (`mcwcs`, table `mcp_workspace_connector_states`):

- `org_id` (FK, indexed), `workspace_id` (FK, indexed, required),
  `install_id` (FK, indexed, required).
- `enabled` (bool, default `true`), `credential_policy`
  (`org` | `workspace` | `user` | `none`), `enablement_source`
  (`admin_auto` | `admin_manual` | `workspace_manual`), `updated_by_user_id` (FK).
- Unique on `(workspace_id, install_id)`.
- Check: `credential_policy IN ('org','workspace','user','none')`.

**`MCPCredentialGrant`** (`mcgrn`, table `mcp_credential_grants`):

- `org_id` (FK, indexed), `install_id` (FK, indexed), `grant_scope`
  (`org` | `workspace` | `user`).
- `workspace_id` (FK, indexed, nullable — required iff `grant_scope=workspace` or
  `=user`), `user_id` (FK, indexed, nullable — required iff `grant_scope=user`).
- `credential_id` (FK → `credentials.id`, required, max_length 20),
  `refresh_credential_id` (FK → `credentials.id`, nullable, max_length 20).
- `expires_at` (nullable, OAuth access-token expiry), `grant_status`
  (`valid` | `missing` | `expired` | `revoked` | `error`, default `valid`),
  `created_by_user_id` (FK).
- Uniqueness — same NULL-distinct issue as installs; replace the single
  multi-column `UniqueConstraint` with three partial unique indexes, one per
  scope, so org grants can't be duplicated under NULL `workspace_id`/`user_id`:
  - `uq_mcp_credential_grant_org` on `(install_id)` where `grant_scope = 'org'`.
  - `uq_mcp_credential_grant_workspace` on `(install_id, workspace_id)` where
    `grant_scope = 'workspace'`.
  - `uq_mcp_credential_grant_user` on `(install_id, workspace_id, user_id)` where
    `grant_scope = 'user'`.
- Check: `grant_scope IN ('org','workspace','user')`, plus row-level guards
  enforced at the service layer (`workspace_id` non-null iff scope ∈
  `{workspace,user}`; `user_id` non-null iff scope = `user`). The check
  constraint version of the row-level guards (`(grant_scope='org' AND
  workspace_id IS NULL AND user_id IS NULL) OR ...`) should also be added —
  it costs nothing and turns a programming bug into a DB error before the
  partial unique index sees it.

Update `backend/cubeplex/models/__init__.py` to export the four classes and remove
old MCP model exports.

- [ ] **Step 4: Generate the migration via Alembic autogenerate, then patch what autogen can't see**

Per repo convention (`backend/CLAUDE.md`, "After modifying SQLModel schemas, use
auto-generation"), do not hand-write the table create/drop. Run:

```bash
cd backend
uv run alembic revision --autogenerate -m "normalize mcp four-layer tables"
```

Alembic will emit a new file under `backend/alembic/versions/<hash>_normalize_mcp_four_layer_tables.py`
containing:

- `op.drop_table(...)` for `user_mcp_credentials`, `workspace_mcp_credentials`,
  `workspace_mcp_overrides`, `mcp_servers`, `mcp_catalog_connectors`.
- `op.create_table(...)` for the four new tables with columns, FKs, plain
  `UniqueConstraint`s, and indexes reflected off the SQLModel classes.

Then open the generated file and manually add what autogenerate cannot infer:

1. **All partial unique indexes** — autogen cannot produce `postgresql_where`,
   so every entry from the Step 3 partial-index list lands here as an explicit
   `op.create_index(..., unique=True, postgresql_where=sa.text(...))`. That covers
   the six install indexes (URL × org/ws, name × org/ws, template × org/ws) and
   the three grant scope indexes (`org`, `workspace`, `user`). All install indexes
   that exclude `install_state='uninstalled'` must include that predicate in their
   `postgresql_where` so uninstalled rows do not block reinstall.

2. **Check constraints** (autogen sometimes drops `CheckConstraint` produced by
   SQLModel; verify the generated file and add any that are missing):

   - `ck_mcp_connector_installs_scope`: `install_scope IN ('org','workspace')`
   - `ck_mcp_connector_installs_auth_method`: `auth_method IN ('oauth','static','none')`
   - `ck_mcp_workspace_connector_states_policy`:
     `credential_policy IN ('org','workspace','user','none')`
   - `ck_mcp_credential_grants_scope`: `grant_scope IN ('org','workspace','user')`
   - `ck_mcp_credential_grants_scope_columns`:
     `(grant_scope='org' AND workspace_id IS NULL AND user_id IS NULL)
      OR (grant_scope='workspace' AND workspace_id IS NOT NULL AND user_id IS NULL)
      OR (grant_scope='user' AND workspace_id IS NOT NULL AND user_id IS NOT NULL)`

3. **Downgrade**: destructive migration is not reversible. Replace the autogenerated
   `downgrade()` body with:

   ```python
   raise RuntimeError("Destructive MCP four-layer migration is not reversible")
   ```

Do not hard-code the revision hash anywhere; trust whatever Alembic generated.

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
git add backend/cubeplex/models/mcp.py \
        backend/cubeplex/models/__init__.py \
        backend/alembic/versions/*_normalize_mcp_four_layer_tables.py \
        backend/tests/unit/test_mcp_models.py
git commit -m "feat(mcp): add four-layer connector schema"
```

---

## Task 2: Repositories And Template Seeding

**Files:**

- Modify: `backend/cubeplex/repositories/mcp.py`
- Create: `backend/cubeplex/services/mcp_templates.py`
- Rename: `backend/cubeplex/mcp/catalog_seed.py` to
  `backend/cubeplex/mcp/template_seed.py`
- Rename: `backend/cubeplex/seeders/mcp_catalog_seeder.py` to
  `backend/cubeplex/seeders/mcp_template_seeder.py`
- Rename: `backend/cubeplex/cli/seed_mcp_catalog.py` to
  `backend/cubeplex/cli/seed_mcp_templates.py`
- Modify: `backend/tests/unit/test_catalog_seed.py`
- Modify: `backend/tests/unit/test_mcp_repositories.py`

- [ ] **Step 1: Write repository tests for final nouns**

Append to `backend/tests/unit/test_mcp_repositories.py`:

```python
async def test_connector_template_repository_upserts_by_slug(session: AsyncSession) -> None:
    from cubeplex.repositories.mcp import MCPConnectorTemplateRepository

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
    from cubeplex.models import MCPCredentialGrant
    from cubeplex.repositories.mcp import MCPCredentialGrantRepository

    repo = MCPCredentialGrantRepository(session, org_id="org-1")
    await repo.add(
        MCPCredentialGrant(
            org_id="org-1",
            install_id="mcins-1",
            grant_scope="user",
            user_id="user-1",
            credential_id="cred-1",
            created_by_user_id="user-1",
        )
    )

    assert await repo.get_user_grant(install_id="mcins-1", user_id="user-1") is not None
    assert await repo.get_user_grant(install_id="mcins-1", user_id="user-2") is None
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

Edit `backend/cubeplex/repositories/mcp.py` so it exports these concrete methods:

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

**Repository scoping note.** The existing `ScopedRepository[T]` (in
`backend/cubeplex/repositories/base.py`) requires `OrgScopedMixin`, which the new MCP
models cannot use (installs/grants have nullable `workspace_id`; templates have no
`org_id` at all). Introduce a *new* lightweight org-only base instead — every
non-template repo takes `org_id` in `__init__`, every query filters by `org_id`,
and `add()` force-sets `org_id` to defend against cross-org writes. `MCPConnectorTemplate`
has no org scope. Document the deviation from `ScopedRepository` in a short module
docstring so future engineers don't try to inherit it.

- [ ] **Step 4: Create template service**

Create `backend/cubeplex/services/mcp_templates.py`:

```python
"""Connector template service."""

from cubeplex.models import MCPConnectorTemplate
from cubeplex.repositories.mcp import MCPConnectorTemplateRepository


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
git mv backend/cubeplex/mcp/catalog_seed.py backend/cubeplex/mcp/template_seed.py
git mv backend/cubeplex/seeders/mcp_catalog_seeder.py \
       backend/cubeplex/seeders/mcp_template_seeder.py
git mv backend/cubeplex/cli/seed_mcp_catalog.py backend/cubeplex/cli/seed_mcp_templates.py
```

In `backend/cubeplex/mcp/template_seed.py`, rename `CatalogSeedEntry` to
`MCPConnectorTemplateSeedEntry`. Rename fields:

```python
default_credential_scope -> default_credential_policy
static_form_fields -> static_form_schema
cred_metadata -> template_metadata
tool_citations -> tool_citation_defaults
```

Update `backend/cubeplex/seeders/mcp_template_seeder.py` and
`backend/cubeplex/cli/seed_mcp_templates.py` to call `MCPConnectorTemplateRepository`.

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
git rm backend/cubeplex/repositories/mcp_catalog.py backend/cubeplex/services/mcp_catalog.py
```

- [ ] **Step 8: Commit**

```bash
git add backend/cubeplex/repositories/mcp.py \
        backend/cubeplex/services/mcp_templates.py \
        backend/cubeplex/mcp/template_seed.py \
        backend/cubeplex/seeders/mcp_template_seeder.py \
        backend/cubeplex/cli/seed_mcp_templates.py \
        backend/tests/unit/test_catalog_seed.py \
        backend/tests/unit/test_mcp_repositories.py
git commit -m "feat(mcp): replace catalog repositories with connector templates"
```

---

## Task 3: Install, Workspace State, And Grant Services

**Files:**

- Create: `backend/cubeplex/services/mcp_installs.py`
- Modify: `backend/cubeplex/mcp/dependencies.py`
- Modify: `backend/tests/unit/test_mcp_service_invariants.py`
- Create: `backend/tests/e2e/test_mcp_four_layer_routes.py`

- [ ] **Step 1: Add service invariant tests**

Append to `backend/tests/unit/test_mcp_service_invariants.py`:

```python
def test_auth_method_none_resolves_not_required_defaults() -> None:
    from cubeplex.services.mcp_installs import install_defaults_for_auth_method

    defaults = install_defaults_for_auth_method("none", "user")

    assert defaults.auth_status == "not_required"
    assert defaults.credential_policy == "none"


def test_static_auth_uses_requested_policy() -> None:
    from cubeplex.services.mcp_installs import install_defaults_for_auth_method

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

Expected: FAIL because `cubeplex.services.mcp_installs` does not exist.

- [ ] **Step 3: Create install service primitives**

Create `backend/cubeplex/services/mcp_installs.py`. The module exposes:

- A frozen dataclass `MCPInstallDefaults(auth_status: str, credential_policy: str)`.
- A pure function `install_defaults_for_auth_method(auth_method, requested_policy)
  -> MCPInstallDefaults` with these invariants (the existing unit tests are the contract):
  - `auth_method=="none"` → `auth_status="not_required"`, `credential_policy="none"`
    (regardless of what was requested — "none" must never become user-scope).
  - Otherwise → `auth_status="pending"`, `credential_policy=requested_policy`.
- A class `MCPConnectorInstallService(install_repo, state_repo, grant_repo,
  cred_service, *, org_id, actor_user_id)` with these methods:
  - `create_from_template_for_workspace(*, template, workspace_id, auth_method,
    credential_policy)` — writes one install with `install_scope="workspace"`,
    copies `tool_citation_defaults → tool_citations`, hashes the server URL
    (reuse `cubeplex.mcp._constants.server_url_hash`), then upserts a
    `WorkspaceConnectorState(enabled=True, enablement_source="workspace_manual")`
    in the same transaction (atomically — failure rolls both back).
  - `create_from_template_for_org(*, template, auth_method, credential_policy,
    distribution)` — analogous to above but `install_scope="org"`,
    `workspace_id=None`; if `distribution.mode == "auto"` (all current workspaces)
    or `"selected"` (list of `workspace_ids`), create `WorkspaceConnectorState`
    rows with `enablement_source="admin_auto"`/`"admin_manual"` accordingly.
  - `create_static_grant(*, install_id, grant_scope, plaintext, workspace_id=None,
    user_id=None, name=None)` — stores the secret in the vault via
    `CredentialService.create(kind=CREDENTIAL_KIND_MCP, ...)`, then writes the
    grant row pointing at the vault id. Validates scope-vs-fk combination
    matching the DB check constraint exactly:
    - `org` ⇒ `workspace_id` and `user_id` MUST both be `None`.
    - `workspace` ⇒ `workspace_id` MUST be set, `user_id` MUST be `None`.
    - `user` ⇒ **both** `workspace_id` AND `user_id` MUST be set (user grants
      are scoped per workspace, so the workspace context is recorded on the
      grant row to prevent cross-workspace reuse — the `/grants/me` route
      passes the current `workspace_id` from the path).
    Wrong shape → `ValueError` before any vault or DB write.
  - `disconnect_grant(*, install_id, grant_scope, workspace_id=None, user_id=None)`
    — deletes the grant row (and OAuth-side revoke when available). Does **not**
    touch install or workspace state.
  - `uninstall(install_id)` — flips `install_state="uninstalled"`,
    `auth_status="disconnected"`, bumps `updated_at`. Does not delete workspace
    state rows (the effective-state service treats `install_state != "active"`
    as unusable, per the spec's reason matrix).

No fall-back fan-out: a `credential_policy=user` request must never write or read
an `org`-scope grant; enforce this at the service boundary as a positive assertion,
not just an absence of code.

- [ ] **Step 4: Add dependency providers**

In `backend/cubeplex/mcp/dependencies.py`, add the FastAPI dependency providers
needed for the workspace and admin route surfaces. The split is necessary
because admin routes are org-scoped (no `workspace_id` in the path) and use
`get_admin_request_context` / `require_org_admin`; workspace routes use
`request_context` / `require_member`. Reusing a member-scoped provider on
admin routes would either reject the call or scope through an arbitrary
workspace membership.

Providers:

- `get_connector_template_service(session) -> MCPConnectorTemplateService` —
  global, no org scope; used by both admin and workspace routes.
- `get_ws_install_service(session, cred_service, ctx=Depends(request_context))
  -> MCPConnectorInstallService` — used by workspace routes. Instantiates the
  three repos with `org_id=ctx.org_id` from workspace membership, passes
  `actor_user_id=ctx.user.id`.
- `get_admin_install_service(session, cred_service,
  ctx=Depends(get_admin_request_context)) -> MCPConnectorInstallService` —
  used by admin routes. Same construction but `org_id` comes from the admin
  context (no workspace), and the underlying calls into the service that
  involve workspace fan-out get an explicit `workspace_ids` list from the
  request body, not from `ctx`.

Remove old `get_*catalog*` / `get_*override*` providers from the same module.

- [ ] **Step 5: Run service tests**

Run:

```bash
cd backend
uv run pytest -q tests/unit/test_mcp_service_invariants.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/services/mcp_installs.py \
        backend/cubeplex/mcp/dependencies.py \
        backend/tests/unit/test_mcp_service_invariants.py
git commit -m "feat(mcp): add install state and grant services"
```

---

## Task 4: Final API Routes (Including OAuth Alignment)

**Files:**

- Modify: `backend/cubeplex/api/schemas/mcp.py`
- Modify: `backend/cubeplex/api/routes/v1/admin_mcp.py`
- Modify: `backend/cubeplex/api/routes/v1/ws_mcp.py`
- Modify: `backend/cubeplex/api/routes/v1/mcp_oauth.py`
- Modify: `backend/cubeplex/api/app.py`
- Delete: `backend/cubeplex/api/routes/v1/mcp_catalog.py`
- Modify: `backend/tests/unit/test_admin_mcp_routes.py`
- Modify: `backend/tests/unit/test_ws_mcp_routes.py`
- Modify: `backend/tests/e2e/test_mcp_four_layer_routes.py`

- [ ] **Step 1: Add route registration tests**

Replace the MCP route assertions in `backend/tests/unit/test_ws_mcp_routes.py` with
the workspace-side contract. The test must assert each of the following is registered:

- `GET    /api/v1/ws/{workspace_id}/mcp/templates`
- `GET    /api/v1/ws/{workspace_id}/mcp/connectors`
- `POST   /api/v1/ws/{workspace_id}/mcp/installs`  (workspace-local install create)
- `DELETE /api/v1/ws/{workspace_id}/mcp/installs/{install_id}`  (workspace-local uninstall)
- `PATCH  /api/v1/ws/{workspace_id}/mcp/connectors/{install_id}/state`
  (matches spec §API Shape — workspace state edit lives under `/connectors`, not
  `/installs`; the install path is reserved for install-lifecycle operations)
- `POST   /api/v1/ws/{workspace_id}/mcp/installs/{install_id}/grants/me`
- `DELETE /api/v1/ws/{workspace_id}/mcp/installs/{install_id}/grants/me`
- `POST   /api/v1/ws/{workspace_id}/mcp/installs/{install_id}/grants/me/oauth/start`
- `POST   /api/v1/ws/{workspace_id}/mcp/installs/{install_id}/grants/workspace`
- `DELETE /api/v1/ws/{workspace_id}/mcp/installs/{install_id}/grants/workspace`
- `POST   /api/v1/ws/{workspace_id}/mcp/installs/{install_id}/grants/workspace/oauth/start`

And negative assertions that `/mcp/catalog`, `/mcp/org-installs/.../override`, and
any `/mcp/servers/...` are absent.

Replace the MCP route assertions in `backend/tests/unit/test_admin_mcp_routes.py`
with the admin-side contract. Required:

- `GET    /api/v1/admin/mcp/templates`                  (template library)
- `GET    /api/v1/admin/mcp/installs`                   (list org installs)
- `POST   /api/v1/admin/mcp/installs`                   (create org install, with optional
   `template_id` and `auto_enable` distribution payload)
- `GET    /api/v1/admin/mcp/installs/{install_id}`      (detail)
- `PATCH  /api/v1/admin/mcp/installs/{install_id}`      (edit default_credential_policy,
   auto_enroll_new_workspaces, headers, etc.)
- `DELETE /api/v1/admin/mcp/installs/{install_id}`      (uninstall)
- `POST   /api/v1/admin/mcp/installs/{install_id}/grants/org`
- `DELETE /api/v1/admin/mcp/installs/{install_id}/grants/org`
- `POST   /api/v1/admin/mcp/installs/{install_id}/grants/org/oauth/start`

Plus a public route:

- `GET    /api/v1/mcp/templates`  (authenticated, but not workspace-scoped — used
   by the template library / global picker; may attach `install_summary` for the
   caller's current org when present).

And negative assertions: no `/admin/mcp/catalog/...`, no `/admin/mcp/servers/...`,
no `/admin/mcp/.../overrides`.

Methods are part of the contract: do **not** use `PUT` for grant create. POST creates
or replaces a scoped grant; DELETE disconnects it. This matches the spec's API
shape and lets the same body shape be reused for replace-on-rotation.

- [ ] **Step 2: Run route tests and confirm old route names still exist**

Run:

```bash
cd backend
uv run pytest -q tests/unit/test_ws_mcp_routes.py tests/unit/test_admin_mcp_routes.py
```

Expected: FAIL because current routes still expose old MCP paths.

- [ ] **Step 3: Replace MCP schemas**

Rewrite `backend/cubeplex/api/schemas/mcp.py` using final nouns. Use
`Literal["org","workspace","user","none"]` for credential policies,
`Literal["oauth","static","none"]` for auth methods. The contracts (one Pydantic
model per item; subagent decides field order, base class, etc.):

- `MCPConnectorTemplateOut`: `template_id`, `slug`, `name`, `provider`, `description`,
  `server_url`, `transport`, `supported_auth_methods`, `default_credential_policy`,
  `static_form_schema`, `status`.
- `MCPConnectorInstallOut`: `install_id`, `template_id`, `install_scope`, `workspace_id`,
  `name`, `server_url`, `transport`, `auth_method`, `default_credential_policy`,
  `auth_status`, `discovery_status`, `install_state`, `tool_count` (derived: `len(tools_cache)`),
  `last_error`, `auto_enroll_new_workspaces`.
- `MCPWorkspaceConnectorStateOut`: `workspace_id`, `install_id`, `enabled`,
  `credential_policy`, `enablement_source`.
- `MCPCredentialGrantStatusOut`: `install_id`, `grant_scope`, `workspace_id`, `user_id`,
  `grant_status`, `has_value` (true iff vault still holds the credential),
  `expires_at`.
- `MCPEffectiveConnectorOut` (per spec §API Shape example): `template` (nullable for
  custom installs), `install`, `workspace_state`, `credential_policy`,
  `required_grant_scope`, `credential_availability`
  (`available` | `missing` | `not_required`), `credential_source` (`org` | `workspace` |
  `user` | `null`), `usable`, `reason`.

Cross-field validation that must live in the request schemas (the install/state
endpoints can otherwise accept impossible combinations):

- `credential_policy="none"` is allowed **only** when `auth_method="none"`. The
  API rejects `credential_policy="none"` for OAuth or static installs with a
  422 pointing at `credential_policy`. The DB layer is also defended by the
  effective-state decision order (rule 5 keys on `auth_method`, not policy),
  but request-side validation is the user-facing gate.

- For PATCH endpoints whose body carries `credential_policy` without
  `auth_method` (`PATCH /admin/mcp/installs/{id}` setting
  `default_credential_policy`, `PATCH /ws/{ws}/mcp/connectors/{id}/state`
  setting `credential_policy`), schema-only validation can't enforce the
  pairing because the request body lacks `auth_method`. The **service layer**
  must load the install row first and reject any patch that sets
  `credential_policy="none"` on a row whose `auth_method != "none"` (and vice
  versa: cannot raise a non-`none` policy on an `auth_method="none"` install).
  Return 422 with the same field error shape the schema validator would have
  emitted.

Request schemas should also exist for:

- `AdminCreateInstallIn`: `template_id?`, `install_scope` (`org`),
  `auth_method`, `default_credential_policy`, `auto_enable?: {mode: "all"|"selected"|"none",
  workspace_ids?: list[str]}`, optional `server_url`/`transport`/`headers` for custom.
- `PatchInstallIn`: subset of mutable install fields (`default_credential_policy`,
  `auto_enroll_new_workspaces`, `headers`, `name`, ...). Reject any unknown key.
- `PatchWorkspaceStateIn`: `enabled?`, `credential_policy?`.
- `CreateGrantIn` (three flavors for org / workspace / me): `credential_plaintext`
  for static, `oauth_callback_state` for OAuth resolution, or empty body for OAuth-start.

- [ ] **Step 4: Replace admin routes**

Rewrite `backend/cubeplex/api/routes/v1/admin_mcp.py` to expose the admin contract
from Step 1 (template list, install CRUD, grant POST/DELETE, oauth/start). Each
handler delegates to `MCPConnectorInstallService` / `MCPConnectorTemplateService`;
no DB queries inline.

Also register the public template route:

- `GET /api/v1/mcp/templates` — authenticated; returns templates plus an optional
  `install_summary` (count of active installs for the caller's org) for each row.

Existing `mcp_oauth.py` start/callback handlers must be **aligned** in this task:

- The OAuth start endpoints now live at `…/grants/<scope>/oauth/start`; the
  callback writes an `MCPCredentialGrant` whose `grant_scope`, `workspace_id`,
  and `user_id` are recovered from the OAuth state token (not from session state).
- The callback must not write to `mcp_servers` or `workspace_mcp_credentials`
  (those tables are gone). Update its imports/repositories accordingly.
- The callback updates the install's `auth_status` from `pending` → `authorized`
  only when the grant being created has `grant_scope` matching
  `install.default_credential_policy`'s required scope; otherwise leave
  `auth_status` alone — the install can have multiple per-user grants without
  the install itself becoming "authorized".

Remove server/override/catalog handlers from this module.

- [ ] **Step 5: Replace workspace routes**

Rewrite `backend/cubeplex/api/routes/v1/ws_mcp.py` to expose the workspace contract
from Step 1. Authorization rules from spec §User Roles And Permissions:

- All routes require workspace membership (`require_member`).
- `POST /installs`, `DELETE /installs/{id}`, `PATCH /connectors/{id}/state`,
  and the workspace-scope grant routes (`POST`/`DELETE`/`oauth/start` under
  `/installs/{id}/grants/workspace`) require `role=admin` on the workspace.
- `*/grants/me*` routes are open to any member.
- A workspace member cannot delete a workspace-local install they did not create
  unless they are workspace admin.

Pay attention to the route prefixes when wiring the admin guard: the state edit
lives under `/connectors`, not `/installs`. A handler registered without the
workspace-admin dependency would let ordinary members enable/disable connectors
or change credential policy.

Remove old server/catalog/org-install override handlers from this module.

- [ ] **Step 6: Remove old route module from app**

Edit `backend/cubeplex/api/app.py` and remove the import/mount for
`cubeplex.api.routes.v1.mcp_catalog`.

Run:

```bash
git rm backend/cubeplex/api/routes/v1/mcp_catalog.py
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
git add backend/cubeplex/api/schemas/mcp.py \
        backend/cubeplex/api/routes/v1/admin_mcp.py \
        backend/cubeplex/api/routes/v1/ws_mcp.py \
        backend/cubeplex/api/routes/v1/mcp_oauth.py \
        backend/cubeplex/api/app.py \
        backend/tests/unit/test_admin_mcp_routes.py \
        backend/tests/unit/test_ws_mcp_routes.py
git commit -m "feat(mcp): replace catalog routes with four-layer API"
```

---

## Task 5: Effective State Service And Runtime

**Files:**

- Create: `backend/cubeplex/mcp/effective.py`
- Modify: `backend/cubeplex/mcp/cubepi_discovery.py`
- Modify: `backend/cubeplex/mcp/cubepi_runtime.py`
- Modify: `backend/cubeplex/streams/run_manager.py`
- Create: `backend/tests/unit/test_mcp_effective_state.py`
- Create: `backend/tests/unit/test_mcp_effective_service.py`
- Modify: `backend/tests/unit/test_mcp_cubepi_runtime.py`

- [ ] **Step 1: Add effective state tests (full reason matrix)**

`backend/tests/unit/test_mcp_effective_state.py` is the contract for the pure
function. Cover **every** terminal reason in the spec's reason table — the
function should never collapse "user vs workspace vs org grant missing" into a
single `credential_missing`, otherwise UI/admin cannot diagnose the connector.
Concrete test cases (subagent writes one test per row, names map to spec
reasons):

| Test case | Input shape | Expected `reason` |
| --- | --- | --- |
| no template, no install | `install_state="active"` but no install row for this template in org/ws | `not_installed` |
| install uninstalled | `install_state="uninstalled"` | `install_uninstalled` |
| template disabled | `template_status="disabled"` | `template_deprecated` (hard block) |
| template deprecated, install still active | `template_status="deprecated"`, otherwise authorized | `usable` — deprecation surfaces via the DTO's `template_status` field but does NOT block runtime |
| workspace state missing/disabled | `workspace_enabled=False` | `not_enabled_in_workspace` |
| unsupported transport | `transport="stdio"` | (skipped — spec doesn't include this; replace with `server_unreachable` if needed) |
| no-auth happy path | `auth_method="none"`, `credential_policy="none"`, no grant | `usable` |
| pending OAuth at org scope | `auth_status="pending"`, `credential_policy="org"`, no grant | `pending_oauth` |
| missing org grant | `auth_status="authorized"` but no row with `grant_scope="org"` | `missing_org_grant` |
| missing workspace grant | `credential_policy="workspace"`, no row at workspace scope | `missing_workspace_grant` |
| user needs to connect | `credential_policy="user"`, no row for current `user_id` | `user_needs_connection` |
| grant expired (refresh failed) | grant `status="expired"` and no refresh credential | `grant_expired` |
| discovery failed | `discovery_status="error"`, otherwise authorized | `discovery_failed` |
| valid user grant | `credential_policy="user"`, grant `scope="user" status="valid"` | `usable` |

The "user_needs_connection" / scope-specific reasons distinguish *which scope's*
grant is missing — runtime/UI consumers branch on this string.

- [ ] **Step 2: Implement pure effective-state model**

Create `backend/cubeplex/mcp/effective.py`. The module exports:

- `CredentialPolicy = Literal["org","workspace","user","none"]`.
- `MCPEffectiveReason` — a `Literal` covering every spec reason
  (`usable`, `not_installed`, `not_enabled_in_workspace`, `install_uninstalled`,
  `template_deprecated`, `pending_oauth`, `missing_org_grant`,
  `missing_workspace_grant`, `user_needs_connection`, `grant_expired`,
  `discovery_failed`, `server_unreachable`).
- `MCPGrantInput(scope: str, status: str, has_refresh: bool)` — the runtime's
  view of the resolved grant row.
- `MCPEffectiveInput` — frozen dataclass with at least these fields:
  - `template_status: str | None` (None ⇒ custom install with no template)
  - `install_present: bool` (False ⇒ caller decided "not installed")
  - `install_state: str`
  - `workspace_state_present: bool`
  - `workspace_enabled: bool`
  - `auth_method: str`
  - `auth_status: str`
  - `discovery_status: str`
  - `credential_policy: CredentialPolicy`
  - `grant: MCPGrantInput | None`
  - `transport: str`
- `MCPEffectiveResult(usable: bool, reason: MCPEffectiveReason,
  credential_availability: Literal["available","missing","not_required"])`.

Decision order in `compute_effective_state` (first match wins):

1. `install_present=False` → `not_installed`.
2. `install_state == "uninstalled"` → `install_uninstalled`.
3. `template_status == "disabled"` → `template_deprecated` (treated as a hard
   block — disabled means the template can no longer ship). `deprecated` is
   **not** a block: per the spec, "Template can be deprecated without breaking
   existing installs." Surface `template_status="deprecated"` in the DTO as a
   warning, but continue evaluating the remaining rules; the install can still
   be `usable=true`. Templates that are `None` (custom installs) skip this
   rule entirely.
4. `workspace_state_present=False` or `workspace_enabled=False` →
   `not_enabled_in_workspace`.
5. `auth_method == "none"` → `usable`, `credential_availability="not_required"`.
   Do **not** key this branch on `credential_policy == "none"` alone — an OAuth
   or static install with `credential_policy="none"` is a configuration bug
   (a credentialed connector cannot run without a credential), and the API
   layer must reject `credential_policy="none"` whenever `auth_method != "none"`
   so it can never reach this branch.
6. `auth_method == "oauth"` AND `credential_policy IN {"org", "workspace"}` AND
   `auth_status == "pending"` AND grant absent → `pending_oauth`. `pending_oauth`
   is an *install-scoped* state ("admin / workspace has not finished the OAuth
   handshake"); it must not mask per-user state. For
   `credential_policy == "user"`, every member has their own OAuth flow, so a
   missing user grant always falls through to rule 7 and reports
   `user_needs_connection` — even when the install row's `auth_status` is still
   `pending` from an earlier abandoned admin flow.
7. Grant absent → scope-specific missing reason
   (`missing_org_grant` / `missing_workspace_grant` / `user_needs_connection`
   based on `credential_policy`).
8. Grant present but `status == "expired"` and `has_refresh=False` →
   `grant_expired`.
9. Grant present but `scope != credential_policy` → treat as missing for that
   scope (no cross-scope fallback).
10. `discovery_status == "error"` → `discovery_failed`.
11. Otherwise → `usable`, `credential_availability="available"`.

The order matters: `discovery_failed` only blocks usability after all auth
gates pass, because discovery is only attempted once the connector is
authorized.

- [ ] **Step 3: Add DB-backed effective service**

Extend `backend/cubeplex/mcp/effective.py` with `MCPEffectiveConnectorService`. This
is the **only** place in the codebase that joins template + install + workspace state
+ grant to decide runtime usability. Two public methods:

- `list_for_workspace_user(workspace_id, user_id, *, include_unusable: bool=True)
  -> list[MCPEffectiveConnectorDTO]` — for UI/admin/diagnostics. Returns every
  install visible to the workspace (org installs that have a `WorkspaceConnectorState`
  row, plus all workspace-local installs in the same workspace). When
  `include_unusable=False`, drop rows whose `compute_effective_state` returns
  `usable=False`.
- `list_runtime_specs(workspace_id, user_id) -> list[MCPRuntimeConnectorSpec]` —
  for the agent runtime. Returns only `usable=True` rows with the minimum fields
  the loader needs (`install_id`, `server_url`, `transport`, `auth_method`,
  resolved `grant_scope`, `credential_id` reference, `tool_citations`,
  `tools_cache`).

Algorithm (sequential reads OK; everything is per-request and small):

1. Resolve the caller's `org_id` from workspace membership.
2. Load active workspace-local installs for this workspace and all active org
   installs in this org. Skip rows where `install_state != "active"`.
3. For each install, look up its `WorkspaceConnectorState` for `(workspace_id, install_id)`.
   Missing state for an org install ⇒ `enabled=False` (don't synthesize a row).
   Missing state for a workspace install ⇒ defect — log + treat as `enabled=False`.
4. Resolve the **required grant** by combining `state.credential_policy` (if state
   exists) else `install.default_credential_policy`:
   - `none` ⇒ no grant fetch; `credential_availability="not_required"`.
   - `org` ⇒ fetch `(install_id, grant_scope='org')`.
   - `workspace` ⇒ fetch `(install_id, 'workspace', workspace_id)`.
   - `user` ⇒ fetch `(install_id, 'user', workspace_id, user_id)`.
   No fall-back across scopes. The presence of an org grant must not flip a
   user-policy row to usable.
5. Load template by `install.template_id` (if any). Custom installs (template_id
   `NULL`) get `template_status=None`; `compute_effective_state` must treat
   `None` as "no template gate" (already implemented in the pure function).
6. For OAuth grants that look expired (`expires_at < now`), call
   `OAuthTokenManager.get_access_token(...)` — it refreshes via the
   `refresh_credential_id` and updates the grant in place. If refresh fails,
   set `grant_status="expired"` and let `compute_effective_state` mark unusable
   with reason `grant_expired`.
7. Hand off to `compute_effective_state` per row and assemble the DTO list.

Performance: one round-trip per layer (templates, installs, states, grants) using
`IN (...)` filters keyed by the install id set; do not N+1 per install.

- [ ] **Step 4: Wire credential resolution in the runtime loader**

`backend/cubeplex/mcp/cubepi_runtime.py::load_workspace_mcp_tools_for_cubepi` should
take `effective_service: MCPEffectiveConnectorService` and a non-optional
`token_manager: OAuthTokenManager`. For each `MCPRuntimeConnectorSpec` from
`list_runtime_specs(...)`, resolve the connection credential by `auth_method`:

- `oauth` → `token_manager.get_access_token(install_id, grant_scope, workspace_id,
  user_id)`; the token manager handles refresh + persistence already.
- `static` → fetch the vault row by `spec.credential_id` via `CredentialService`,
  decrypt, inject into the configured header template.
- `none` → mint a short-lived cubeplex identity token via the existing
  `cubeplex.mcp.user_token` helper (the same one `mcp_oauth` already uses for
  identity flow); do **not** look for a grant.

Discovery/refresh failures should not crash the loader — log + skip + continue,
matching the current behavior.

- [ ] **Step 5: Wire run manager**

In `backend/cubeplex/streams/run_manager.py`, inside the per-run MCP tools block:

- Build `MCPEffectiveConnectorService` for the current `(org_id, workspace_id, user_id)`.
- Build `OAuthTokenManager` via the existing
  `cubeplex.mcp.dependencies._build_token_manager_for_org` helper (confirmed to
  exist; if private, expose a thin wrapper rather than reaching into the
  underscore name).
- Pass both into `load_workspace_mcp_tools_for_cubepi`.

Remove any references to old install/server tables from this block.

- [ ] **Step 6: Run effective and runtime tests**

Run:

```bash
cd backend
uv run pytest -q tests/unit/test_mcp_effective_state.py \
                 tests/unit/test_mcp_effective_service.py \
                 tests/unit/test_mcp_cubepi_runtime.py
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/mcp/effective.py \
        backend/cubeplex/mcp/cubepi_discovery.py \
        backend/cubeplex/mcp/cubepi_runtime.py \
        backend/cubeplex/streams/run_manager.py \
        backend/tests/unit/test_mcp_effective_state.py \
        backend/tests/unit/test_mcp_effective_service.py \
        backend/tests/unit/test_mcp_cubepi_runtime.py
git commit -m "feat(mcp): derive runtime connectors from effective state"
```

---

## Task 6: Backend E2E Coverage

**Files:**

- Create: `backend/tests/e2e/test_mcp_four_layer_routes.py`
- Create: `backend/tests/e2e/test_mcp_four_layer_runtime.py`
- Modify: `backend/tests/e2e/conftest.py`

**Goal:** every numbered flow in spec §Testing Strategy lands as an E2E test.

- [ ] **Step 1: Add fixtures**

In `backend/tests/e2e/conftest.py`, add the fixtures the tests below need.
Reuse the existing E2E plumbing (the auth/client/workspace fixtures already in this
file — do not invent new auth flow). Required new fixtures:

- `noauth_template_id` — seeds a `slug="noauth-e2e"` template with
  `supported_auth_methods=["none"]`, `default_credential_policy="none"`.
- `static_template_id` — seeds a `slug="static-e2e"` template with
  `supported_auth_methods=["static"]`, `default_credential_policy="user"`.
- `oauth_template_id` — seeds a template with `supported_auth_methods=["oauth"]`
  and minimal OAuth metadata. Used by the OAuth-refresh test.
- `admin_client` and `member_client` — workspace clients whose membership role
  is `admin` and `member` respectively, sharing the same workspace. Used by the
  user-isolation test (one as admin doing the install, the other as a second
  user member). If a two-user fixture pattern doesn't yet exist in the file,
  add the minimum scaffolding (register second user, accept invite) reusing the
  existing helpers.

- [ ] **Step 2a: Workspace-local no-auth happy path (spec test #2)**

`test_workspace_local_noauth_install_renders_usable` in
`backend/tests/e2e/test_mcp_four_layer_routes.py`:

- Workspace admin POSTs `/ws/{ws}/mcp/installs` with the no-auth template id.
- GET `/ws/{ws}/mcp/connectors` returns a row whose `install_scope="workspace"`,
  `enabled=true`, `credential_policy="none"`,
  `credential_availability="not_required"`, `usable=true`.
- Runtime smoke (in `test_mcp_four_layer_runtime.py`): `list_runtime_specs(...)`
  returns the connector and no grant lookup is performed.

- [ ] **Step 2b: Org admin no-auth install + distribution (spec test #1)**

`test_org_admin_noauth_install_distributed_to_workspace_renders_usable` in
the same file. This is the dedicated org-path test — without it, an
implementation could pass Step 2a but break the `install_scope="org"` +
`WorkspaceConnectorState` codepath:

- Org admin POSTs `/admin/mcp/installs` with `template_id=noauth_template_id`,
  `install_scope="org"`, `auth_method="none"`, and
  `auto_enable={"mode":"selected","workspace_ids":[workspace_id]}`.
- Assert the install row is `install_scope="org"`, `workspace_id IS NULL`,
  `auth_status="not_required"`.
- Assert a `WorkspaceConnectorState` row was created for the target workspace
  with `enablement_source="admin_manual"` and `enabled=true`.
- GET `/ws/{ws}/mcp/connectors` returns the same install with
  `install_scope="org"`, `credential_availability="not_required"`,
  `usable=true`. A non-targeted workspace in the same org must NOT see this
  install as enabled.
- Runtime smoke: `list_runtime_specs(ws, user)` for the targeted workspace
  returns the connector; for a non-targeted workspace it does not.

- [ ] **Step 3: User-policy scope isolation (spec test #3)**

Two assertions in one test (reason strings must be the spec's scope-specific
values, not the generic `credential_missing` — collapsing reasons here would
let the implementation pass while regressing the diagnostic matrix Task 5
protects):

1. Org admin installs static template with `credential_policy="org"` + org-grant
   plaintext. Workspace admin (`admin_client`) flips state to
   `credential_policy="user"`. Without any user grant, both users see
   `credential_availability="missing"`, `usable=false`, and
   `reason="user_needs_connection"`. The pre-existing org grant must **not**
   satisfy a user-policy row.
2. User A (`admin_client`'s user) POSTs `/grants/me` with their token. User A
   then sees `usable=true`, `reason="usable"`; user B (`member_client`) still
   sees `credential_availability="missing"`, `usable=false`,
   `reason="user_needs_connection"` for the same install.

- [ ] **Step 4: Policy change drops the previous scope's grant from runtime
  (spec test #4)**

- Org install with `credential_policy="org"` and a valid org grant.
- Workspace flips `credential_policy` to `"workspace"` via PATCH state.
- GET `/connectors` shows `credential_source` is no longer `org`; without a
  workspace grant, `usable=false`, `reason="missing_workspace_grant"`.

- [ ] **Step 5: Disconnect keeps install/state (spec test #5)**

- After Step 4's setup, DELETE the org grant.
- Install row still exists with `install_state="active"`; `WorkspaceConnectorState`
  row still has `enabled=true`. Effective state for that workspace returns
  `usable=false`, `reason="missing_org_grant"` (assuming the original setup was
  org-policy; replace with `missing_workspace_grant` / `user_needs_connection`
  if the test variant flipped policy first). Re-POSTing a grant immediately
  flips back to usable without re-install.

- [ ] **Step 6: Uninstall then reinstall same template (spec test #6)**

- DELETE the install. Verify `install_state="uninstalled"`,
  `WorkspaceConnectorState` rows are no longer returned by `GET /connectors`.
- POST a new install from the same template — should succeed (partial unique
  index ignores uninstalled rows).

- [ ] **Step 7: OAuth refresh before runtime returns usable (spec test #7)**

In `backend/tests/e2e/test_mcp_four_layer_runtime.py`, stub `OAuthTokenManager`'s
HTTP refresh call (the existing OAuth tests already monkeypatch the provider
endpoint — reuse that fixture). Insert a user grant with `expires_at` in the
past and a fake refresh-credential. `list_runtime_specs(...)` should:

1. Trigger a refresh call (assert the mock got hit exactly once).
2. Return the connector as usable with the freshly-rotated `credential_id`
   stored on the grant row.
3. Leave `grant_status="valid"`.

- [ ] **Step 8: Invalid credential policy is rejected at API boundary
  (spec test #8)**

POST `/installs` with `credential_policy="bogus"` returns 422 with a field
error pointing at `credential_policy`; no rows are written.

- [ ] **Step 9: Run backend E2E tests**

```bash
cd backend
uv run pytest -q tests/e2e/test_mcp_four_layer_routes.py \
                 tests/e2e/test_mcp_four_layer_runtime.py
```

Expected: all tests pass.

- [ ] **Step 10: Commit**

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

export async function wsPatchConnectorState(
  client: ApiClient,
  wsId: string,
  installId: string,
  body: Partial<MCPWorkspaceConnectorState>,
) {
  const res = await client.patch(
    `/api/v1/ws/${wsId}/mcp/connectors/${installId}/state`,
    body,
  )
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
pnpm --filter @cubeplex/core test -- mcp.test.ts
pnpm --filter @cubeplex/core type-check
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

- [ ] **Step 3: Replace message keys (both locales together — i18n parity check is in CI)**

Add the new keys (English / 简体中文) to `frontend/packages/web/messages/en.json` and
`frontend/packages/web/messages/zh.json` under `mcp`:

| Key | en | zh |
| --- | --- | --- |
| `templates` | Connector templates | 连接器模板 |
| `installs` | Connector installs | 连接器安装 |
| `workspaceState` | Workspace state | 工作区状态 |
| `credentialPolicy` | Credential policy | 凭证策略 |
| `orgGrant` | Org grant | 组织授权 |
| `workspaceGrant` | Workspace grant | 工作区授权 |
| `myGrant` | My grant | 我的授权 |
| `needsCredential` | Needs your credential | 需要你的凭证 |
| `ready` | Ready | 可用 |

In the same edit, **remove every key whose path starts with `mcpCatalog.*` or
`mcpOverride*`** from both files — the i18n parity check (added in commit
`884920a0`) will fail CI if the two locales diverge. After editing, run:

```bash
cd frontend
pnpm --filter @cubeplex/web i18n:check  # or whatever the parity script is named
```

to confirm no orphan keys remain.

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
pnpm --filter @cubeplex/web type-check
pnpm --filter @cubeplex/web test:e2e -- mcp/ws-mcp.spec.ts mcp/admin-mcp.spec.ts
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
  backend/cubeplex frontend/packages/core frontend/packages/web \
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
pnpm --filter @cubeplex/core type-check
pnpm --filter @cubeplex/web type-check
pnpm --filter @cubeplex/web test:e2e -- mcp/ws-mcp.spec.ts mcp/admin-mcp.spec.ts
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
git add backend/cubeplex frontend/packages
git commit -m "chore(mcp): remove old catalog and override surfaces"
```
