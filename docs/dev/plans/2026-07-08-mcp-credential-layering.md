# MCP Credential Layering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current "org install vs workspace install" conflict with a new `mcp_connectors` identity table and layered org/workspace/user credentials.

**Architecture:** Adopt Option A from the spec: create `mcp_connectors`, migrate/deprecate `mcp_connector_installs`, and backfill old installs into connector identity plus workspace state and credential grants. Workspace credentials are preserved as overrides; org admin can add org credentials and recommend org use, but the first version does not include a force-org-credential action.

**Tech Stack:** FastAPI, SQLModel, Alembic, Postgres, pytest, Next.js, React 19, TypeScript, Vitest, `@cubebox/core`.

## Global Constraints

- Do not ship the current template-hiding workaround as final behavior.
- Org admin adding a connector must not return `409 install_already_exists` only because a workspace already uses its own credential.
- Workspace users/admins must always be able to use workspace/user credentials instead of org credentials when policy allows it.
- No force-org-credential behavior in this implementation.
- Connector identity remains unique per org by template / URL / namespace.
- Credential grants can coexist at org, workspace, and user scope for the same connector identity.
- Runtime credential selection must follow workspace state and actor context, never row ordering.
- New backend tests that hit the app or DB belong in `backend/tests/e2e/`.
- Docs for changed user-facing MCP behavior must update `docs/site/docs/` in the same PR.
- Use `alembic revision --autogenerate -m "..."` for schema migrations; do not hand-write migration files except for required data migration adjustments after autogenerate.

---

## Definition: Legacy Backfill

Legacy backfill means one-time data migration, not a long-lived runtime service.

When code finds an active workspace-scope install for a connector identity:

1. It ensures a single `mcp_connectors` row exists for the same org + template / URL / namespace.
2. It creates or updates that workspace's `MCPWorkspaceConnectorState` to point at the org-owned identity.
3. It moves credential grants from the old workspace-scope install id to the connector id.
4. It tombstones the old workspace-scope install row.
5. It preserves the workspace's effective behavior: a workspace using its own credential before migration still uses its own credential after migration.

It does not mean:

- choosing one workspace credential as the canonical org credential
- replacing workspace credentials with org credentials
- disabling workspace overrides
- forcing all workspaces to use org credentials

---

## File Structure

### Backend

- Modify: `backend/cubebox/models/mcp.py`
  - Add `MCPConnector`.
  - Update `MCPWorkspaceConnectorState` and `MCPCredentialGrant` to reference `mcp_connectors.id`.
  - Keep `MCPConnectorInstall` only as legacy migration/compatibility data.

- Modify: `backend/cubebox/services/mcp_installs.py`
  - Update org create and workspace create flows.
  - Remove cross-scope credential-layering 409s.

- Modify: `backend/cubebox/repositories/mcp.py`
  - Add `MCPConnectorRepository`.
  - Add list/query helpers for active connector identity conflicts and workspace-scope legacy installs.

- Modify: `backend/cubebox/mcp/effective.py`
  - Resolve connector availability from org-owned identity + workspace state.
  - Stop treating workspace-scope installs as independent runtime connector identities after migration.

- Modify: `backend/cubebox/streams/run_manager.py`
  - Keep runtime credential lookup aligned with `effective.py` if it has direct MCP credential resolution paths.

- Modify: `backend/cubebox/api/routes/v1/admin_mcp.py`
  - Admin add connector uses `mcp_connectors` instead of returning 409 for workspace credential layering.
  - Admin templates/catalog responses surface "used in workspaces" state without hiding templates.

- Modify: `backend/cubebox/api/routes/v1/ws_mcp.py`
  - Workspace connect/enable creates workspace state and grants against connector identity.
  - It creates org-owned identity lazily if none exists.

- Modify: `backend/cubebox/api/schemas/mcp.py`
  - Add response fields that communicate `connector_id`, credential source, and legacy install compatibility fields cleanly.

- Test: `backend/tests/e2e/test_mcp_credential_layering.py`
  - End-to-end API and runtime behavior for org/workspace/user credential layering.

### Frontend Core

- Modify: `frontend/packages/core/src/types/mcp.ts`
  - Add typed fields for connector identity, credential source, and catalog/admin state.

- Modify: `frontend/packages/core/src/api/mcp.ts`
  - Keep route helpers but align request/response types with new semantics.

- Test: `frontend/packages/core/__tests__/api/mcp.test.ts`
  - Ensure route helpers still target the existing API paths and parse the new fields.

### Frontend Web

- Modify: `frontend/packages/web/app/admin/mcp/page.tsx`
  - Show templates that are used in workspaces.
  - Do not hide templates because of workspace credential usage.

- Modify: `frontend/packages/web/components/mcp/MCPTemplateInstallPanel.tsx`
  - Copy/action changes from "Install duplicate connector" to "Add to organization".
  - Show existing workspace usage and explain that workspace credentials remain overrides.

- Modify: `frontend/packages/web/components/workspace-settings/McpPanel.tsx`
  - Make credential source explicit.
  - Allow workspace admin to switch between org/workspace/user credential policies.

- Modify: `frontend/packages/web/components/mcp/WsAuthBand.tsx`
  - Show the correct auth action for the effective credential policy.

- Modify: `frontend/packages/web/components/mcp/AdminAuthBand.tsx`
  - Only manages org credential grants; does not imply workspace overrides are removed.

- Modify: `frontend/packages/web/messages/en.json`
- Modify: `frontend/packages/web/messages/zh.json`
  - Add clear copy for "Add to organization", "Use organization credential", "Use workspace credential", and "Connect my account".

- Test: `frontend/packages/web/__tests__/components/mcpCredentialLayering.test.tsx`
  - Admin template visibility and workspace credential source UI behavior.

### Docs

- Modify: `docs/site/docs/admin/mcp-connectors.md`
  - Explain org connector provisioning and workspace credential overrides.

- Modify: `docs/site/docs/guides/mcp/installing-connectors.md`
  - Rename user-facing concepts from generic install to add/enable/connect where needed.

- Modify: `docs/site/docs/guides/mcp/overview.md`
  - Explain credential source layering.

---

## Task 1: Revert the Template-Hiding Workaround and Lock the Intended UI State

**Files:**

- Modify: `frontend/packages/web/app/admin/mcp/page.tsx`
- Delete or rewrite: `frontend/packages/web/components/mcp/adminTemplateAvailability.ts`
- Modify: `frontend/packages/web/__tests__/components/mcpAdminTemplates.test.ts`

**Interfaces:**

- Consumes: `MCPConnectorTemplate.install_summary?: Record<string, unknown> | null`
- Produces: frontend behavior where templates remain visible even when `install_summary.active_workspace_install_count > 0`

- [ ] **Step 1: Rewrite the failing frontend test**

Replace the current hide-conflict expectation with:

```ts
it('keeps templates visible when workspaces already use their own credentials', () => {
  const workspaceUsed = template({
    template_id: 'mcptpl_atlassian',
    install_summary: {
      active_conflict_count: 1,
      active_workspace_install_count: 1,
      active_org_install_count: 0,
    },
  })

  expect(filterAdminCatalogTemplates([workspaceUsed], [])).toEqual([workspaceUsed])
})
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
cd frontend
pnpm --filter web test __tests__/components/mcpAdminTemplates.test.ts
```

Expected: FAIL because the current helper hides templates with active conflicts.

- [ ] **Step 3: Implement minimal frontend change**

Replace the helper with a non-hiding helper:

```ts
export function filterAdminCatalogTemplates(
  templates: MCPConnectorTemplate[],
  _connectors: AdminOrgConnector[],
): MCPConnectorTemplate[] {
  return templates
}
```

Then update `frontend/packages/web/app/admin/mcp/page.tsx` to use this helper or remove filtering entirely.

- [ ] **Step 4: Run frontend target test**

Run:

```bash
cd frontend
pnpm --filter web test __tests__/components/mcpAdminTemplates.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/app/admin/mcp/page.tsx \
  frontend/packages/web/components/mcp/adminTemplateAvailability.ts \
  frontend/packages/web/__tests__/components/mcpAdminTemplates.test.ts
git commit -m "Keep MCP templates visible with workspace credentials"
```

---

## Task 2: Add `mcp_connectors` Identity Table and Repository

**Files:**

- Modify: `backend/cubebox/models/mcp.py`
- Modify: `backend/cubebox/repositories/mcp.py`
- Create: generated Alembic migration under `backend/alembic/versions/`
- Test: `backend/tests/unit/test_mcp_connector_repository.py`

**Interfaces:**

- Produces:

```python
class MCPConnector(SQLModel, table=True):
    id: str
    org_id: str
    template_id: str | None
    name: str
    slug_name: str
    server_url: str
    server_url_hash: str
    transport: str
    auth_method: str
    oauth_client_config: dict[str, Any]
    static_auth_style: str
    static_auth_header_name: str | None
    static_auth_query_param: str | None
    tools_cache: list[dict[str, Any]]
    tool_citations: dict[str, Any]
    discovery_status: str
    last_error: str | None
    status: str
```

```python
class MCPConnectorRepository:
    async def get(self, connector_id: str) -> MCPConnector | None:
        """Return the connector in this repository's org, or None."""

    async def get_active_by_identity(
        self,
        *,
        template_id: str | None,
        server_url_hash: str,
        slug_name: str,
    ) -> MCPConnector | None:
        """Return active connector matching template id, URL hash, or slug."""
```

- [ ] **Step 1: Write failing repository test**

Create `backend/tests/unit/test_mcp_connector_repository.py`:

```python
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.mcp._constants import server_url_hash, slugify_for_namespace
from cubebox.models import MCPConnector
from cubebox.repositories.mcp import MCPConnectorRepository


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_get_active_by_identity_matches_template_url_or_slug(
    session: AsyncSession,
) -> None:
    url = 'https://mcp.example.com'
    connector = MCPConnector(
        org_id='org-1',
        template_id='mcptpl-1',
        name='Example MCP',
        server_url=url,
        server_url_hash=server_url_hash(url),
        transport='streamable_http',
        auth_method='oauth',
        status='active',
    )
    session.add(connector)
    await session.commit()

    repo = MCPConnectorRepository(session, org_id='org-1')
    found = await repo.get_active_by_identity(
        template_id='mcptpl-1',
        server_url_hash='different',
        slug_name=slugify_for_namespace('different'),
    )

    assert found is not None
    assert found.id == connector.id
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend
uv run pytest tests/unit/test_mcp_connector_repository.py --no-cov
```

Expected: FAIL because `MCPConnector` / `MCPConnectorRepository` do not exist.

- [ ] **Step 3: Add model and repository**

Add `MCPConnector` to `backend/cubebox/models/mcp.py` with prefix `mcpco` in
`backend/cubebox/models/public_id.py`. Add partial unique indexes for active
rows:

- `(org_id, template_id)` where `status='active' AND template_id IS NOT NULL`
- `(org_id, server_url_hash)` where `status='active'`
- `(org_id, slug_name)` where `status='active'`

Add `MCPConnectorRepository` to `backend/cubebox/repositories/mcp.py`.

- [ ] **Step 4: Generate migration**

Run:

```bash
cd backend
alembic revision --autogenerate -m "add mcp connectors"
```

Expected: migration creates `mcp_connectors`, adds `connector_id` columns to
`mcp_workspace_connector_states` and `mcp_credential_grants`, and creates the
new indexes. Existing `install_id` columns remain during compatibility.

- [ ] **Step 5: Run repository test**

Run:

```bash
cd backend
uv run pytest tests/unit/test_mcp_connector_repository.py --no-cov
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/models/mcp.py \
  backend/cubebox/models/public_id.py \
  backend/cubebox/repositories/mcp.py \
  backend/alembic/versions \
  backend/tests/unit/test_mcp_connector_repository.py
git commit -m "Add MCP connector identity table"
```

---

## Task 3: Migrate Legacy Installs Into `mcp_connectors`

**Files:**

- Modify: `backend/cubebox/repositories/mcp.py`
- Create: generated Alembic migration under `backend/alembic/versions/`
- Test: `backend/tests/e2e/test_mcp_credential_layering.py`

**Interfaces:**

- Consumes the `MCPConnector` model and `MCPConnectorRepository` from Task 2.
- Produces a migrated database where:
  - every active legacy install has a corresponding `mcp_connectors` row
  - workspace state rows point at `connector_id`
  - credential grant rows point at `connector_id`
  - old workspace-scope install rows are tombstoned after their state/grants are migrated
  - workspace effective behavior is preserved

- [ ] **Step 1: Write migration regression test**

Create `backend/tests/e2e/test_mcp_credential_layering.py` with a helper that
seeds the legacy shape directly, then asserts the post-migration shape after
the test database is upgraded:

```python
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.mcp._constants import server_url_hash
from cubebox.models import (
    MCPConnector,
    MCPConnectorInstall,
    MCPCredentialGrant,
    MCPWorkspaceConnectorState,
)

pytestmark = pytest.mark.usefixtures('stub_discover_tools')


async def test_legacy_workspace_install_is_backfilled_to_connector_identity(
    db_session: AsyncSession,
    static_template_id: str,
) -> None:
    legacy = MCPConnectorInstall(
        org_id='org-test',
        workspace_id='ws-test',
        install_scope='workspace',
        template_id=static_template_id,
        name='Static Test',
        server_url='https://static.example.com/mcp',
        server_url_hash=server_url_hash('https://static.example.com/mcp'),
        transport='streamable_http',
        auth_method='static',
        default_credential_policy='workspace',
        auth_status='connected',
        install_state='active',
    )
    db_session.add(legacy)
    await db_session.flush()
    db_session.add(
        MCPCredentialGrant(
            org_id='org-test',
            install_id=legacy.id,
            grant_scope='workspace',
            workspace_id='ws-test',
            credential_id='cred-test',
            grant_status='valid',
        )
    )
    await db_session.commit()

    # The migration/backfill is run by alembic before this assertion in the
    # migrated test database. The old install remains for audit but no longer
    # owns runtime state.
    connector = (
        await db_session.execute(
            select(MCPConnector).where(MCPConnector.template_id == static_template_id)
        )
    ).scalar_one()
    state = (
        await db_session.execute(
            select(MCPWorkspaceConnectorState).where(
                MCPWorkspaceConnectorState.connector_id == connector.id
            )
        )
    ).scalar_one()
    grant = (
        await db_session.execute(
            select(MCPCredentialGrant).where(MCPCredentialGrant.connector_id == connector.id)
        )
    ).scalar_one()

    assert state.workspace_id == 'ws-test'
    assert state.credential_policy == 'workspace'
    assert grant.grant_scope == 'workspace'
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend
uv run pytest tests/e2e/test_mcp_credential_layering.py -k legacy_workspace_install_is_backfilled --no-cov
```

Expected: FAIL because `MCPConnector`/`connector_id` fields and migration do not exist yet.

- [ ] **Step 3: Generate migration**

Run:

```bash
cd backend
alembic revision --autogenerate -m "migrate mcp installs to connectors"
```

Expected: new migration file is created.

- [ ] **Step 4: Add data migration logic**

In the generated migration, add SQL or Python migration steps that:

1. Create one `mcp_connectors` row per org + active template / URL / slug identity.
2. Copy identity fields, auth method, OAuth client config, static auth metadata,
   tools cache, citations, discovery status, and last error from legacy installs.
3. Fill `connector_id` on `mcp_workspace_connector_states`.
4. Fill `connector_id` on `mcp_credential_grants`.
5. Convert active workspace-scope installs into workspace state rows when no
   state row exists yet.
6. Preserve the old workspace install's `default_credential_policy` in the
   workspace state row.
7. Set migrated workspace-scope installs to `install_state='uninstalled'`.
8. Keep `mcp_connector_installs` rows for rollback/audit; do not use them as
   the source of truth after this task.

- [ ] **Step 5: Run e2e test**

Run:

```bash
cd backend
alembic upgrade head
uv run pytest tests/e2e/test_mcp_credential_layering.py -k legacy_workspace_install_is_backfilled --no-cov
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions \
  backend/cubebox/repositories/mcp.py \
  backend/tests/e2e/test_mcp_credential_layering.py
git commit -m "Migrate MCP installs into connector identity"
```

---

## Task 4: Update Admin Add Connector Flow to Use `mcp_connectors`

**Files:**

- Modify: `backend/cubebox/services/mcp_installs.py`
- Modify: `backend/cubebox/api/routes/v1/admin_mcp.py`
- Test: `backend/tests/e2e/test_mcp_credential_layering.py`

**Interfaces:**

- Consumes: `MCPConnectorRepository.get_active_by_identity(template_id, server_url_hash, slug_name)`
- Produces: admin add connector flow succeeds when matching workspace installs exist and returns `connector_id`.

- [ ] **Step 1: Add failing e2e test for direct org add**

In `backend/tests/e2e/test_mcp_credential_layering.py` add:

```python
async def test_org_add_does_not_409_when_workspace_install_exists(
    admin_client: tuple[httpx.AsyncClient, str],
    noauth_template_id: str,
) -> None:
    client, ws_id = admin_client
    ws_install = await client.post(
        f'/api/v1/ws/{ws_id}/mcp/installs',
        json={
            'template_id': noauth_template_id,
            'install_scope': 'workspace',
            'auth_method': 'none',
            'default_credential_policy': 'none',
        },
    )
    assert ws_install.status_code == 201, ws_install.text

    org_add = await client.post(
        '/api/v1/admin/mcp/installs',
        json={
            'template_id': noauth_template_id,
            'install_scope': 'org',
            'auth_method': 'none',
            'default_credential_policy': 'none',
            'auto_enable': {'mode': 'none'},
        },
    )

    assert org_add.status_code == 201, org_add.text
    assert org_add.json()['connector_id'].startswith('mcpco')
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend
uv run pytest tests/e2e/test_mcp_credential_layering.py -k does_not_409 --no-cov
```

Expected: FAIL with 409.

- [ ] **Step 3: Change service conflict handling**

In the admin add connector service path, replace the cross-scope conflict
preflight with:

1. Find or create an active `mcp_connectors` identity with same template / URL /
   slug.
2. Reject only a true duplicate custom identity that cannot map to the same
   connector.
3. Do not create or update legacy `mcp_connector_installs` for new writes.

Keep duplicate active connector identity rejected at `mcp_connectors` unique
indexes.

- [ ] **Step 4: Run target e2e**

Run:

```bash
cd backend
uv run pytest tests/e2e/test_mcp_credential_layering.py -k "does_not_409 or org_add_preserves_existing_workspace_credential" --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/services/mcp_installs.py backend/cubebox/api/routes/v1/admin_mcp.py backend/tests/e2e/test_mcp_credential_layering.py
git commit -m "Allow org MCP add with workspace credential overrides"
```

---

## Task 5: Update Workspace Enable Flow to Use `mcp_connectors`

**Files:**

- Modify: `backend/cubebox/services/mcp_installs.py`
- Modify: `backend/cubebox/api/routes/v1/ws_mcp.py`
- Test: `backend/tests/e2e/test_mcp_credential_layering.py`

**Interfaces:**

- Produces: workspace endpoint creates state/grant against existing org-owned connector identity.

- [ ] **Step 1: Add failing e2e test**

```python
async def test_workspace_enable_reuses_existing_org_connector_identity(
    admin_client: tuple[httpx.AsyncClient, str],
    static_template_id: str,
) -> None:
    client, ws_id = admin_client
    org_add = await client.post(
        '/api/v1/admin/mcp/installs',
        json={
            'template_id': static_template_id,
            'install_scope': 'org',
            'auth_method': 'static',
            'default_credential_policy': 'org',
            'auto_enable': {'mode': 'none'},
        },
    )
    assert org_add.status_code == 201, org_add.text
    connector_id = org_add.json()['connector_id']

    ws_add = await client.post(
        f'/api/v1/ws/{ws_id}/mcp/installs',
        json={
            'template_id': static_template_id,
            'install_scope': 'workspace',
            'auth_method': 'static',
            'default_credential_policy': 'workspace',
        },
    )
    assert ws_add.status_code == 200, ws_add.text
    assert ws_add.json()['connector_id'] == connector_id
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend
uv run pytest tests/e2e/test_mcp_credential_layering.py -k workspace_enable_reuses --no-cov
```

Expected: FAIL because current workspace create attempts a second install or returns 409.

- [ ] **Step 3: Implement service path**

Change workspace template create behavior:

1. Look for existing active org connector identity for template / URL / slug.
2. If found, upsert workspace state with requested credential policy.
3. Return the existing connector identity as the install response.
4. If not found, create org-owned connector identity lazily, then state row.

Do not create an active workspace-scope install row.

- [ ] **Step 4: Run target tests**

Run:

```bash
cd backend
uv run pytest tests/e2e/test_mcp_credential_layering.py -k "workspace_enable_reuses or org_add_preserves" --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/services/mcp_installs.py backend/cubebox/api/routes/v1/ws_mcp.py backend/tests/e2e/test_mcp_credential_layering.py
git commit -m "Reuse MCP connector identity from workspace enablement"
```

---

## Task 6: Runtime Credential Resolution Uses Workspace State

**Files:**

- Modify: `backend/cubebox/mcp/effective.py`
- Modify: `backend/cubebox/streams/run_manager.py`
- Test: `backend/tests/e2e/test_mcp_credential_layering.py`

**Interfaces:**

- Produces runtime behavior:
  - `credential_policy='workspace'` uses workspace grant.
  - `credential_policy='org'` uses org grant.
  - `credential_policy='user'` requires actor user grant.

- [ ] **Step 1: Add failing e2e tests**

Add:

```python
async def test_workspace_policy_uses_workspace_grant_even_when_org_grant_exists(
    admin_client: tuple[httpx.AsyncClient, str],
    static_template_id: str,
) -> None:
    client, ws_id = admin_client
    # Seed org connector + org grant, then set workspace policy to workspace.
    # Assert connector row reports credential_source == 'workspace'.


async def test_org_policy_uses_org_grant_when_workspace_has_no_override(
    admin_client: tuple[httpx.AsyncClient, str],
    static_template_id: str,
) -> None:
    client, ws_id = admin_client
    # Seed org connector + org grant and enable workspace with org policy.
    # Assert connector row reports credential_source == 'org'.


async def test_user_policy_does_not_fallback_to_org_or_workspace_grant(
    four_layer_admin_and_member: tuple[
        tuple[httpx.AsyncClient, str, str],
        tuple[httpx.AsyncClient, str, str],
    ],
    static_template_id: str,
) -> None:
    (admin_c, workspace_id, _admin_uid), (member_c, _ws_b, _member_uid) = (
        four_layer_admin_and_member
    )
    # Seed org and workspace grants, then switch workspace policy to user.
    # Assert member sees credential_availability == 'missing' and
    # reason == 'user_needs_connection'.
```

Each test should call `/api/v1/ws/{ws_id}/mcp/connectors` first and assert
`credential_source`, then invoke one MCP tool through the workspace invoke
endpoint if available in existing tests.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd backend
uv run pytest tests/e2e/test_mcp_credential_layering.py -k "workspace_policy or org_policy or user_policy" --no-cov
```

Expected: FAIL on at least one credential source assertion.

- [ ] **Step 3: Update effective-state derivation**

In `backend/cubebox/mcp/effective.py`, derive effective connector rows from:

1. active org-owned connector identities
2. workspace state rows
3. matching grant for selected policy

Do not include tombstoned legacy workspace-scope installs in runtime-visible rows.

- [ ] **Step 4: Update direct runtime credential lookups**

Search:

```bash
rg -n "credential_policy|MCPCredentialGrant|workspace_id" backend/cubebox/streams backend/cubebox/mcp
```

Update direct lookups in `run_manager.py` or related runtime code to use the
same selected policy rules as `effective.py`.

- [ ] **Step 5: Run target e2e tests**

Run:

```bash
cd backend
uv run pytest tests/e2e/test_mcp_credential_layering.py --no-cov
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/mcp/effective.py backend/cubebox/streams/run_manager.py backend/tests/e2e/test_mcp_credential_layering.py
git commit -m "Resolve MCP credentials from workspace state"
```

---

## Task 7: Admin UI Copy and Flow

**Files:**

- Modify: `frontend/packages/web/components/mcp/MCPTemplateInstallPanel.tsx`
- Modify: `frontend/packages/web/components/mcp/MCPAdminDetailPanel.tsx`
- Modify: `frontend/packages/web/messages/en.json`
- Modify: `frontend/packages/web/messages/zh.json`
- Test: `frontend/packages/web/__tests__/components/mcpCredentialLayering.test.tsx`

**Interfaces:**

- Consumes core template/install response fields from backend.
- Produces admin UI that says "Add to organization" and explains workspace credentials remain usable.

- [ ] **Step 1: Write failing frontend test**

Create `frontend/packages/web/__tests__/components/mcpCredentialLayering.test.tsx` asserting:

```ts
expect(screen.getByRole('button', { name: 'Add to organization' })).toBeInTheDocument()
expect(screen.getByText(/Workspace credentials remain available/i)).toBeInTheDocument()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd frontend
pnpm --filter web test __tests__/components/mcpCredentialLayering.test.tsx
```

Expected: FAIL because current copy says "Install".

- [ ] **Step 3: Update copy and panel**

Change admin template panel copy:

- `Install` -> `Add to organization`
- `Workspace rollout` remains, but add text: "Existing workspace credentials are preserved."
- Remove any copy implying workspace installs must be removed first.

- [ ] **Step 4: Run frontend test**

Run:

```bash
cd frontend
pnpm --filter web test __tests__/components/mcpCredentialLayering.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/components/mcp/MCPTemplateInstallPanel.tsx \
  frontend/packages/web/components/mcp/MCPAdminDetailPanel.tsx \
  frontend/packages/web/messages/en.json \
  frontend/packages/web/messages/zh.json \
  frontend/packages/web/__tests__/components/mcpCredentialLayering.test.tsx
git commit -m "Clarify org MCP connector provisioning UI"
```

---

## Task 8: Workspace UI Credential Source Selection

**Files:**

- Modify: `frontend/packages/web/components/workspace-settings/McpPanel.tsx`
- Modify: `frontend/packages/web/components/mcp/WsAuthBand.tsx`
- Modify: `frontend/packages/web/messages/en.json`
- Modify: `frontend/packages/web/messages/zh.json`
- Test: `frontend/packages/web/__tests__/components/McpPanel.test.tsx`

**Interfaces:**

- Consumes: `MCPEffectiveConnector.credential_policy`, `credential_source`, `credential_availability`
- Produces: workspace UI where credential source is explicit and switchable.

- [ ] **Step 1: Add failing frontend test**

Extend `McpPanel.test.tsx`:

```ts
it('shows credential source choices for a connector available from org', async () => {
  renderWithIntl(<McpPanel wsId="ws_1" />)
  fireEvent.click(await screen.findByTestId('ws-connector-row-mcins_atlassian'))
  expect(screen.getByRole('button', { name: 'Use organization credential' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Use workspace credential' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Connect my account' })).toBeInTheDocument()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd frontend
pnpm --filter web test __tests__/components/McpPanel.test.tsx
```

Expected: FAIL because current buttons are raw `org/workspace/user`.

- [ ] **Step 3: Update UI copy and actions**

Replace raw policy buttons with product labels:

- `org` -> `Use organization credential`
- `workspace` -> `Use workspace credential`
- `user` -> `Connect my account`
- `none` -> `No credential required`

Keep existing `wsPatchConnectorState` calls.

- [ ] **Step 4: Run test**

Run:

```bash
cd frontend
pnpm --filter web test __tests__/components/McpPanel.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/components/workspace-settings/McpPanel.tsx \
  frontend/packages/web/components/mcp/WsAuthBand.tsx \
  frontend/packages/web/messages/en.json \
  frontend/packages/web/messages/zh.json \
  frontend/packages/web/__tests__/components/McpPanel.test.tsx
git commit -m "Show MCP workspace credential source choices"
```

---

## Task 9: User-Facing Docs

**Files:**

- Modify: `docs/site/docs/admin/mcp-connectors.md`
- Modify: `docs/site/docs/guides/mcp/installing-connectors.md`
- Modify: `docs/site/docs/guides/mcp/overview.md`

**Interfaces:**

- Consumes final product vocabulary and behavior from Tasks 4, 8, and 9.
- Produces docs that describe credential layering accurately.

- [ ] **Step 1: Update admin docs**

In `docs/site/docs/admin/mcp-connectors.md`, replace "workspace install blocks org install" language with:

```md
Workspace credentials do not block org-level connector setup. If a workspace already uses a connector with its own credential, adding the connector to the organization preserves that workspace's credential choice. Org rollout applies to workspaces that do not already have an explicit workspace or user credential policy.
```

- [ ] **Step 2: Update user guide**

In `docs/site/docs/guides/mcp/installing-connectors.md`, explain:

- org admins add connectors to the organization
- workspace admins enable connectors and choose credential source
- users connect their own account when policy is user

- [ ] **Step 3: Update overview**

In `docs/site/docs/guides/mcp/overview.md`, add a short "Credential sources"
section with org/workspace/user/none definitions.

- [ ] **Step 4: Commit**

```bash
git add docs/site/docs/admin/mcp-connectors.md \
  docs/site/docs/guides/mcp/installing-connectors.md \
  docs/site/docs/guides/mcp/overview.md
git commit -m "Document MCP credential layering"
```

---

## Task 10: Final Verification Sweep

**Files:**

- No code changes unless verification reveals a defect.

**Interfaces:**

- Consumes all prior tasks.
- Produces final evidence before PR.

- [ ] **Step 1: Run backend targeted tests**

Run:

```bash
cd backend
uv run pytest tests/e2e/test_mcp_credential_layering.py --no-cov 2>&1 | tee tmp/mcp-credential-layering-e2e.log | tail -20
```

Expected: all tests pass.

- [ ] **Step 2: Run backend lint/type checks**

Run:

```bash
cd backend
uv run ruff check cubebox tests 2>&1 | tee tmp/mcp-credential-layering-ruff.log | tail -20
uv run mypy cubebox 2>&1 | tee tmp/mcp-credential-layering-mypy.log | tail -20
```

Expected: both pass.

- [ ] **Step 3: Run frontend targeted tests**

Run:

```bash
cd frontend
pnpm --filter web test __tests__/components/McpPanel.test.tsx __tests__/components/mcpCredentialLayering.test.tsx __tests__/components/mcpAdminTemplates.test.ts 2>&1 | tee tmp/mcp-credential-layering-web-tests.log | tail -20
```

Expected: all tests pass.

- [ ] **Step 4: Run frontend lint/type checks**

Run:

```bash
cd frontend
pnpm --filter web type-check 2>&1 | tee tmp/mcp-credential-layering-type-check.log | tail -20
pnpm --filter web lint 2>&1 | tee tmp/mcp-credential-layering-lint.log | tail -20
```

Expected: both pass.

- [ ] **Step 5: Inspect final diff**

Run:

```bash
git status --short
git diff --stat origin/main...HEAD
```

Expected: only MCP credential-layering code, tests, migration, and docs are included.

- [ ] **Step 6: Commit any final fixes**

If verification required fixes:

```bash
git add <changed files>
git commit -m "Finalize MCP credential layering"
```

If no fixes were required, do not create an empty commit.
