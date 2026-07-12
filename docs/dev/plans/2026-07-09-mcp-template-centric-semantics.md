# MCP Template-Centric Semantics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `docs/dev/specs/2026-07-09-mcp-template-centric-semantics-design.md` — dissolve the "install" concept: templates gain a visibility scope (`global`/`org`/`workspace`), connector rows become lazily-created infrastructure, `auth_method` moves to grants, org-level disable lives in a new `mcp_connector_templates_settings` table, and both admin and workspace pages become one template-driven list.

**Architecture:** Backend-first cutover on one branch. Schema migration first (additive columns + data backfill + drops), then repositories, then the pure list-composition and effective-state functions, then services/routes, then the frontend against the new API. The project is unreleased: old endpoints are deleted, not shimmed.

**Tech Stack:** FastAPI + SQLModel + Alembic (backend, mypy strict), Next.js + React 19 + `@cubebox/core` (frontend, strict TS), pytest e2e against real Postgres, Playwright for frontend flows.

## Global Constraints

- Read `AGENTS.md` (repo root) first. Non-negotiables that bite here:
  - Worktree first; **read `.worktree.env`** — ports 8000/3000 are wrong inside worktrees. `cat .worktree.env` on every fresh shell (subagents: re-`cd` + `pwd` on every Bash call).
  - Tests touching Postgres/the app → `backend/tests/e2e/`. Pure functions → `backend/tests/unit/`. Misplacing breaks `make check-ci`.
  - tz-aware datetimes only (`datetime.now(UTC)`).
  - New table → public-ID prefix in `backend/cubebox/models/public_id.py`.
  - Migrations via `alembic revision --autogenerate`; data-migration statements are added to the generated revision (never hand-craft schema ops autogen can produce).
  - Line length 100. `uv add` / `pnpm add` for deps (none expected). pnpm, never npm.
  - Pipe noisy output through `tee tmp/<task>.log`, then `tail -3`.
  - No backwards-compat shims; delete old surface cleanly.
- Spec is the contract: `docs/dev/specs/2026-07-09-mcp-template-centric-semantics-design.md`. When this plan and the spec disagree, the spec wins.
- Error codes introduced here (copy verbatim): `template_disabled_in_org`, `template_not_visible`, `template_not_owned_by_workspace`, `connector_name_conflict`, `auth_method_not_supported_by_template`.
- New public-ID prefix: `mcts` for `mcp_connector_templates_settings`.

## File Structure (what exists where when we're done)

```
backend/cubebox/models/mcp.py                      # template scope cols; settings model; grant auth_method; connector minus auth cols
backend/cubebox/models/public_id.py                # + PREFIX_MCP_TEMPLATE_SETTINGS = "mcts"
backend/cubebox/repositories/mcp.py                # visibility queries; settings repo; lazy get_or_create
backend/cubebox/services/mcp_catalog.py            # NEW: pure list-composition for both pages
backend/cubebox/services/mcp_installs.py           # MCPConnectorService: ensure/distribute/purge (install/promote/distribution machinery deleted)
backend/cubebox/services/mcp_templates.py          # template service grows create/promote/visibility
backend/cubebox/services/mcp_ws_available.py       # DELETED
backend/cubebox/services/mcp_admin_connectors.py   # DELETED
backend/cubebox/mcp/effective.py                   # disabled veto; grant-level auth
backend/cubebox/mcp/workspace_bootstrap.py         # skips org-disabled templates
backend/cubebox/mcp/oauth/start.py                 # validates via template.supported_auth_methods
backend/cubebox/api/routes/v1/admin_mcp.py         # catalog/templates/disable/distribute/purge; installs+promote+connectors removed
backend/cubebox/api/routes/v1/ws_mcp.py            # catalog/template-state/templates+promote; installs+available removed
backend/cubebox/api/schemas/mcp_catalog.py         # NEW: catalog Out models (admin + ws)
backend/cubebox/api/schemas/mcp.py                 # install-create/promote/distribution In models removed
backend/cubebox/api/schemas/mcp_admin_connector.py # DELETED
backend/cubebox/api/schemas/mcp_ws_available.py    # DELETED
frontend/packages/core/src/api/mcp.ts              # client rewritten to new surface
frontend/packages/web/app/admin/mcp/page.tsx       # single catalog list + filters
frontend/packages/web/app/(app)/w/[wsId]/.../mcp   # ws page → single catalog list (locate exact path in Task 10)
frontend/packages/web/components/mcp/…             # MCPCatalogList, MCPDistributeDialog new; install panels + promote dialog deleted
docs/site/docs/…connectors…                        # rewritten around catalog/enable/distribute/disable/purge
```

---

### Task 1: Worktree + baseline

**Files:** none (environment).

- [ ] **Step 1: Create the worktree** — from the **main repo root**:

```bash
cd /home/chris/cubebox
./scripts/new-worktree feat/2026-07-09-mcp-template-centric
```

- [ ] **Step 2: Enter it, read the env, run doctor**

```bash
cd ../cubebox-worktrees/feat-2026-07-09-mcp-template-centric 2>/dev/null || cd "$(git worktree list | grep 2026-07-09-mcp-template-centric | awk '{print $1}')"
cat .worktree.env
./scripts/worktree-env doctor
```

Record the allocated backend/frontend ports and test DB name; use them everywhere below. Copy `backend/.env` + `backend/config.development.local.yaml` from the main checkout if doctor says they're missing.

- [ ] **Step 3: Baseline test run** (changed-module scope only)

```bash
cd backend && uv run pytest tests/e2e/test_mcp_four_layer_routes.py --no-cov 2>&1 | tee ../tmp/baseline.log | tail -3
```

Expected: PASS (or note pre-existing failures in the task journal — do not fix them here).

- [ ] **Step 4: Commit the spec + plan** if not already committed on this branch:

```bash
git add docs/dev/specs/2026-07-09-mcp-template-centric-semantics-design.md docs/dev/plans/2026-07-09-mcp-template-centric-semantics.md
git commit -m "docs: spec + plan for MCP template-centric semantics"
```

---

### Task 2: Models + public-ID prefix

**Files:**
- Modify: `backend/cubebox/models/mcp.py`
- Modify: `backend/cubebox/models/public_id.py` (after line 59, `PREFIX_MCP_CONNECTOR`)
- Modify: `backend/cubebox/models/__init__.py` (export `MCPConnectorTemplateSettings`)

**Interfaces:**
- Produces: `MCPConnectorTemplate.scope/org_id/workspace_id/created_by_user_id`; `MCPConnectorTemplateSettings` model; `MCPCredentialGrant.auth_method`; `MCPConnector` **without** `auth_method`/`auth_status`/`install_scope`/`workspace_id` and with `template_id: str` (non-null).

- [ ] **Step 1: Add the prefix** in `public_id.py`:

```python
PREFIX_MCP_TEMPLATE_SETTINGS: str = "mcts"
```

- [ ] **Step 2: Extend `MCPConnectorTemplate`** — add after the `status` field (models/mcp.py:87), and add the check constraints to `__table_args__` (keep the existing slug unique constraint):

```python
    # Visibility scope (spec §3.1). 'global' rows are the seeded catalog and
    # carry no owner; 'org'/'workspace' rows are custom templates.
    scope: str = Field(
        default="global",
        max_length=16,
        sa_column_kwargs={"server_default": text("'global'")},
    )
    org_id: str | None = Field(
        default=None, foreign_key="organizations.id", max_length=20, nullable=True, index=True
    )
    workspace_id: str | None = Field(
        default=None, foreign_key="workspaces.id", max_length=20, nullable=True, index=True
    )
    created_by_user_id: str | None = Field(
        default=None, foreign_key="users.id", max_length=20, nullable=True
    )
```

```python
    __table_args__ = (
        UniqueConstraint("slug", name="uq_mcp_connector_template_slug"),
        CheckConstraint(
            "(scope='global' AND org_id IS NULL AND workspace_id IS NULL)"
            " OR (scope='org' AND org_id IS NOT NULL AND workspace_id IS NULL)"
            " OR (scope='workspace' AND org_id IS NOT NULL AND workspace_id IS NOT NULL)",
            name="ck_mcp_connector_templates_scope_shape",
        ),
    )
```

- [ ] **Step 3: Add `MCPConnectorTemplateSettings`** (new class at the end of models/mcp.py):

```python
class MCPConnectorTemplateSettings(CubeboxBase, table=True):
    """Per-(org, template) settings. Absent row = all defaults (spec §3.4)."""

    _PREFIX: ClassVar[str] = PREFIX_MCP_TEMPLATE_SETTINGS
    __tablename__ = "mcp_connector_templates_settings"
    __table_args__ = (
        UniqueConstraint("org_id", "template_id", name="uq_mcp_connector_templates_settings"),
    )

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    template_id: str = Field(foreign_key="mcp_connector_templates.id", max_length=20, index=True)
    disabled: bool = Field(default=False, sa_column_kwargs={"server_default": text("false")})
    updated_by_user_id: str | None = Field(
        default=None, foreign_key="users.id", max_length=20, nullable=True
    )
```

Import `PREFIX_MCP_TEMPLATE_SETTINGS` next to the existing `PREFIX_MCP_CONNECTOR` import.

- [ ] **Step 4: Grant gains `auth_method`** — in `MCPCredentialGrant`, add next to `grant_scope` and add the check constraint to its `__table_args__`:

```python
    auth_method: str = Field(max_length=16)
```

```python
        CheckConstraint(
            "auth_method IN ('oauth','static')",
            name="ck_mcp_credential_grants_auth_method",
        ),
```

- [ ] **Step 5: Slim `MCPConnector`** — delete: the `auth_method` field (line 141), the `auth_status` field (147-150), the `ck_mcp_connectors_auth_method` CheckConstraint (96-99), the `install_scope`/`workspace_id` properties (201-206). Keep the `install_state` property for now (Task 7 removes its last callers, delete it there). Change `template_id` to non-null:

```python
    template_id: str = Field(foreign_key="mcp_connector_templates.id", max_length=20, index=True)
```

Simplify the partial index accordingly (drop the now-tautological NULL clause):

```python
        Index(
            "uq_mcp_connector_template_per_org",
            "org_id",
            "template_id",
            unique=True,
            postgresql_where="status = 'active'",
        ),
```

- [ ] **Step 6: Export + typecheck**. Add `MCPConnectorTemplateSettings` to `models/__init__.py`. Then:

```bash
cd backend && uv run mypy cubebox/models 2>&1 | tee ../tmp/task2-mypy.log | tail -3
```

Expected: models package clean. The rest of the codebase now fails mypy/imports — that is expected until Tasks 4-9; do **not** run the full suite here.

- [ ] **Step 7: Commit**

```bash
git add backend/cubebox/models
git commit -m "feat(mcp): template scope, template settings table, grant auth_method; slim connector"
```

---

### Task 3: Alembic migration + disposable-DB verification

**Files:**
- Create: `backend/alembic/versions/<autogen>_mcp_template_centric.py`
- Create: `backend/scripts/dev/verify_mcp_template_centric_migration.py`

**Interfaces:**
- Produces: schema matching Task 2 models, with data backfill (grant auth_method; synthesized org templates for custom connectors).

Per the AGENTS.md TDD-for-migrations judgment: no red/green loop; verify against a disposable DB seeded with the bad shapes.

- [ ] **Step 1: Autogenerate**

```bash
cd backend && uv run alembic revision --autogenerate -m "mcp template centric semantics" 2>&1 | tail -3
```

- [ ] **Step 2: Insert data-migration ops into the generated `upgrade()`**, ordered so backfills run while both old and new columns exist. Autogen will have produced the add-column/create-table/drop-column ops; **reorder and interleave** so the sequence is:

```python
    # 1) templates: new columns land first (autogen ops), server_default keeps
    #    existing rows scope='global'.

    # 2) grants.auth_method: autogen adds it nullable=False — relax to a
    #    3-step add-backfill-tighten:
    op.add_column("mcp_credential_grants", sa.Column("auth_method", sa.String(16), nullable=True))
    op.execute(
        """
        UPDATE mcp_credential_grants g
        SET auth_method = COALESCE(NULLIF(c.auth_method, 'none'), 'static')
        FROM mcp_connectors c
        WHERE g.connector_id = c.id
        """
    )
    op.alter_column("mcp_credential_grants", "auth_method", nullable=False)

    # 3) synthesize org-scope templates for custom connectors, then link.
    #    Runs BEFORE the connectors.auth_method drop (it reads that column)
    #    and BEFORE template_id goes NOT NULL.
    op.execute(
        """
        INSERT INTO mcp_connector_templates
            (id, slug, name, description, provider, server_url, transport,
             supported_auth_methods, default_credential_policy,
             static_auth_style, static_auth_header_name, static_auth_query_param,
             template_metadata, tool_citation_defaults, status,
             scope, org_id, workspace_id, created_by_user_id,
             created_at, updated_at)
        SELECT
            'mctpl_' || substr(md5(c.id), 1, 20),
            'custom-' || c.slug_name || '-' || lower(substr(c.org_id, length(c.org_id) - 5)),
            c.name, '', 'custom', c.server_url, c.transport,
            to_jsonb(ARRAY[c.auth_method]),
            c.default_credential_policy,
            c.static_auth_style, c.static_auth_header_name, c.static_auth_query_param,
            '{}'::jsonb, '{}'::jsonb, 'active',
            'org', c.org_id, NULL, c.created_by_user_id,
            now(), now()
        FROM mcp_connectors c
        WHERE c.template_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE mcp_connectors c
        SET template_id = 'mctpl_' || substr(md5(c.id), 1, 20)
        WHERE c.template_id IS NULL
        """
    )
    op.alter_column("mcp_connectors", "template_id", nullable=False)

    # 4) connectors: drop auth cols + old check constraint (autogen ops),
    #    recreate uq_mcp_connector_template_per_org without the NULL clause
    #    (autogen emits drop+create for the partial index — keep them here).

    # 5) settings table create (autogen op).
```

Adjust the JSON column casts to match the template `id` length limit (`mctpl_` + 20 hex chars must fit the id column width — check `CubeboxBase` id length and shorten the `substr` if needed). If the templates table stores `supported_auth_methods` as `JSON` not `JSONB`, use `to_json` instead — copy whatever type autogen shows for that column.

- [ ] **Step 3: Write the disposable-DB verification script** `backend/scripts/dev/verify_mcp_template_centric_migration.py`:

```python
"""Seed pre-migration MCP shapes on a scratch DB, run the new revision, assert results.

Usage (inside the worktree, venv active):
    createdb mcp_mig_scratch
    CUBEBOX_DATABASE__NAME=mcp_mig_scratch uv run alembic upgrade <previous_head>
    CUBEBOX_DATABASE__NAME=mcp_mig_scratch uv run python scripts/dev/verify_mcp_template_centric_migration.py seed
    CUBEBOX_DATABASE__NAME=mcp_mig_scratch uv run alembic upgrade head
    CUBEBOX_DATABASE__NAME=mcp_mig_scratch uv run python scripts/dev/verify_mcp_template_centric_migration.py check
    dropdb mcp_mig_scratch
"""
```

`seed` inserts (raw SQL against the pre-migration schema): one org+workspace+user, one global template, one templated connector with `auth_method='oauth'` + an org grant, one **custom** connector (`template_id NULL`, `auth_method='static'`) + a workspace grant. `check` asserts: every grant has `auth_method` matching its old connector value; zero connectors with `template_id IS NULL`; the synthesized template has `scope='org'`, the right `org_id`, `supported_auth_methods == ['static']`; `mcp_connector_templates_settings` exists and is empty; `mcp_connectors` has no `auth_method` column. Exit non-zero on any failure and print each assertion.

- [ ] **Step 4: Run the verification sequence** exactly as the script docstring says, using the worktree DB env override style from `docs/worktrees.md`. Expected final output: `all checks passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions backend/scripts/dev/verify_mcp_template_centric_migration.py
git commit -m "feat(mcp): migration to template-centric schema with grant/custom-connector backfill"
```

---

### Task 4: Repositories — visibility, settings, lazy connector

**Files:**
- Modify: `backend/cubebox/repositories/mcp.py`
- Test: `backend/tests/e2e/test_mcp_template_repositories.py` (opens AsyncSession ⇒ e2e per repo rules)

**Interfaces:**
- Produces (exact signatures — later tasks call these):
  - `MCPConnectorTemplateRepository.list_visible_for_org(org_id: str) -> list[MCPConnectorTemplate]`
  - `MCPConnectorTemplateRepository.list_visible_for_workspace(org_id: str, workspace_id: str) -> list[MCPConnectorTemplate]`
  - `MCPConnectorTemplateRepository.create_scoped(*, scope: str, org_id: str, workspace_id: str | None, created_by_user_id: str, name: str, server_url: str, transport: str, supported_auth_methods: list[str], default_credential_policy: str, headers_note: None = None) -> MCPConnectorTemplate` (slug generated as in the migration: `custom-<slugified-name>-<org suffix>`; raises `ValueError("connector_name_conflict")` on slug collision)
  - `MCPConnectorTemplateRepository.promote_to_org(template_id: str) -> MCPConnectorTemplate` (scope `workspace`→`org`, clears `workspace_id`; `ValueError("template_not_owned_by_workspace")` if scope isn't `workspace`)
  - `MCPTemplateSettingsRepository(session, *, org_id)` with `get(template_id) -> MCPConnectorTemplateSettings | None`, `set_disabled(template_id: str, disabled: bool, *, updated_by_user_id: str) -> MCPConnectorTemplateSettings` (upsert), `disabled_template_ids() -> set[str]`
  - `MCPConnectorRepository.get_by_template_id(template_id: str) -> MCPConnector | None` (active only)
  - `MCPConnectorRepository.get_or_create_for_template(template: MCPConnectorTemplate, *, created_by_user_id: str) -> MCPConnector` — race-safe lazy create copying the template snapshot (name, slug_name via existing `slugify_for_namespace`, server_url + hash, transport, static_auth_*, tool_citation_defaults, default_credential_policy); on `IntegrityError` rollback and re-`get_by_template_id`.

- [ ] **Step 1: Write failing e2e tests.** Model fixtures on `tests/e2e/test_mcp_four_layer_routes.py` (its `db_maker` fixture, lines 44-52, is the pattern for direct-DB assertions; org/workspace/user setup helpers come from `tests/e2e/conftest.py`). Cover exactly:

```python
async def test_visibility_partition(db_maker):
    # seed: global template; org template (org A); workspace template (org A, ws1);
    # org template owned by org B.
    # list_visible_for_org(A)        -> {global, orgA, wsA1}
    # list_visible_for_workspace(A, ws1) -> {global, orgA, wsA1}
    # list_visible_for_workspace(A, ws2) -> {global, orgA}          # ws1 custom hidden
    # list_visible_for_org(B)        -> {global, orgB}

async def test_settings_upsert_and_disabled_ids(db_maker):
    # set_disabled(t, True) twice is idempotent (one row, disabled=True);
    # disabled_template_ids() == {t.id}; set_disabled(t, False) keeps one row,
    # disabled_template_ids() == set().

async def test_lazy_connector_create_is_idempotent(db_maker):
    # get_or_create_for_template twice -> same row id; snapshot fields copied;
    # two concurrent creates (asyncio.gather with two sessions) -> both return
    # the same connector id.

async def test_promote_to_org(db_maker):
    # workspace template -> promote -> scope=='org', workspace_id is None;
    # promoting an org template raises ValueError("template_not_owned_by_workspace").
```

- [ ] **Step 2: Run to verify failure**

```bash
cd backend && uv run pytest tests/e2e/test_mcp_template_repositories.py --no-cov 2>&1 | tee ../tmp/task4-red.log | tail -3
```

Expected: FAIL with `AttributeError` (methods don't exist).

- [ ] **Step 3: Implement.** Visibility queries are one `select` each:

```python
    async def list_visible_for_org(self, org_id: str) -> list[MCPConnectorTemplate]:
        stmt = select(MCPConnectorTemplate).where(
            cast("ColumnElement[bool]", MCPConnectorTemplate.status == "active"),
            or_(
                cast("ColumnElement[bool]", MCPConnectorTemplate.scope == "global"),
                cast("ColumnElement[bool]", MCPConnectorTemplate.org_id == org_id),
            ),
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_visible_for_workspace(
        self, org_id: str, workspace_id: str
    ) -> list[MCPConnectorTemplate]:
        stmt = select(MCPConnectorTemplate).where(
            cast("ColumnElement[bool]", MCPConnectorTemplate.status == "active"),
            or_(
                cast("ColumnElement[bool]", MCPConnectorTemplate.scope == "global"),
                and_(
                    cast("ColumnElement[bool]", MCPConnectorTemplate.scope == "org"),
                    cast("ColumnElement[bool]", MCPConnectorTemplate.org_id == org_id),
                ),
                and_(
                    cast("ColumnElement[bool]", MCPConnectorTemplate.scope == "workspace"),
                    cast("ColumnElement[bool]", MCPConnectorTemplate.workspace_id == workspace_id),
                ),
            ),
        )
        return list((await self.session.execute(stmt)).scalars().all())
```

`MCPTemplateSettingsRepository` follows the org-only scoping pattern documented at the top of the file (`__init__(session, *, org_id)`, filter + force-set `org_id`). `get_or_create_for_template`:

```python
    async def get_or_create_for_template(
        self,
        template: MCPConnectorTemplate,
        *,
        created_by_user_id: str,
    ) -> MCPConnector:
        existing = await self.get_by_template_id(template.id)
        if existing is not None:
            return existing
        row = MCPConnector(
            org_id=self.org_id,
            template_id=template.id,
            name=template.name,
            slug_name=slugify_for_namespace(template.name),
            server_url=template.server_url,
            server_url_hash=server_url_hash(template.server_url),
            transport=template.transport,
            default_credential_policy=template.default_credential_policy,
            static_auth_style=template.static_auth_style,
            static_auth_header_name=template.static_auth_header_name,
            static_auth_query_param=template.static_auth_query_param,
            tool_citations=dict(template.tool_citation_defaults),
            created_by_user_id=created_by_user_id,
        )
        try:
            return await self.add(row)
        except IntegrityError:
            await self.session.rollback()
            raced = await self.get_by_template_id(template.id)
            if raced is None:
                raise
            return raced
```

(`slugify_for_namespace` / `server_url_hash` live in `cubebox/mcp/_constants.py` — same imports `services/mcp_installs.py` uses today.)

- [ ] **Step 4: Green + commit**

```bash
uv run pytest tests/e2e/test_mcp_template_repositories.py --no-cov 2>&1 | tee ../tmp/task4-green.log | tail -3
git add backend/cubebox/repositories/mcp.py backend/tests/e2e/test_mcp_template_repositories.py
git commit -m "feat(mcp): template visibility queries, settings repo, lazy connector creation"
```

---

### Task 5: Pure catalog composition — `services/mcp_catalog.py`

**Files:**
- Create: `backend/cubebox/services/mcp_catalog.py`
- Delete: `backend/cubebox/services/mcp_ws_available.py`, `backend/cubebox/services/mcp_admin_connectors.py` (+ their unit tests under `backend/tests/unit/` — find with `grep -rl "mcp_ws_available\|mcp_admin_connectors" backend/tests`)
- Test: `backend/tests/unit/test_mcp_catalog.py` (pure ⇒ unit)

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class AdminCatalogRow:
    template: Any                    # MCPConnectorTemplate (duck-typed for tests)
    connector: Any | None
    disabled: bool
    enabled_workspace_count: int
    eligible_workspace_count: int
    auto_enroll_new_workspaces: bool
    org_grant_status: str | None     # 'valid' | 'expired' | None
    in_use: bool                     # connector is not None
    needs_attention: bool            # expired org grant OR connector.discovery_status=='error'

def build_admin_catalog_rows(
    *,
    templates: list[Any],
    connectors_by_template_id: dict[str, Any],
    disabled_template_ids: set[str],
    enabled_counts_by_connector_id: dict[str, int],
    org_grants_by_connector_id: dict[str, Any],
    eligible_workspace_count: int,
) -> list[AdminCatalogRow]

@dataclass(frozen=True)
class WorkspaceCatalogRow:
    template: Any
    connector: Any | None
    enabled: bool                    # this workspace's state row says enabled

def build_workspace_catalog_rows(
    *,
    templates: list[Any],            # already visibility-filtered (Task 4 query)
    connectors_by_template_id: dict[str, Any],
    states_by_connector_id: dict[str, Any],
    disabled_template_ids: set[str],
) -> list[WorkspaceCatalogRow]      # org-disabled templates are EXCLUDED entirely
```

Ordering: admin rows sorted in-use first then template name; workspace rows enabled first then template name. Stable so the frontend can diff.

- [ ] **Step 1: Failing unit tests** (dataclass stand-ins, same style the deleted `mcp_admin_connectors` tests used):

```python
def test_admin_rows_cover_all_visible_templates_with_facts(): ...
    # 3 templates: one with connector + valid org grant + 2 enabled ws -> in_use,
    # org_grant_status='valid', needs_attention False;
    # one with connector + expired grant -> needs_attention True;
    # one without connector -> in_use False, counts 0, org_grant_status None.

def test_admin_disabled_flag_passthrough(): ...
def test_workspace_rows_exclude_org_disabled(): ...
def test_workspace_enabled_comes_from_state_row(): ...
    # no state row -> enabled False; state enabled=False -> False; True -> True.
```

- [ ] **Step 2: Red** — `uv run pytest tests/unit/test_mcp_catalog.py --no-cov 2>&1 | tail -3` → `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — straight comprehensions over the inputs; no I/O, no session. `needs_attention = (org_grant_status == 'expired') or (connector is not None and connector.discovery_status == 'error')`.

- [ ] **Step 4: Delete the two superseded modules and their tests.** Their route-layer callers break — Tasks 9-10 rewrite those; leave broken imports only inside `admin_mcp.py`/`ws_mcp.py` (tracked there).

- [ ] **Step 5: Green + commit**

```bash
uv run pytest tests/unit/test_mcp_catalog.py --no-cov 2>&1 | tee ../tmp/task5.log | tail -3
git add -A backend/cubebox/services backend/tests
git commit -m "feat(mcp): pure catalog composition; retire ws_available/admin_connectors derivations"
```

---

### Task 6: Effective state — disabled veto + grant-level auth

**Files:**
- Modify: `backend/cubebox/mcp/effective.py`
- Modify: `backend/cubebox/services/mcp_discovery.py` (`_build_runtime_spec_for_discovery`, line ~213; usability check line ~359)
- Modify: `backend/cubebox/mcp/oauth/start.py:143`
- Test: `backend/tests/unit/test_mcp_effective_state.py` (extend the existing unit tests for `compute_effective_state` — locate via `grep -rl compute_effective_state backend/tests/unit`)

**Interfaces:**
- `MCPEffectiveInput` changes (all callers updated in this task): **remove** `auth_method: str`, `auth_status: str`; **add** `org_disabled: bool`, `auth_required: bool` (template supports anything besides `none`), `oauth_supported: bool`. `MCPGrantInput` gains `auth_method: str`.
- `MCPEffectiveReason` gains `"template_disabled_in_org"`.
- `MCPRuntimeConnectorSpec.auth_method` is now populated from the grant (`grant.auth_method`), or `"none"` when `auth_required` is false.

- [ ] **Step 1: Failing unit tests** for the new decision table:

```python
def test_org_disabled_vetoes_everything():
    # enabled state, valid grant, but org_disabled=True
    # -> (False, "template_disabled_in_org", "missing")

def test_auth_not_required_is_usable_without_grant(): ...
def test_missing_grant_oauth_supported_org_policy_is_pending_oauth(): ...
    # auth_required=True, oauth_supported=True, grant=None, policy='org'
def test_missing_grant_static_only_is_missing_org_grant(): ...
    # oauth_supported=False -> reason 'missing_org_grant'
def test_user_policy_missing_grant_is_user_needs_connection(): ...
```

- [ ] **Step 2: Red**, then **Step 3: Implement** `compute_effective_state`:

```python
def compute_effective_state(value: MCPEffectiveInput) -> MCPEffectiveResult:
    if value.org_disabled:
        return MCPEffectiveResult(False, "template_disabled_in_org", "missing")
    if not value.install_present:
        return MCPEffectiveResult(False, "not_installed", "missing")
    if value.install_state == "uninstalled":
        return MCPEffectiveResult(False, "install_uninstalled", "missing")
    if value.template_status == "disabled":
        return MCPEffectiveResult(False, "template_deprecated", "missing")
    if not value.workspace_state_present or not value.workspace_enabled:
        return MCPEffectiveResult(False, "not_enabled_in_workspace", "missing")
    if not value.auth_required:
        return MCPEffectiveResult(True, "usable", "not_required")
    if value.grant is None:
        if value.oauth_supported and value.credential_policy in {"org", "workspace"}:
            return MCPEffectiveResult(False, "pending_oauth", "missing")
        return MCPEffectiveResult(False, _missing_grant_reason(value.credential_policy), "missing")
    if value.grant.status == "expired":
        return MCPEffectiveResult(False, "grant_expired", "missing")
    if value.grant.scope != value.credential_policy:
        return MCPEffectiveResult(False, _missing_grant_reason(value.credential_policy), "missing")
    if value.discovery_status == "error":
        return MCPEffectiveResult(False, "discovery_failed", "missing")
    return MCPEffectiveResult(True, "usable", "available")
```

- [ ] **Step 4: Rewire `_collect_rows`** (effective.py:239): construct `MCPTemplateSettingsRepository` (new constructor param `settings_repo`, threaded through `cubebox/mcp/dependencies.py` — grep `MCPEffectiveConnectorService(` for all construction sites and add the arg), load `disabled_ids = await settings_repo.disabled_template_ids()` once, and per connector derive:

```python
            template = templates_by_id[connector.template_id]  # template_id now non-null
            methods = set(template.supported_auth_methods or [])
            auth_required = bool(methods - {"none"})
            oauth_supported = "oauth" in methods
            org_disabled = template.id in disabled_ids
```

`_resolve_grant`: refresh condition becomes `grant.auth_method == "oauth"` (drop the connector check). `_credential_availability_by_scope`: replace `connector.auth_method == "none"` with `not auth_required` (pass the bool in). `list_runtime_specs`: `auth_method=row.grant.auth_method if row.grant is not None else "none"`.

- [ ] **Step 5: Fix the two remaining readers.** `mcp_discovery.py`: `_build_runtime_spec_for_discovery(install=..., grant=...)` sets `auth_method` from the grant the same way (line ~213); the usability guard at ~359 (`install.auth_method == "none" or ...`) re-derives from the template (the function already has session access — load the template by `install.template_id`). `oauth/start.py:143`: replace `if install.auth_method != "oauth"` with a template lookup asserting `"oauth" in template.supported_auth_methods`, error code `auth_method_not_supported_by_template`. Find the OAuth callback's grant-creation site (`grep -rn "MCPCredentialGrant(" backend/cubebox/mcp backend/cubebox/api`) and set `auth_method="oauth"` there; `create_static_grant` in `mcp_installs.py` sets `auth_method="static"` (formal change lands in Task 7, add the field now to keep the model NOT NULL satisfied).

- [ ] **Step 6: Green + commit**

```bash
uv run pytest tests/unit/test_mcp_effective_state.py tests/e2e/test_mcp_four_layer_runtime.py --no-cov 2>&1 | tee ../tmp/task6.log | tail -3
git add backend/cubebox backend/tests
git commit -m "feat(mcp): org-disable veto and grant-level auth in effective/runtime derivation"
```

(Some runtime e2e tests will still reference deleted install routes — mark the specific route-dependent ones for rewrite in Tasks 9-10 rather than patching them here; the journal must list which.)

---

### Task 7: Connector service — ensure / distribute / purge; bootstrap filter

**Files:**
- Modify: `backend/cubebox/services/mcp_installs.py`
- Modify: `backend/cubebox/mcp/workspace_bootstrap.py`
- Test: `backend/tests/e2e/test_mcp_distribute_and_purge.py`

**Interfaces:**
- `MCPConnectorService` (constructor unchanged) **replaces** `create_from_template_for_org`, `create_from_template_for_workspace`, `create_custom_install_for_org`, `promote_workspace_install_to_org`, `_resolve_distribution`, `_fan_out_state_rows`, `uninstall` with:
  - `ensure_connector(template: MCPConnectorTemplate) -> MCPConnector` — delegates to `get_or_create_for_template(created_by_user_id=self._actor_user_id)`.
  - `distribute(template: MCPConnectorTemplate, *, enable_existing: bool, auto_enroll: bool) -> MCPConnector` — ensure connector; when `enable_existing`, insert `enabled=True, enablement_source='admin_auto'` state rows **only for workspaces with no existing state row** (spec §5: existing rows, including explicit `enabled=False`, are never touched); set `auto_enroll_new_workspaces = auto_enroll` on the connector.
  - `purge(template_id: str) -> None` — resolve active connector by template; delete all its grants, all its state rows, then the connector row (hard delete; project unreleased). No-op `ValueError("mcp_install_not_found")` if no connector exists.
  - `create_static_grant(...)` gains `auth_method="static"` on the created row (from Task 6 step 5).
  - `set_workspace_enabled(template: MCPConnectorTemplate, *, workspace_id: str, enabled: bool, credential_policy: str | None) -> MCPWorkspaceConnectorState` — the lazy enable path used by the workspace route: ensure connector, upsert this workspace's state (`enablement_source='workspace_manual'`).
- `enroll_workspace_in_org_wide_mcp` additionally skips connectors whose template is org-disabled (join `disabled_template_ids()`).

- [ ] **Step 1: Failing e2e tests** (service-level, direct sessions via the Task 4 test's fixtures):

```python
async def test_distribute_inserts_only_missing_state_rows():
    # ws1 has explicit enabled=False row, ws2 has none.
    # distribute(enable_existing=True, auto_enroll=True)
    # -> ws1 row untouched (enabled False, source unchanged),
    #    ws2 row created (enabled True, source 'admin_auto'),
    #    connector.auto_enroll_new_workspaces is True.

async def test_purge_deletes_connector_grants_states_keeps_template(): ...
async def test_lazy_enable_from_template_creates_connector_and_state(): ...
    # set_workspace_enabled on a template with no connector; second workspace
    # enabling reuses the same connector id.        (spec test #1)

async def test_bootstrap_skips_org_disabled_templates(): ...
    # auto_enroll connector whose template is disabled -> new workspace gets no state row.
```

- [ ] **Step 2: Red** → **Step 3: Implement** (distribute core):

```python
    async def distribute(
        self,
        template: MCPConnectorTemplate,
        *,
        enable_existing: bool,
        auto_enroll: bool,
    ) -> MCPConnector:
        if self._workspace_repo is None:
            raise RuntimeError("distribute requires workspace_repo")
        connector = await self._connector_repo.get_or_create_for_template(
            template, created_by_user_id=self._actor_user_id
        )
        if enable_existing:
            existing_rows = await self._state_repo.list_for_install(connector.id)
            already = {row.workspace_id for row in existing_rows}
            for ws in await self._workspace_repo.list_for_org(self._org_id):
                if ws.id in already:
                    continue
                await self._state_repo.upsert_for_connector(
                    workspace_id=ws.id,
                    connector_id=connector.id,
                    enabled=True,
                    credential_policy=connector.default_credential_policy,
                    enablement_source="admin_auto",
                    updated_by_user_id=self._actor_user_id,
                )
        connector.auto_enroll_new_workspaces = auto_enroll
        return await self._connector_repo.update(connector)
```

Delete the superseded methods and the now-dead `install_defaults_for_auth_method` if nothing else imports it (`grep -rn install_defaults_for_auth_method backend/cubebox`). Delete `MCPConnector.install_state` property once `_install_to_out` (its last caller) dies in Task 9 — leave a journal note if it must wait.

- [ ] **Step 4: Green + commit**

```bash
uv run pytest tests/e2e/test_mcp_distribute_and_purge.py --no-cov 2>&1 | tee ../tmp/task7.log | tail -3
git add backend/cubebox backend/tests
git commit -m "feat(mcp): distribute/purge/lazy-enable service ops; bootstrap honors org disable"
```

---

### Task 8: API schemas — `mcp_catalog.py`; prune `mcp.py`

**Files:**
- Create: `backend/cubebox/api/schemas/mcp_catalog.py`
- Modify: `backend/cubebox/api/schemas/mcp.py`
- Delete: `backend/cubebox/api/schemas/mcp_admin_connector.py`, `backend/cubebox/api/schemas/mcp_ws_available.py`

**Interfaces (produced — routes in Tasks 9-10 return exactly these):**

```python
class MCPTemplateOut(BaseModel):
    template_id: str
    slug: str
    name: str
    provider: str
    description: str
    scope: Literal["global", "org", "workspace"]
    workspace_id: str | None
    server_url: str
    transport: str
    supported_auth_methods: list[str]
    default_credential_policy: str
    status: str

class MCPConnectorFactsOut(BaseModel):
    connector_id: str
    default_credential_policy: str
    discovery_status: str
    tool_count: int
    tools: list[MCPToolEntry]            # reuse from schemas/mcp.py
    tool_citations: dict[str, dict[str, Any]]
    last_error: str | None
    auto_enroll_new_workspaces: bool

class AdminCatalogRowOut(BaseModel):
    template: MCPTemplateOut
    connector: MCPConnectorFactsOut | None
    disabled: bool
    in_use: bool
    needs_attention: bool
    enabled_workspace_count: int
    eligible_workspace_count: int
    org_grant_status: Literal["valid", "expired"] | None

class AdminCatalogListOut(BaseModel):
    items: list[AdminCatalogRowOut]

class WorkspaceCatalogRowOut(BaseModel):
    template: MCPTemplateOut
    connector: MCPConnectorFactsOut | None
    enabled: bool
    usable: bool | None                  # None when no connector/state yet
    reason: str | None
    credential_availability_by_scope: dict[Literal["org", "workspace", "user"], bool]

class WorkspaceCatalogListOut(BaseModel):
    items: list[WorkspaceCatalogRowOut]

class CreateTemplateIn(BaseModel):       # admin org-custom AND ws-custom share it
    name: str
    server_url: str
    transport: Literal["streamable_http", "sse"]
    auth_method: Literal["oauth", "static", "none"]
    default_credential_policy: Literal["org", "workspace", "user", "none"]
    # pairing rule enforced by validator: policy 'none' <=> auth 'none'

class DistributeIn(BaseModel):
    enable_existing: bool = True
    auto_enroll: bool = True

class TemplateStateIn(BaseModel):        # ws enable/disable
    enabled: bool
    credential_policy: Literal["org", "workspace", "user"] | None = None
```

- [ ] **Step 1: Write the module** (all of the above, plus move the pairing validator from `AdminCreateInstallIn` into `CreateTemplateIn`).
- [ ] **Step 2: Prune `schemas/mcp.py`** — delete `AdminCreateInstallIn`, `PromoteInstallIn`, the distribution/auto-enable models, `MCPAdminInstallEffectiveOut`; keep grant/invoke/test-connection/refresh/tool-citation models. `CreateGrantIn` needs no `auth_method` field: org grants via `credential_plaintext` are static by definition; OAuth grants are minted by the callback.
- [ ] **Step 3: `uv run mypy cubebox/api/schemas 2>&1 | tail -3`** — clean. Commit:

```bash
git add backend/cubebox/api/schemas
git commit -m "feat(mcp): catalog API schemas; retire install/promote/available schemas"
```

---

### Task 9: Admin routes rewrite

**Files:**
- Modify: `backend/cubebox/api/routes/v1/admin_mcp.py`
- Modify: `backend/cubebox/mcp/dependencies.py` (settings-repo dependency)
- Test: `backend/tests/e2e/test_mcp_admin_catalog_routes.py`

**Interfaces (produced — the frontend consumes exactly these paths):**

| method + path | body | returns |
|---|---|---|
| `GET /admin/mcp/catalog` | — | `AdminCatalogListOut` |
| `POST /admin/mcp/templates` | `CreateTemplateIn` | `MCPTemplateOut` (201) |
| `DELETE /admin/mcp/templates/{template_id}` | — | 204 (org-owned only; 409 `template_in_use` if an active connector exists) |
| `PUT /admin/mcp/templates/{template_id}/disable` | — | 204 |
| `DELETE /admin/mcp/templates/{template_id}/disable` | — | 204 |
| `POST /admin/mcp/templates/{template_id}/distribute` | `DistributeIn` | `AdminCatalogRowOut` |
| `POST /admin/mcp/templates/{template_id}/purge` | — | 204 |

**Removed** (delete handlers + their tests' route calls): `POST /admin/mcp/installs`, `POST .../promote-to-org`, `GET /admin/mcp/connectors`, `GET .../installs/{id}/effective`, the module-level `_derive_admin_org_effective`, `_install_to_out`, `_template_to_out` install-summary path. **Kept as-is** (they key off `connector_id`, which catalog rows expose via `connector.connector_id`): grants create/delete + oauth start, `refresh-discovery`, tool invoke, `test-connection`, `tool-citations`, `PATCH /installs/{id}` reduced to `name`/`headers`/`default_credential_policy` (strip `auth_method`, `auto_enroll_new_workspaces`, `server_url`, `transport` — server config now belongs to the template). `GET /mcp/templates` (public list) is deleted; the workspace catalog replaces it.

- [ ] **Step 1: Failing e2e tests** (HTTP surface, `admin_client` fixture from `tests/e2e/conftest.py:648`):

```python
async def test_catalog_lists_every_visible_template_with_facts(): ...
    # seeded global template appears with connector=None; after distribute it
    # appears with in_use=True and enabled_workspace_count == ws count.
async def test_distribute_does_not_resurrect_explicitly_disabled_workspace(): ...   # spec test #2
async def test_disable_hides_from_workspace_and_rejects_enable(): ...               # spec test #3 (route half)
async def test_purge_then_reenable_from_zero(): ...                                 # spec test #5
async def test_create_org_template_and_grant_flow(): ...
    # POST templates (static/org policy) -> distribute -> POST grants/org with
    # plaintext -> catalog row org_grant_status == 'valid'.
async def test_admin_catalog_needs_attention_on_expired_grant(): ...                # spec test #7
```

- [ ] **Step 2: Red** → **Step 3: Implement.** The catalog handler is assembly only (route stays thin):

```python
@router.get("/catalog", response_model=AdminCatalogListOut)
async def admin_catalog(
    session: Annotated[AsyncSession, Depends(get_session)],
    grant_repo: Annotated[MCPCredentialGrantRepository, Depends(get_grant_repo)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> AdminCatalogListOut:
    template_repo = MCPConnectorTemplateRepository(session)
    connector_repo = MCPConnectorRepository(session, org_id=ctx.org_id)
    state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=ctx.org_id)
    settings_repo = MCPTemplateSettingsRepository(session, org_id=ctx.org_id)
    workspace_repo = WorkspaceRepository(session)

    templates = await template_repo.list_visible_for_org(ctx.org_id)
    connectors = await connector_repo.list_active()
    connectors_by_template = {c.template_id: c for c in connectors}
    enabled_counts: dict[str, int] = {}
    org_grants: dict[str, Any] = {}
    for connector in connectors:
        rows = await state_repo.list_for_install(connector.id)
        enabled_counts[connector.id] = sum(1 for r in rows if r.enabled)
        org_grants[connector.id] = await grant_repo.get_org_grant(connector.id)
    rows = build_admin_catalog_rows(
        templates=templates,
        connectors_by_template_id=connectors_by_template,
        disabled_template_ids=await settings_repo.disabled_template_ids(),
        enabled_counts_by_connector_id=enabled_counts,
        org_grants_by_connector_id=org_grants,
        eligible_workspace_count=len(await workspace_repo.list_for_org(ctx.org_id)),
    )
    return AdminCatalogListOut(items=[_row_to_out(r) for r in rows])
```

`_row_to_out` maps `AdminCatalogRow` → `AdminCatalogRowOut` (`org_grant_status`: grant None → None, `grant_status=='expired'` → `'expired'`, else `'valid'`). Distribute/disable/purge handlers delegate to Task 7 service + Task 4 settings repo, each with an `audit.record` (`mcp.template.distributed` / `mcp.template.disabled` / `mcp.template.purged` / `mcp.template.created`).

- [ ] **Step 4: Update the stale e2e route tests** identified in the Task 6 journal (`test_mcp_four_layer_routes.py` etc.): rewrite scenarios onto the new surface (install-create calls become template + distribute/enable calls) — the *invariants* those tests protect stay, only the setup surface changes. Delete tests whose sole subject was the removed surface (install uniqueness of custom installs → now template-level; promote-install; available-list composition).

- [ ] **Step 5: Green + commit**

```bash
uv run pytest tests/e2e/test_mcp_admin_catalog_routes.py tests/e2e -k "mcp" --no-cov 2>&1 | tee ../tmp/task9.log | tail -3
git add backend/cubebox backend/tests
git commit -m "feat(mcp): admin catalog/template routes; retire install+promote surface"
```

---

### Task 10: Workspace routes rewrite

**Files:**
- Modify: `backend/cubebox/api/routes/v1/ws_mcp.py`
- Test: `backend/tests/e2e/test_mcp_ws_catalog_routes.py`

**Interfaces:**

| method + path | body | returns |
|---|---|---|
| `GET /ws/{ws}/mcp/catalog` | — | `WorkspaceCatalogListOut` |
| `PUT /ws/{ws}/mcp/templates/{template_id}/state` | `TemplateStateIn` | `WorkspaceCatalogRowOut` |
| `POST /ws/{ws}/mcp/templates` | `CreateTemplateIn` | `MCPTemplateOut` (201, `scope='workspace'`) |
| `POST /ws/{ws}/mcp/templates/{template_id}/promote` | — | `MCPTemplateOut` |

Rules: catalog uses `list_visible_for_workspace` + `build_workspace_catalog_rows` (org-disabled excluded), enriched per-row with effective state where a connector+state exists (reuse `MCPEffectiveConnectorService` results keyed by connector id) and `credential_availability_by_scope`. `PUT .../state` is the lazy-enable path (`set_workspace_enabled` from Task 7); it 404s `template_not_visible` for templates outside the workspace's visibility and 409s `template_disabled_in_org` when disabled. Promote 404s unless the template's `workspace_id` equals the path workspace; ws-admin gated (same dependency the old `patch_workspace_connector_state` used). **Removed**: `POST /ws/{ws}/mcp/installs`, `DELETE .../installs/{id}`, `GET .../available`, `GET .../templates` (old list). **Kept**: effective connector list for chat, active-tools, all grant endpoints, ws refresh-discovery, invoke; the old `PATCH connectors/{id}/state` is deleted (the template-state PUT replaces it — update its callers in the frontend, Task 12).

- [ ] **Step 1: Failing e2e tests**:

```python
async def test_ws_catalog_shows_visible_templates_with_enabled_state(): ...
async def test_lazy_enable_creates_shared_connector(): ...                 # spec test #1 (HTTP half)
async def test_enable_rejected_when_org_disabled(): ...                    # spec test #3
async def test_ws_custom_template_invisible_to_sibling_workspace(): ...    # spec test #6 pre-promote
async def test_promote_makes_template_enableable_by_sibling(): ...         # spec test #6
async def test_mixed_grants_oauth_user_plus_static_workspace(): ...        # spec test #4
    # static ws grant via existing ws grant endpoint; oauth user grant seeded
    # directly via db_maker (auth_method='oauth'); both resolve in runtime specs.
```

- [ ] **Step 2: Red** → **Step 3: Implement**, mirroring the admin catalog assembly with the workspace visibility query and per-row effective enrichment. Workspace deletion cascade (spec §10, unpromoted ws templates purged with the workspace): find the workspace-deletion service (`grep -rn "def delete_workspace" backend/cubebox/services`) and add: purge connectors of, then delete, `scope='workspace'` templates owned by the deleted workspace.

- [ ] **Step 4: Green + commit**

```bash
uv run pytest tests/e2e/test_mcp_ws_catalog_routes.py --no-cov 2>&1 | tee ../tmp/task10.log | tail -3
git add backend/cubebox backend/tests
git commit -m "feat(mcp): workspace catalog + template state/create/promote routes"
```

---

### Task 11: Backend sweep

**Files:** whatever the sweep flags.

- [ ] **Step 1:** `grep -rn "install_scope\|list_workspace_installs\|mcp_ws_available\|mcp_admin_connectors\|AdminCreateInstallIn\|auth_status" backend/cubebox` — must return nothing (delete stragglers; `MCPConnector.install_state` property goes now if Task 7 deferred it).
- [ ] **Step 2:** Full backend gate:

```bash
cd backend && uv run mypy cubebox 2>&1 | tee ../tmp/task11-mypy.log | tail -3
uv run pytest --no-cov 2>&1 | tee ../tmp/task11-pytest.log | tail -3
```

Expected: both clean. Fix forward; re-run only the failing files first, full suite once at the end.
- [ ] **Step 3: Commit** `git commit -am "chore(mcp): backend sweep for template-centric cutover"`

---

### Task 12: Frontend — core client + admin page

**Files:**
- Modify: `frontend/packages/core/src/api/mcp.ts`
- Modify: `frontend/packages/web/app/admin/mcp/page.tsx`
- Create: `frontend/packages/web/components/mcp/MCPCatalogList.tsx`, `frontend/packages/web/components/mcp/MCPDistributeDialog.tsx`
- Modify: `frontend/packages/web/components/mcp/MCPToolbar.tsx` (filter chips), `MCPAdminDetailPanel.tsx` (actions)
- Delete: `MCPTemplateInstallPanel.tsx`, `MCPCustomCreatePanel.tsx` (replaced by a template-create form), `MCPPromoteDialog.tsx`, `AvailableConnectorRow.tsx`
- Test: reuse/extend existing component tests colocated in `components/mcp/` (business-flow Playwright lands in Task 13)

**Interfaces (core client — page code consumes these):**

```typescript
export type MCPTemplateScope = 'global' | 'org' | 'workspace'
export type AdminCatalogFilter = 'in_use' | 'needs_attention' | 'org_credential' | 'unused' | 'all'

export interface MCPTemplate { template_id: string; slug: string; name: string; provider: string; description: string; scope: MCPTemplateScope; workspace_id: string | null; server_url: string; transport: string; supported_auth_methods: string[]; default_credential_policy: string; status: string }
export interface MCPConnectorFacts { connector_id: string; default_credential_policy: string; discovery_status: string; tool_count: number; tools: MCPToolEntry[]; tool_citations: Record<string, Record<string, unknown>>; last_error: string | null; auto_enroll_new_workspaces: boolean }
export interface AdminCatalogRow { template: MCPTemplate; connector: MCPConnectorFacts | null; disabled: boolean; in_use: boolean; needs_attention: boolean; enabled_workspace_count: number; eligible_workspace_count: number; org_grant_status: 'valid' | 'expired' | null }
export interface WorkspaceCatalogRow { template: MCPTemplate; connector: MCPConnectorFacts | null; enabled: boolean; usable: boolean | null; reason: string | null; credential_availability_by_scope: Record<'org' | 'workspace' | 'user', boolean> }

export async function adminListCatalog(client: ApiClient): Promise<{ items: AdminCatalogRow[] }>
export async function adminCreateTemplate(client: ApiClient, body: CreateTemplateBody): Promise<MCPTemplate>
export async function adminDeleteTemplate(client: ApiClient, templateId: string): Promise<void>
export async function adminSetTemplateDisabled(client: ApiClient, templateId: string, disabled: boolean): Promise<void>   // PUT vs DELETE …/disable
export async function adminDistribute(client: ApiClient, templateId: string, body: { enable_existing: boolean; auto_enroll: boolean }): Promise<AdminCatalogRow>
export async function adminPurgeTemplate(client: ApiClient, templateId: string): Promise<void>
export async function wsListCatalog(client: ApiClient, wsId: string): Promise<{ items: WorkspaceCatalogRow[] }>
export async function wsSetTemplateState(client: ApiClient, wsId: string, templateId: string, body: { enabled: boolean; credential_policy?: string }): Promise<WorkspaceCatalogRow>
export async function wsCreateTemplate(client: ApiClient, wsId: string, body: CreateTemplateBody): Promise<MCPTemplate>
export async function wsPromoteTemplate(client: ApiClient, wsId: string, templateId: string): Promise<MCPTemplate>
```

Delete from `mcp.ts`: `adminCreateInstall`, `wsCreateInstall`, `wsDeleteInstall`, `wsListAvailable`, `adminPromoteToOrg`, `adminListConnectors`, `adminGetInstallEffective`, `wsPatchConnectorState`, `PromoteDistribution`. Keep grant/invoke/discovery/test-connection functions (paths unchanged).

- [ ] **Step 1: Rewrite `mcp.ts`** to the interface above; `cd frontend && pnpm --filter @cubebox/core build 2>&1 | tee ../tmp/task12-core.log | tail -3` (core must build before web sees the types).
- [ ] **Step 2: Rework the admin page** (`app/admin/mcp/page.tsx`). Replace the two-section rail (lines 124-176: `installs` header + `MCPConnectorList` + `templates` section with install/custom buttons) with one `MCPCatalogList` fed by `adminListCatalog`, filtered client-side:

```typescript
const visible = useMemo(() => rows.filter((r) => {
  if (search && !r.template.name.toLowerCase().includes(search.toLowerCase())) return false
  switch (filter) {
    case 'in_use': return r.in_use
    case 'needs_attention': return r.needs_attention
    case 'org_credential': return r.org_grant_status !== null
    case 'unused': return !r.in_use
    default: return true
  }
}), [rows, search, filter])
```

Default filter `'in_use'`; source dropdown filters on `template.scope`. Row chips: `enabled_workspace_count/eligible_workspace_count`, org-credential dot (green valid / red expired), `disabled` badge, source badge (reuse `MCPScopeBadge`). "添加自定义连接器" opens the template-create form (fields = `CreateTemplateBody`; reuse the form internals salvaged from `MCPCustomCreatePanel` including `adminTestConnection`).
- [ ] **Step 3: Detail panel actions** (`MCPAdminDetailPanel`): selection is now an `AdminCatalogRow`. Actions: Distribute (opens `MCPDistributeDialog` — two checkboxes exactly as spec §5, both default checked, confirm calls `adminDistribute`), Disable/Enable toggle (`adminSetTemplateDisabled`), Purge (danger-zone confirm listing "移除所有工作区启用、删除全部凭证、清除连接器配置" — calls `adminPurgeTemplate`), org-grant band + Try It + citations unchanged (they key off `row.connector?.connector_id`; hidden when `connector` is null).
- [ ] **Step 4: Verify** — `pnpm --filter web lint && pnpm --filter web test 2>&1 | tee ../tmp/task12-web.log | tail -3`; then `pnpm build` at repo root. Commit `feat(mcp-web): admin catalog single list with filters, distribute/disable/purge`.

---

### Task 13: Frontend — workspace page + i18n + Playwright flow

**Files:**
- Modify: the workspace MCP settings page — locate with `grep -rln "wsListAvailable\|wsListEffectiveConnectors" frontend/packages/web/app` (expected under `app/(app)/w/[wsId]/settings/…`)
- Modify: `frontend/packages/web/messages/en.json`, `zh.json` (the `mcpAdmin`/`mcpWorkspace` blocks around lines 1834+)
- Test: Playwright business-flow spec under the existing frontend e2e directory (`grep -rln "mcp" frontend/packages/web/e2e` for placement)

- [ ] **Step 1: Workspace page → single list** from `wsListCatalog`: each row shows template name/source badge/enabled switch; toggle calls `wsSetTemplateState`; rows with `usable === false` show the `reason` copy; credential band unchanged (keys off `connector.connector_id`). "登记自定义连接器" → `wsCreateTemplate`; own `scope='workspace'` rows get a "提交为组织模板" button → `wsPromoteTemplate`. Delete the old available-section components.
- [ ] **Step 2: i18n.** Remove keys: `distributionLabel/None/All/*Hint`, install/promote strings. Add (en + zh, zh shown):

```json
"filterInUse": "使用中", "filterNeedsAttention": "需要处理", "filterOrgCredential": "组织凭证",
"filterUnused": "未使用", "filterAll": "全部",
"distributeTitle": "为工作区启用", "distributeExisting": "为尚未设置的 {count} 个工作区启用",
"distributeAutoEnroll": "新建工作区自动启用",
"disableAction": "在本组织禁用", "enableAction": "恢复启用",
"purgeAction": "清除所有使用", "purgeConfirmBody": "将移除所有工作区的启用、删除全部凭证、清除连接器配置。模板保留在目录中。",
"sourceGlobal": "官方目录", "sourceOrg": "组织自定义", "sourceWorkspace": "工作区提交",
"promoteTemplateAction": "提交为组织模板", "registerCustomTemplate": "登记自定义连接器",
"disabledBadge": "已禁用", "reasonTemplateDisabled": "已被组织禁用"
```

- [ ] **Step 3: One Playwright business flow** (real backend, per `docs/testing.md` — business invariant, not DOM counting): admin creates a static org-custom template → distribute (both boxes) → workspace page shows it enabled → admin disables → workspace page no longer lists it and its tools drop from active tools. 
- [ ] **Step 4: Verify + commit** — `pnpm lint && pnpm build && npx playwright test <new spec> 2>&1 | tee ../tmp/task13.log | tail -3`; commit `feat(mcp-web): workspace catalog list, template promote, i18n`.

---

### Task 14: User docs (same-PR requirement)

**Files:** the connector pages under `docs/site/docs/` — find via the code-area→page mapping in `docs/dev/plans/2026-06-23-docs-overhaul.md`.

- [ ] **Step 1:** Rewrite the admin connectors page around: catalog + filters, distribute dialog (two checkboxes), disable vs purge, org-custom template creation. Rewrite the workspace page around: single list, enable toggle, custom template registration, promote. Use plain language ("管理员从目录安装 → 工作区决定用不用 → 用的人连上自己的凭证" framing from the spec). Screenshot placeholders per the overhaul plan's placeholder format wherever captures are missing.
- [ ] **Step 2:** Commit `docs: rewrite MCP connector pages for template-centric flow`.

---

### Task 15: Final sweep + PR

- [ ] **Step 1:** Full gates, in order, all through tee:

```bash
cd backend && uv run mypy cubebox 2>&1 | tee ../tmp/final-mypy.log | tail -3
uv run pytest --no-cov 2>&1 | tee ../tmp/final-pytest.log | tail -3
cd ../frontend && pnpm lint 2>&1 | tee ../tmp/final-lint.log | tail -3 && pnpm build 2>&1 | tee ../tmp/final-build.log | tail -3
```

- [ ] **Step 2:** `/verification-before-completion` — paste the four tails as evidence.
- [ ] **Step 3:** Cross-check the spec's §11 invariant list against the tests written in Tasks 7/9/10/13 — every one of the 7 must map to a named test; list the mapping in the PR body.
- [ ] **Step 4:** Open the PR (title: a brief description, no prefixes) and run the `/pr-codex-review-loop` skill.

---

## Self-Review Notes

- **Spec coverage:** §3.1→T2/T4, §3.2→T2/T4, §3.3→T2/T6, §3.4→T2/T4, §4→T5/T9/T10/T12/T13, §5→T7/T9/T10/T12, §6→T6, §7→T8/T9/T10/T12, §8→T3, §10 slug collision→T4 (`connector_name_conflict`), §10 ws-deletion cascade→T10 step 3, §11→T15 step 3 mapping. §9 items deliberately absent.
- **Known judgment calls encoded above:** PATCH install keeps only name/headers/policy; `CreateGrantIn` carries no `auth_method` (derivable per flow); old ws state-PATCH endpoint replaced by template-state PUT.
- **Type consistency:** catalog row field names are identical across `mcp_catalog.py` (Task 5), `schemas/mcp_catalog.py` (Task 8), and `mcp.ts` (Task 12) — reviewers should diff those three blocks first.
