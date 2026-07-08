# MCP Connector Model Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `connector_id` the primary MCP runtime identity and remove `mcp_connector_installs` from workspace state, grants, OAuth, and effective runtime resolution.

**Architecture:** Keep `mcp_connectors` as the org-owned connector identity and shared metadata table. Re-key workspace state and credential grants to `connector_id`, then move routes, services, OAuth, discovery, and runtime to connector-centric queries before dropping install-centric columns and the install table.

**Tech Stack:** FastAPI, SQLModel, SQLAlchemy async, Alembic, Postgres, pytest e2e tests, Next.js, React 19, TypeScript, pnpm.

## Scope

**PR #308 delivers Tasks 1–5 and 7** (backend connector model, migrations, service/OAuth/runtime cutover, frontend type/schema alignment, and docs). Task 6 (drop `mcp_connector_installs` table) is deferred — the install table remains as an administrative ledger while `connector_id` is the runtime identity.

## Global Constraints

- Work in a feature worktree; first command inside the worktree is `cat .worktree.env`.
- Read `backend/docs/mcp_catalog_oauth.md`, `backend/docs/quick-reference.md`, `docs/testing.md`, and `docs/worktrees.md` before implementation.
- Use `alembic revision --autogenerate -m "..."` for schema migrations.
- Do not hand-edit `pyproject.toml` or `package.json`.
- If a test opens an `AsyncSession`, runs Alembic, or hits the FastAPI app, place it under `backend/tests/e2e/`.
- Docs for user-facing MCP behavior must ship in the same PR under `docs/site/docs/`.
- Keep org-admin routes and workspace routes separate; reuse belongs in services and repositories.
- Keep the current credential layering PR focused; implement this cleanup as a follow-up PR.

---

## File Structure

- `backend/cubebox/models/mcp.py` - make workspace state and credential grants connector-centric; remove install model only after readers are cut over.
- `backend/cubebox/models/public_id.py` - keep `mcpco` public id prefix; remove install prefix only if no remaining model uses it.
- `backend/cubebox/repositories/mcp.py` - add connector-centric state and grant repository methods; remove install repository after callers are gone.
- `backend/cubebox/services/mcp_installs.py` - replace with connector-centric service or shrink to compatibility wrappers during cutover.
- `backend/cubebox/mcp/effective.py` - compute runtime availability from connector + workspace state + grant.
- `backend/cubebox/mcp/oauth/start.py` - start OAuth by connector id, target grant scope, workspace id, and user id.
- `backend/cubebox/mcp/oauth/callback.py` - write connector-scoped grants from OAuth callback.
- `backend/cubebox/mcp/discovery.py` and `backend/cubebox/services/mcp_discovery.py` - update connector metadata instead of install metadata.
- `backend/cubebox/api/routes/v1/admin_mcp.py` - expose connector-centric admin operations while preserving route isolation.
- `backend/cubebox/api/routes/v1/ws_mcp.py` - expose connector-centric workspace operations while preserving route isolation.
- `backend/cubebox/api/schemas/mcp.py` - make connector response types primary; remove install-centric response fields after route cutover.
- `frontend/packages/core/src/types/mcp.ts` - make `connector_id` required for public MCP connector objects.
- `frontend/packages/core/src/api/mcp.ts` - call connector-centric endpoints and types.
- `frontend/packages/web/app/admin/mcp/page.tsx` - use connector identity as the list/action key.
- `frontend/packages/web/app/(app)/w/[wsId]/mcp/page.tsx` - use connector identity as the list/action key.
- `docs/site/docs/admin/mcp-connectors.md` - update admin docs to describe adding organization connectors and org credentials.
- `docs/site/docs/guides/mcp/installing-connectors.md` - update workspace docs to describe enabling connectors and choosing credential source.
- `backend/tests/e2e/test_mcp_connector_model_cleanup.py` - add business-flow tests for connector-centric behavior.
- `backend/tests/e2e/test_migration.py` - add migration coverage for install-to-connector cleanup.

---

### Task 1: Add Connector-Centric Repository Tests

**Files:**
- Create: `backend/tests/e2e/test_mcp_connector_model_cleanup.py`
- Modify: `backend/cubebox/repositories/mcp.py`
- Modify: `backend/cubebox/models/mcp.py`

**Interfaces:**
- Consumes: existing `MCPConnector`, `MCPWorkspaceConnectorState`, `MCPCredentialGrant`.
- Produces:
  - `MCPWorkspaceConnectorStateRepository.get_by_connector(workspace_id: str, connector_id: str) -> MCPWorkspaceConnectorState | None`
  - `MCPWorkspaceConnectorStateRepository.upsert_for_connector(...) -> MCPWorkspaceConnectorState`
  - `MCPCredentialGrantRepository.get_org_grant_for_connector(connector_id: str) -> MCPCredentialGrant | None`
  - `MCPCredentialGrantRepository.get_workspace_grant_for_connector(connector_id: str, workspace_id: str) -> MCPCredentialGrant | None`
  - `MCPCredentialGrantRepository.get_user_grant_for_connector(connector_id: str, user_id: str, *, workspace_id: str) -> MCPCredentialGrant | None`

- [ ] **Step 1: Read required docs**

Run:

```bash
cat .worktree.env
sed -n '1,220p' docs/worktrees.md
sed -n '1,220p' docs/testing.md
sed -n '1,220p' backend/docs/mcp_catalog_oauth.md
sed -n '1,220p' backend/docs/quick-reference.md
```

Expected: commands complete and show worktree ports, test placement rules, MCP catalog/OAuth notes, and migration/public-id guidance.

- [ ] **Step 2: Write failing repository e2e tests**

Create `backend/tests/e2e/test_mcp_connector_model_cleanup.py` with tests that:

- create one connector
- upsert workspace state by `connector_id`
- create org, workspace, and user grants by `connector_id`
- assert lookup never needs `install_id`

Use existing e2e fixtures from nearby MCP tests instead of mocks. The test names must be:

```python
async def test_workspace_state_is_keyed_by_connector_id(...): ...
async def test_credential_grants_are_keyed_by_connector_id(...): ...
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```bash
cd backend
uv run pytest tests/e2e/test_mcp_connector_model_cleanup.py --no-cov 2>&1 | tee tmp/mcp_connector_model_cleanup_task1_fail.log | tail -20
```

Expected: FAIL because connector-centric repository methods do not exist or are not implemented.

- [ ] **Step 4: Add connector-centric repository methods**

Modify `backend/cubebox/repositories/mcp.py`:

- keep existing install-centric methods during cutover
- add connector-centric methods listed in the Interfaces section
- make each query filter `org_id == self.org_id`
- make each upsert force `org_id` from the repository constructor

- [ ] **Step 5: Run repository tests**

Run:

```bash
cd backend
uv run pytest tests/e2e/test_mcp_connector_model_cleanup.py --no-cov 2>&1 | tee tmp/mcp_connector_model_cleanup_task1_pass.log | tail -20
```

Expected: PASS for the two new repository tests.

- [ ] **Step 6: Commit**

Run:

```bash
git add backend/tests/e2e/test_mcp_connector_model_cleanup.py backend/cubebox/repositories/mcp.py backend/cubebox/models/mcp.py
git commit -m "Add connector-centric MCP repository methods"
```

Expected: commit succeeds.

---

### Task 2: Migrate Workspace State and Grants to Connector Keys

**Files:**
- Modify: `backend/cubebox/models/mcp.py`
- Modify: `backend/cubebox/repositories/mcp.py`
- Create: one Alembic-generated migration under `backend/alembic/versions/` named by the revision command with message `mcp connector state grant keys`
- Modify: `backend/tests/e2e/test_migration.py`
- Modify: `backend/tests/e2e/test_mcp_connector_model_cleanup.py`

**Interfaces:**
- Consumes: repository methods from Task 1.
- Produces:
  - `mcp_workspace_connector_states.connector_id` is non-null for active runtime rows.
  - `mcp_credential_grants.connector_id` is non-null for active runtime rows.
  - unique state key is `(workspace_id, connector_id)`.
  - unique grant keys are connector-centric.

- [ ] **Step 1: Write migration tests**

Add migration assertions to `backend/tests/e2e/test_migration.py` that inspect the generated migration text or migrated schema and assert:

- workspace state has a connector-centric unique constraint
- credential grant has connector-centric partial unique indexes
- install-centric unique constraints are removed only after connector-centric constraints exist

Use the existing migration test style in this file.

- [ ] **Step 2: Generate migration**

Run:

```bash
cd backend
uv run alembic revision --autogenerate -m "mcp connector state grant keys"
```

Expected: Alembic creates one new migration file under `backend/alembic/versions/`.

- [ ] **Step 3: Review generated migration**

Check the generated file and ensure it:

- backfills missing `connector_id` from existing install mappings
- creates connector-centric unique constraints/indexes
- makes `connector_id` non-null only after backfill
- keeps `install_id` nullable or present until later cutover tasks remove readers
- has downgrade steps that restore the previous schema shape where possible

- [ ] **Step 4: Update models**

Modify `backend/cubebox/models/mcp.py` so:

- `MCPWorkspaceConnectorState.connector_id` is required
- `MCPCredentialGrant.connector_id` is required
- SQLModel table args match the new connector-centric uniqueness
- `install_id` remains present only if still needed by compatibility code in this task

- [ ] **Step 5: Run migration and repository tests**

Run:

```bash
cd backend
uv run pytest tests/e2e/test_migration.py tests/e2e/test_mcp_connector_model_cleanup.py --no-cov 2>&1 | tee tmp/mcp_connector_model_cleanup_task2.log | tail -30
```

Expected: PASS for migration and repository coverage.

- [ ] **Step 6: Commit**

Run:

```bash
git add backend/alembic/versions backend/cubebox/models/mcp.py backend/cubebox/repositories/mcp.py backend/tests/e2e/test_migration.py backend/tests/e2e/test_mcp_connector_model_cleanup.py
git commit -m "Rekey MCP state and grants by connector"
```

Expected: commit succeeds.

---

### Task 3: Cut Over Service Creation and Workspace Enablement

**Files:**
- Modify: `backend/cubebox/services/mcp_installs.py`
- Modify: `backend/cubebox/mcp/workspace_bootstrap.py`
- Modify: `backend/cubebox/mcp/dependencies.py`
- Modify: `backend/cubebox/api/routes/v1/admin_mcp.py`
- Modify: `backend/cubebox/api/routes/v1/ws_mcp.py`
- Modify: `backend/tests/e2e/test_mcp_connector_model_cleanup.py`
- Modify: existing MCP route/service tests that still assert install-centric behavior

**Interfaces:**
- Consumes: connector-centric repository methods and schema from Tasks 1-2.
- Produces:
  - admin add creates/reuses connector and writes optional org grant/state rows by `connector_id`
  - workspace enable upserts state by `connector_id`
  - no new workspace-scope install row is created for workspace enablement

- [ ] **Step 1: Add failing business-flow tests**

Extend `backend/tests/e2e/test_mcp_connector_model_cleanup.py` with:

```python
async def test_org_add_does_not_create_install_row_for_workspace_override(...): ...
async def test_workspace_enable_uses_connector_state_without_workspace_install(...): ...
async def test_two_workspaces_can_use_different_workspace_grants_for_same_connector(...): ...
```

The tests must assert database rows directly:

- one active `mcp_connectors` row
- workspace states keyed by the same `connector_id`
- no new active workspace-scope `mcp_connector_installs` row
- grants keyed by `connector_id`

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd backend
uv run pytest tests/e2e/test_mcp_connector_model_cleanup.py --no-cov 2>&1 | tee tmp/mcp_connector_model_cleanup_task3_fail.log | tail -30
```

Expected: FAIL because service/routes still create or depend on install rows.

- [ ] **Step 3: Refactor creation service**

Modify `backend/cubebox/services/mcp_installs.py` or introduce a connector-centric service in the same module to:

- ensure connector from template/custom server
- write org credential grant by `connector_id`
- fan out workspace states by `connector_id`
- upsert workspace enablement by `connector_id`
- keep compatibility wrappers only for routes not yet migrated in this task

- [ ] **Step 4: Update admin and workspace routes**

Modify `backend/cubebox/api/routes/v1/admin_mcp.py` and `backend/cubebox/api/routes/v1/ws_mcp.py` so:

- request handlers resolve connector identity first
- workspace enable/disable uses `connector_id`
- route-level scope isolation remains unchanged
- legacy `install_id` route parameters are converted to `connector_id` at the boundary where they still exist

- [ ] **Step 5: Update workspace bootstrap**

Modify `backend/cubebox/mcp/workspace_bootstrap.py` so new workspaces are auto-enrolled from connector rollout data and write connector-keyed state rows.

- [ ] **Step 6: Run service and route tests**

Run:

```bash
cd backend
uv run pytest tests/e2e/test_mcp_connector_model_cleanup.py tests/e2e/test_mcp_credential_layering.py tests/e2e/test_mcp_install_uniqueness.py --no-cov 2>&1 | tee tmp/mcp_connector_model_cleanup_task3_pass.log | tail -30
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```bash
git add backend/cubebox/services/mcp_installs.py backend/cubebox/mcp/workspace_bootstrap.py backend/cubebox/mcp/dependencies.py backend/cubebox/api/routes/v1/admin_mcp.py backend/cubebox/api/routes/v1/ws_mcp.py backend/tests/e2e/test_mcp_connector_model_cleanup.py backend/tests/e2e/test_mcp_credential_layering.py backend/tests/e2e/test_mcp_install_uniqueness.py
git commit -m "Create MCP workspace state from connector identity"
```

Expected: commit succeeds.

---

### Task 4: Cut Over OAuth, Credential Grants, and Effective Runtime

**Files:**
- Modify: `backend/cubebox/mcp/oauth/start.py`
- Modify: `backend/cubebox/mcp/oauth/callback.py`
- Modify: `backend/cubebox/mcp/effective.py`
- Modify: `backend/cubebox/streams/run_manager.py`
- Modify: `backend/cubebox/services/mcp_discovery.py`
- Modify: `backend/cubebox/mcp/template_seed.py`
- Modify: OAuth and runtime e2e tests under `backend/tests/e2e/`

**Interfaces:**
- Consumes: connector-keyed states and grants from Tasks 1-3.
- Produces:
  - OAuth state carries `connector_id`, `grant_scope`, `workspace_id`, and `user_id` as needed.
  - callback writes grants by `connector_id`.
  - effective runtime reads connector + workspace state + selected connector grant.
  - discovery writes connector metadata.

- [ ] **Step 1: Add failing OAuth/runtime tests**

Add or update e2e tests so they assert:

- org OAuth callback creates an org grant keyed by `connector_id`
- workspace OAuth/static credential creates a workspace grant keyed by `connector_id`
- user OAuth creates a user grant keyed by `connector_id`, `workspace_id`, and `user_id`
- runtime with `workspace` policy does not fall back to org grant
- runtime with `user` policy does not fall back to org or workspace grant
- discovery updates `mcp_connectors.tools_cache`

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd backend
uv run pytest tests/e2e/test_mcp_oauth_handoff.py tests/e2e/test_mcp_four_layer_runtime.py tests/e2e/test_mcp_connector_model_cleanup.py --no-cov 2>&1 | tee tmp/mcp_connector_model_cleanup_task4_fail.log | tail -40
```

Expected: FAIL in OAuth/runtime paths that still use install ids.

- [ ] **Step 3: Update OAuth start payload**

Modify `backend/cubebox/mcp/oauth/start.py` so OAuth state includes:

- `connector_id`
- `grant_scope`
- `workspace_id` when scope is workspace or user
- `user_id` when scope is user
- frontend origin and CSRF fields already present today

Reject invalid scope/identity combinations before creating provider URLs.

- [ ] **Step 4: Update OAuth callback grant writes**

Modify `backend/cubebox/mcp/oauth/callback.py` so callback:

- resolves connector by `connector_id`
- validates org/workspace/user ownership
- writes the selected grant through connector-centric grant repository methods
- does not create or update install rows

- [ ] **Step 5: Update effective runtime**

Modify `backend/cubebox/mcp/effective.py` so effective state:

- lists connectors available to the workspace through org availability and workspace state
- reads workspace state by `connector_id`
- resolves grants by `connector_id`
- keeps strict no-fallback credential selection

- [ ] **Step 6: Update stream runtime and discovery**

Modify `backend/cubebox/streams/run_manager.py` and `backend/cubebox/services/mcp_discovery.py` so runtime tool specs and discovery metadata come from `MCPConnector`.

- [ ] **Step 7: Run OAuth/runtime tests**

Run:

```bash
cd backend
uv run pytest tests/e2e/test_mcp_oauth_handoff.py tests/e2e/test_mcp_four_layer_runtime.py tests/e2e/test_mcp_connector_model_cleanup.py --no-cov 2>&1 | tee tmp/mcp_connector_model_cleanup_task4_pass.log | tail -40
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```bash
git add backend/cubebox/mcp/oauth/start.py backend/cubebox/mcp/oauth/callback.py backend/cubebox/mcp/effective.py backend/cubebox/streams/run_manager.py backend/cubebox/services/mcp_discovery.py backend/cubebox/mcp/template_seed.py backend/tests/e2e
git commit -m "Resolve MCP runtime credentials by connector"
```

Expected: commit succeeds.

---

### Task 5: Cut Over API Schemas, Frontend Types, and UI Copy

**Files:**
- Modify: `backend/cubebox/api/schemas/mcp.py`
- Modify: `backend/cubebox/api/schemas/mcp_admin_connector.py`
- Modify: `backend/cubebox/api/schemas/mcp_ws_available.py`
- Modify: `frontend/packages/core/src/types/mcp.ts`
- Modify: `frontend/packages/core/src/types/mcp_admin_connector.ts`
- Modify: `frontend/packages/core/src/types/mcp_ws_available.ts`
- Modify: `frontend/packages/core/src/api/mcp.ts`
- Modify: `frontend/packages/core/__tests__/api/mcp.test.ts`
- Modify: `frontend/packages/web/app/admin/mcp/page.tsx`
- Modify: current workspace MCP page under `frontend/packages/web/app/`
- Modify: `frontend/packages/web/messages/en.json`
- Modify: `frontend/packages/web/messages/zh.json`

**Interfaces:**
- Consumes: connector-centric backend routes from Tasks 3-4.
- Produces:
  - frontend uses `connector_id` as stable key
  - public API response types expose connector primary objects
  - UI copy says add/enable/connect instead of treating install as the object

- [ ] **Step 1: Update frontend API tests first**

Modify `frontend/packages/core/__tests__/api/mcp.test.ts` so mocked MCP responses use `connector_id` as required and avoid install-centric primary keys for new endpoints.

- [ ] **Step 2: Run frontend tests to verify failure**

Run:

```bash
cd frontend
pnpm test -- --runInBand packages/core/__tests__/api/mcp.test.ts 2>&1 | tee tmp/mcp_connector_model_cleanup_task5_fail.log | tail -30
```

Expected: FAIL because frontend types/API still accept install-centric shapes.

- [ ] **Step 3: Update backend schemas**

Modify backend MCP schemas so connector-centric response objects include:

- `connector_id`
- `template_id`
- `name`
- `server_url`
- `transport`
- `auth_method`
- `discovery_status`
- `tool_count`
- `tools`
- `tool_citations`
- workspace state when applicable
- credential availability when applicable

Keep compatibility fields only where legacy routes still require them during this task.

- [ ] **Step 4: Update core TypeScript types and API client**

Modify `frontend/packages/core/src/types/mcp.ts` and `frontend/packages/core/src/api/mcp.ts` so new connector objects require `connector_id` and workspace/admin actions pass connector ids.

- [ ] **Step 5: Update admin and workspace pages**

Modify the admin and workspace MCP pages so list keys, action payloads, and local state use `connector_id`.

Update copy:

- admin: "Add to organization", "Provide organization credential"
- workspace: "Enable connector", "Use organization credential", "Use workspace credential", "Connect my account"

- [ ] **Step 6: Run frontend checks**

Run:

```bash
cd frontend
pnpm test -- --runInBand packages/core/__tests__/api/mcp.test.ts 2>&1 | tee tmp/mcp_connector_model_cleanup_task5_test.log | tail -30
pnpm check-ci 2>&1 | tee tmp/mcp_connector_model_cleanup_task5_check_ci.log | tail -30
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```bash
git add backend/cubebox/api/schemas/mcp.py backend/cubebox/api/schemas/mcp_admin_connector.py backend/cubebox/api/schemas/mcp_ws_available.py frontend/packages/core/src/types/mcp.ts frontend/packages/core/src/types/mcp_admin_connector.ts frontend/packages/core/src/types/mcp_ws_available.ts frontend/packages/core/src/api/mcp.ts frontend/packages/core/__tests__/api/mcp.test.ts frontend/packages/web/app frontend/packages/web/messages/en.json frontend/packages/web/messages/zh.json
git commit -m "Expose MCP connectors as primary API objects"
```

Expected: commit succeeds.

---

### Task 6: Remove Install Runtime Dependencies and Drop Install Table

**Files:**
- Modify: `backend/cubebox/models/mcp.py`
- Modify: `backend/cubebox/repositories/mcp.py`
- Modify: `backend/cubebox/services/mcp_installs.py`
- Modify: `backend/cubebox/models/__init__.py`
- Modify: `backend/cubebox/repositories/__init__.py`
- Modify: `backend/cubebox/models/public_id.py`
- Create: one Alembic-generated migration under `backend/alembic/versions/` named by the revision command with message `drop mcp connector installs`
- Modify: tests that import `MCPConnectorInstall`

**Interfaces:**
- Consumes: all connector-centric readers/writers from Tasks 1-5.
- Produces:
  - no runtime code imports `MCPConnectorInstall`
  - `mcp_connector_installs` is dropped
  - public APIs no longer expose `install_id` except legacy compatibility responses if explicitly retained

- [ ] **Step 1: Search for remaining install dependencies**

Run:

```bash
rg -n "MCPConnectorInstall|mcp_connector_installs|install_id" backend/cubebox backend/tests frontend/packages/core frontend/packages/web docs/site/docs
```

Expected: output lists remaining places to migrate or compatibility references to justify.

- [ ] **Step 2: Update or delete install-centric tests**

Modify tests so they assert connector-centric behavior. Delete tests whose only purpose is preserving install-centric behavior.

- [ ] **Step 3: Generate drop migration**

Run:

```bash
cd backend
uv run alembic revision --autogenerate -m "drop mcp connector installs"
```

Expected: Alembic creates one migration that removes `mcp_connector_installs` and remaining install foreign keys only after code no longer uses them.

- [ ] **Step 4: Remove model and repository exports**

Modify backend model/repository modules to remove:

- `MCPConnectorInstall`
- `MCPConnectorInstallRepository`
- install public id prefix if unused
- compatibility service methods that return install rows

- [ ] **Step 5: Run backend typecheck and tests**

Run:

```bash
cd backend
uv run mypy cubebox 2>&1 | tee tmp/mcp_connector_model_cleanup_task6_mypy.log | tail -40
uv run pytest tests/e2e/test_mcp_connector_model_cleanup.py tests/e2e/test_migration.py --no-cov 2>&1 | tee tmp/mcp_connector_model_cleanup_task6_pytest.log | tail -40
```

Expected: PASS.

- [ ] **Step 6: Run final search**

Run:

```bash
rg -n "MCPConnectorInstall|mcp_connector_installs|install_id" backend/cubebox frontend/packages/core frontend/packages/web docs/site/docs || true
```

Expected: no runtime references. Any remaining docs references must describe historical migration only.

- [ ] **Step 7: Commit**

Run:

```bash
git add backend/cubebox backend/alembic/versions backend/tests frontend/packages/core frontend/packages/web docs/site/docs
git commit -m "Drop MCP connector install runtime model"
```

Expected: commit succeeds.

---

### Task 7: Update User Docs and Run Pre-PR Verification

**Files:**
- Modify: `docs/site/docs/admin/mcp-connectors.md`
- Modify: `docs/site/docs/guides/mcp/installing-connectors.md`
- Modify: `docs/site/docs/guides/mcp/overview.md`
- Modify: `backend/docs/mcp_catalog_oauth.md`

**Interfaces:**
- Consumes: final connector-centric behavior from Tasks 1-6.
- Produces:
  - user docs match the product vocabulary
  - backend MCP reference no longer describes install as the durable core object
  - full changed-area checks pass

- [ ] **Step 1: Update docs**

Update docs to describe:

- organization connector identity
- organization credentials
- workspace enablement
- workspace/user credential overrides
- no implicit fallback between credential scopes

Remove product phrasing that describes workspace credentials as duplicate installs.

- [ ] **Step 2: Run backend checks**

Run:

```bash
cd backend
uv run mypy cubebox 2>&1 | tee tmp/mcp_connector_model_cleanup_final_mypy.log | tail -40
uv run pytest tests/e2e/test_mcp_connector_model_cleanup.py tests/e2e/test_mcp_oauth_handoff.py tests/e2e/test_mcp_four_layer_runtime.py tests/e2e/test_migration.py --no-cov 2>&1 | tee tmp/mcp_connector_model_cleanup_final_pytest.log | tail -40
```

Expected: PASS.

- [ ] **Step 3: Run frontend checks**

Run:

```bash
cd frontend
pnpm check-ci 2>&1 | tee tmp/mcp_connector_model_cleanup_final_frontend.log | tail -40
```

Expected: PASS.

- [ ] **Step 4: Run final git review**

Run:

```bash
git status --short
git log --oneline --max-count=10
```

Expected: only intentional changes are present; commits are task-sized and ordered.

- [ ] **Step 5: Commit docs**

Run:

```bash
git add docs/site/docs/admin/mcp-connectors.md docs/site/docs/guides/mcp/installing-connectors.md docs/site/docs/guides/mcp/overview.md backend/docs/mcp_catalog_oauth.md
git commit -m "Document MCP connector credential model"
```

Expected: commit succeeds if docs changed after Task 6; if Task 6 already included all doc changes, this step should report no staged changes and can be skipped.

---

## Self-Review

- Spec coverage: the plan covers connector identity, workspace state, credential grants, OAuth, runtime resolution, discovery, API/frontend, docs, migration, and install table removal.
- Red-flag scan: this plan contains no deferred implementation markers by design.
- Type consistency: connector-centric methods consistently use `connector_id: str`; workspace and user grant lookups require `workspace_id` where the data model requires it.
- Scope check: this is one follow-up PR because all tasks are coupled by the same foreign-key cutover. Splitting before the runtime cutover would leave long-lived dual identity paths.
