# MCP Tool Citations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thread per-tool citation mapping from a DB-backed catalog through install + namespacing into the agent runtime so MCP tool results emit chunked citations.

**Architecture:** Catalog row carries default `tool_citations`; install snapshots the dict onto the `mcp_servers` row; per-run loader namespaces tool names (`{server}__{tool}`) and emits a `dict[namespaced_name, CitationConfig]` for `CitationMiddleware`. Frontend gets a guided editor on the MCP server detail page.

**Tech Stack:** SQLModel + Alembic, FastAPI, pydantic, cubepi runtime, Next.js + Zustand + shadcn-ui, Playwright.

**Spec:** `docs/superpowers/specs/2026-05-14-mcp-tool-citations-design.md`

**Conventions:**

- Backend commands run from `backend/`. Use `uv run pytest -q tests/...` for individual files, `make check` only before opening a PR.
- Frontend commands run from `frontend/`. Use `pnpm --filter @cubeplex/web …`.
- Commits keep one logical change per task. Plan tasks correspond ~1:1 to commits unless noted.

---

## File Structure

**Backend — new files:**

- `backend/alembic/versions/<ts>_add_tool_citations_to_mcp_tables.py`
- `backend/tests/unit/mcp/test_namespace_and_citations.py`
- `backend/tests/unit/mcp/test_tool_citations_routes.py` (or extend existing ws_mcp test file)
- `backend/tests/e2e/test_mcp_tool_citations.py`

**Backend — modified files:**

- `backend/cubeplex/models/mcp.py` — add `tool_citations` column on `MCPCatalogConnector` and `MCPServer`
- `backend/cubeplex/middleware/citations/config.py` — add `content_type` to `CitationConfig`
- `backend/cubeplex/mcp/catalog_seed.py` — `CatalogSeedEntry.tool_citations` field + seed for webtools
- `backend/cubeplex/repositories/mcp_catalog.py` — `upsert_by_slug` accepts `tool_citations`
- `backend/cubeplex/services/mcp_catalog.py` — install copies `catalog.tool_citations` onto new `MCPServer` row
- `backend/cubeplex/mcp/cubepi_admin_refresh.py` — strip orphan keys after discovery rewrite
- `backend/cubeplex/mcp/cubepi_discovery.py` — `CubepiMCPServerSpec.tool_citations` field; populate
- `backend/cubeplex/mcp/cubepi_runtime.py` — namespacing + return citation_configs
- `backend/cubeplex/streams/run_manager.py` — wire citation_configs into `CitationMiddleware`
- `backend/cubeplex/api/routes/v1/ws_mcp.py` — GET/PATCH server tool-citations
- `backend/cubeplex/api/routes/v1/mcp_catalog.py` — GET catalog tool-citations
- `backend/tests/unit/test_catalog_seed.py` — extend for the new field

**Frontend — new files:**

- `frontend/packages/web/components/mcp/MCPCitationMappingTab.tsx`
- `frontend/packages/web/components/mcp/MCPCitationEditor.tsx`
- `frontend/packages/web/components/mcp/MCPCitationFieldRow.tsx`
- `frontend/packages/web/__tests__/e2e/mcp/citation-mapping.spec.ts`

**Frontend — modified files:**

- `frontend/packages/core/src/types/mcp.ts` — `CitationConfigJSON`, `ToolCitationsResponse`
- `frontend/packages/core/src/api/mcp.ts` — three new client methods
- `frontend/packages/web/components/mcp/MCPServerDetail.tsx` — register new tab
- Citation chip component (located at runtime — search for the place that renders citation `tool_name`)
- `frontend/packages/web/messages/{en,zh}.json` — `mcp.serverDetail.citations.*`

---

## Task 1: Schema migration + model columns + CitationConfig.content_type

**Files:**

- Create: `backend/alembic/versions/<ts>_add_tool_citations_to_mcp_tables.py`
- Modify: `backend/cubeplex/models/mcp.py` (add columns on two models)
- Modify: `backend/cubeplex/middleware/citations/config.py:34` (add `content_type` field)
- Test: `backend/tests/unit/test_citation.py` (extend with content_type round-trip)

- [ ] **Step 1: Add `content_type` field to `CitationConfig`**

Edit `backend/cubeplex/middleware/citations/config.py` — add the field above `source_type`:

```python
from typing import Any, Literal

...

class CitationConfig(BaseModel):
    """Per-tool citation configuration.

    Attributes:
        content_type: How the tool output is encoded. "json" runs the
                      response through JSON parsing; "text" treats it as
                      a single text blob (used by e.g. web_fetch).
        source_type: Citation source type (e.g., "web", "file").
        ...
    """

    content_type: Literal["json", "text"] = "json"
    source_type: str
    content_field: str | None
    mapping: dict[str, str]
    args_mapping: dict[str, str] | None = None
    discriminator_field: str | None = None
    discriminator_values: list[str] | None = None
```

- [ ] **Step 2: Add the failing pydantic test**

Append to `backend/tests/unit/test_citation.py`:

```python
def test_citation_config_content_type_defaults_to_json() -> None:
    from cubeplex.middleware.citations.config import CitationConfig

    cfg = CitationConfig(source_type="web", content_field="results", mapping={"snippet": "snippet"})
    assert cfg.content_type == "json"


def test_citation_config_content_type_text_round_trip() -> None:
    from cubeplex.middleware.citations.config import CitationConfig

    raw = {
        "content_type": "text",
        "source_type": "web",
        "content_field": None,
        "mapping": {"snippet": "text"},
    }
    cfg = CitationConfig(**raw)
    assert cfg.content_type == "text"
    assert cfg.model_dump(exclude_none=True)["content_type"] == "text"


def test_citation_config_rejects_unknown_content_type() -> None:
    import pytest
    from pydantic import ValidationError

    from cubeplex.middleware.citations.config import CitationConfig

    with pytest.raises(ValidationError):
        CitationConfig(content_type="binary", source_type="web", content_field=None, mapping={})  # type: ignore[arg-type]
```

- [ ] **Step 3: Run tests to confirm they pass**

Run: `cd backend && uv run pytest -q tests/unit/test_citation.py::test_citation_config_content_type_defaults_to_json tests/unit/test_citation.py::test_citation_config_content_type_text_round_trip tests/unit/test_citation.py::test_citation_config_rejects_unknown_content_type`

Expected: 3 passed.

- [ ] **Step 4: Add `tool_citations` column to both MCP models**

Edit `backend/cubeplex/models/mcp.py` — inside `MCPCatalogConnector`, just before `status: str = ...`:

```python
    tool_citations: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default=text("'{}'")),
    )
```

Inside `MCPServer`, just after `tools_cache: list[dict[str, Any]] = ...`:

```python
    tool_citations: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default=text("'{}'")),
    )
```

- [ ] **Step 5: Auto-generate the alembic migration**

Run: `cd backend && uv run alembic revision --autogenerate -m "add tool_citations to mcp tables"`

Open the generated file (path printed by the command). Verify it contains exactly two `add_column` calls (one per table) and matching `drop_column` in `downgrade()`. If autogen also produced unrelated diffs, delete those lines (they were autogen noise).

- [ ] **Step 6: Apply the migration locally and confirm it round-trips**

```bash
cd backend
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
```

Each command should succeed without errors.

- [ ] **Step 7: Run the broader unit slice to catch regressions**

Run: `cd backend && uv run pytest -q tests/unit/test_citation.py tests/unit/test_catalog_seed.py`

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add backend/cubeplex/middleware/citations/config.py \
        backend/cubeplex/models/mcp.py \
        backend/alembic/versions/*add_tool_citations* \
        backend/tests/unit/test_citation.py
git commit -m "feat(mcp): add tool_citations columns + CitationConfig.content_type"
```

---

## Task 2: `CatalogSeedEntry.tool_citations` + repo `upsert_by_slug` plumbs it

**Files:**

- Modify: `backend/cubeplex/mcp/catalog_seed.py` — add field on dataclass; pass through in `seed_catalog`
- Modify: `backend/cubeplex/repositories/mcp_catalog.py:45-114` — accept `tool_citations`
- Modify: `backend/tests/unit/test_catalog_seed.py` — add roundtrip assertion

- [ ] **Step 1: Add the failing test**

Append to `backend/tests/unit/test_catalog_seed.py`:

```python
async def test_seed_persists_tool_citations(
    session: AsyncSession,
    backend: FernetBackend,
    env_for_all_static_oauth: dict[str, str],
) -> None:
    """tool_citations on a CatalogSeedEntry round-trips through upsert."""
    catalog = [
        CatalogSeedEntry(
            slug="webtools-test",
            name="WebTools Test",
            provider="Cubeplex",
            description="test entry",
            server_url="http://example.com/mcp",
            transport="streamable_http",
            supported_auth_methods=["static"],
            default_credential_scope="org",
            oauth_dcr_supported=None,
            oauth_default_scope=None,
            oauth_static_client_id_env=None,
            oauth_static_client_secret_env=None,
            static_form_fields=None,
            static_auth_header_template=None,
            cred_metadata={},
            tool_citations={
                "web_search": {
                    "content_type": "json",
                    "source_type": "web",
                    "content_field": "results",
                    "mapping": {"url": "url", "snippet": "description"},
                },
            },
        )
    ]
    result = await seed_catalog(
        session,
        backend,
        get_env=env_for_all_static_oauth.get,
        catalog=catalog,
    )
    assert result.skipped == 0
    repo = MCPCatalogConnectorRepository(session)
    row = await repo.get_by_slug("webtools-test")
    assert row is not None
    assert row.tool_citations == catalog[0].tool_citations
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `cd backend && uv run pytest -q tests/unit/test_catalog_seed.py::test_seed_persists_tool_citations`

Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'tool_citations'` (the dataclass doesn't have the field yet).

- [ ] **Step 3: Extend `CatalogSeedEntry`**

Edit `backend/cubeplex/mcp/catalog_seed.py:42` (the dataclass) — add the field after `cred_metadata`:

```python
@dataclass(frozen=True)
class CatalogSeedEntry:
    """One row in the static v1 catalog list."""

    slug: str
    name: str
    provider: str
    description: str
    server_url: str
    transport: Literal["streamable_http", "sse"]
    supported_auth_methods: list[str]
    default_credential_scope: Literal["org", "workspace", "user", "none"]
    oauth_dcr_supported: bool | None
    oauth_default_scope: str | None
    oauth_static_client_id_env: str | None
    oauth_static_client_secret_env: str | None
    static_form_fields: list[dict[str, Any]] | None
    static_auth_header_template: str | None
    cred_metadata: dict[str, Any] = field(default_factory=dict)
    tool_citations: dict[str, dict[str, Any]] = field(default_factory=dict)
```

- [ ] **Step 4: Extend `MCPCatalogConnectorRepository.upsert_by_slug`**

Edit `backend/cubeplex/repositories/mcp_catalog.py:45` — add the parameter and propagate in both the insert and update branches. The final signature and body:

```python
    async def upsert_by_slug(
        self,
        *,
        slug: str,
        name: str,
        description: str,
        provider: str,
        server_url: str,
        transport: str,
        supported_auth_methods: list[str],
        default_credential_scope: str,
        oauth_dcr_supported: bool | None = None,
        oauth_default_scope: str | None = None,
        oauth_static_client_id: str | None = None,
        oauth_static_client_secret_credential_id: str | None = None,
        static_form_fields: list[dict[str, Any]] | None = None,
        static_auth_header_template: str | None = None,
        cred_metadata: dict[str, Any] | None = None,
        tool_citations: dict[str, dict[str, Any]] | None = None,
        status: str = "active",
    ) -> MCPCatalogConnector:
```

Insert branch — add `tool_citations=tool_citations or {}` to the `MCPCatalogConnector(...)` constructor (just after `cred_metadata=...`).

Update branch — add a line after the existing `existing.cred_metadata = cred_metadata or {}`:

```python
existing.tool_citations = tool_citations or {}
```

- [ ] **Step 5: Pass the field through `seed_catalog`**

Edit `backend/cubeplex/mcp/catalog_seed.py` — find the `await repo.upsert_by_slug(...)` call inside `seed_catalog()` and add `tool_citations=dict(entry.tool_citations),` to the kwargs (after `cred_metadata=dict(entry.cred_metadata),`).

- [ ] **Step 6: Re-run the test**

Run: `cd backend && uv run pytest -q tests/unit/test_catalog_seed.py::test_seed_persists_tool_citations`

Expected: PASS.

- [ ] **Step 7: Run the full catalog-seed test file to confirm nothing else broke**

Run: `cd backend && uv run pytest -q tests/unit/test_catalog_seed.py`

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add backend/cubeplex/mcp/catalog_seed.py \
        backend/cubeplex/repositories/mcp_catalog.py \
        backend/tests/unit/test_catalog_seed.py
git commit -m "feat(mcp): plumb tool_citations through CatalogSeedEntry + repo upsert"
```

---

## Task 3: WebTools seed entry carries real `tool_citations` defaults

**Files:**

- Modify: `backend/cubeplex/mcp/catalog_seed.py` — the existing `webtools` `CatalogSeedEntry` (added in a prior PR)
- Modify: `backend/tests/unit/test_catalog_seed.py`

- [ ] **Step 1: Add the failing assertion**

Append to `backend/tests/unit/test_catalog_seed.py`:

```python
def test_webtools_entry_has_web_search_and_web_fetch_citations() -> None:
    """The webtools seed entry must carry citation mappings for both tools."""
    by_slug = {e.slug: e for e in CATALOG}
    assert "webtools" in by_slug
    entry = by_slug["webtools"]

    assert "web_search" in entry.tool_citations
    web_search = entry.tool_citations["web_search"]
    assert web_search["content_type"] == "json"
    assert web_search["source_type"] == "web"
    assert web_search["content_field"] == "results"
    assert web_search["mapping"]["snippet"] in {"description", "snippet"}

    assert "web_fetch" in entry.tool_citations
    web_fetch = entry.tool_citations["web_fetch"]
    assert web_fetch["content_type"] == "text"
    assert web_fetch["content_field"] is None
    assert web_fetch["mapping"]["snippet"] in {"text", "content"}
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `cd backend && uv run pytest -q tests/unit/test_catalog_seed.py::test_webtools_entry_has_web_search_and_web_fetch_citations`

Expected: FAIL with `KeyError: 'web_search'`.

- [ ] **Step 3: Fill in the webtools entry's `tool_citations`**

Edit the existing `CatalogSeedEntry(slug="webtools", ...)` in `backend/cubeplex/mcp/catalog_seed.py` — replace `cred_metadata={},` (the last field) with:

```python
        cred_metadata={},
        tool_citations={
            "web_search": {
                "content_type": "json",
                "source_type": "web",
                "content_field": "results",
                "mapping": {
                    "url": "url",
                    "title": "title",
                    "snippet": "description",
                },
            },
            "web_fetch": {
                "content_type": "text",
                "source_type": "web",
                "content_field": None,
                "mapping": {"snippet": "text"},
                "args_mapping": {"url": "url"},
            },
        },
```

- [ ] **Step 4: Re-run test**

Run: `cd backend && uv run pytest -q tests/unit/test_catalog_seed.py::test_webtools_entry_has_web_search_and_web_fetch_citations`

Expected: PASS.

- [ ] **Step 5: Validate each entry against the `CitationConfig` pydantic model**

Add another guard test to `backend/tests/unit/test_catalog_seed.py`:

```python
def test_all_seed_tool_citations_are_valid_citation_configs() -> None:
    """Every tool_citations entry across CATALOG must be a valid CitationConfig."""
    from cubeplex.middleware.citations.config import CitationConfig

    for entry in CATALOG:
        for tool_name, raw in entry.tool_citations.items():
            try:
                CitationConfig(**raw)
            except Exception as exc:  # noqa: BLE001
                pytest.fail(f"{entry.slug}.{tool_name}: invalid CitationConfig — {exc}")
```

Run: `cd backend && uv run pytest -q tests/unit/test_catalog_seed.py`

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/mcp/catalog_seed.py backend/tests/unit/test_catalog_seed.py
git commit -m "feat(mcp): seed webtools catalog with web_search/web_fetch citations"
```

---

## Task 4: Install copies `catalog.tool_citations` → `MCPServer.tool_citations`

**Files:**

- Modify: `backend/cubeplex/services/mcp_catalog.py:399` and `:448` (the two `MCPServer(...)` constructors inside install paths)
- Test: `backend/tests/unit/test_mcp_service_invariants.py` (extend) or new file `backend/tests/unit/test_install_tool_citations.py`

- [ ] **Step 1: Add the failing test**

Create `backend/tests/unit/test_install_tool_citations.py`:

```python
"""Install paths copy catalog.tool_citations into new MCPServer rows."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.asyncio
async def test_install_for_org_copies_tool_citations(
    seeded_workspace: Any,  # provides ctx, session, install service
) -> None:
    from cubeplex.models import MCPCatalogConnector

    ctx = seeded_workspace.ctx
    session = seeded_workspace.session
    svc = seeded_workspace.install_service

    catalog = MCPCatalogConnector(
        slug="webtools-install-test",
        name="WT",
        provider="x",
        description="x",
        server_url="http://example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["static"],
        default_credential_scope="org",
        tool_citations={"foo": {"source_type": "web", "content_field": None, "mapping": {}}},
        cred_metadata={},
    )
    session.add(catalog)
    await session.flush()

    result = await svc.install_for_org(
        connector_id=catalog.id,
        auth_method="static",
        credential_plaintext="t",
    )

    assert result.server.tool_citations == catalog.tool_citations


@pytest.mark.asyncio
async def test_install_for_workspace_user_copies_tool_citations(
    seeded_workspace: Any,
) -> None:
    # Same as above but use install_for_workspace_user; assert the new row's
    # tool_citations matches the catalog row's.
    ...
```

> Note: the engineer should reuse whatever existing fixture `MCPCatalogService` install tests already use; if no fixture exists, port the smallest viable harness from `tests/e2e/` or create one in `conftest.py`. The fixture name `seeded_workspace` is illustrative; align with what's already there.

- [ ] **Step 2: Run test to confirm it fails**

Run: `cd backend && uv run pytest -q tests/unit/test_install_tool_citations.py`

Expected: FAIL (either `AssertionError: ... != ...` or `AttributeError: ... no attribute 'tool_citations'` if step 3 not done).

- [ ] **Step 3: Copy `connector.tool_citations` into both `MCPServer(...)` constructors**

In `backend/cubeplex/services/mcp_catalog.py`, find both `MCPServer(` literals (around lines 399 and 448) and add this kwarg before `created_by_user_id`:

```python
                tool_citations=dict(connector.tool_citations or {}),
```

- [ ] **Step 4: Re-run the test**

Run: `cd backend && uv run pytest -q tests/unit/test_install_tool_citations.py`

Expected: PASS.

- [ ] **Step 5: Run the broader install slice to catch regressions**

Run: `cd backend && uv run pytest -q tests/unit/test_mcp_service_invariants.py tests/unit/test_install_tool_citations.py`

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/services/mcp_catalog.py \
        backend/tests/unit/test_install_tool_citations.py
git commit -m "feat(mcp): copy catalog tool_citations into new MCPServer rows on install"
```

---

## Task 5: Admin refresh strips orphan citation keys

**Files:**

- Modify: `backend/cubeplex/mcp/cubepi_admin_refresh.py`
- Modify: `backend/tests/unit/test_cubepi_admin_refresh.py`

- [ ] **Step 1: Add the failing test**

Append to `backend/tests/unit/test_cubepi_admin_refresh.py`:

```python
async def test_refresh_strips_orphan_citation_keys(
    session: AsyncSession,
) -> None:
    """After refresh, tool_citations keys whose tools vanished are removed."""
    from cubeplex.mcp.cubepi_admin_refresh import refresh_server_tools

    server = MCPServer(
        org_id="org-x",
        owner_workspace_id=None,
        name="webtools",
        server_url="http://example.com/mcp",
        server_url_hash="hash",
        transport="streamable_http",
        auth_method="none",
        credential_scope="none",
        tools_cache=[
            {"name": "web_search", "description": "", "input_schema": {}},
            {"name": "old_tool",   "description": "", "input_schema": {}},
        ],
        tool_citations={
            "web_search": {"source_type": "web", "content_field": None, "mapping": {}},
            "old_tool":   {"source_type": "web", "content_field": None, "mapping": {}},
        },
        created_by_user_id="usr-x",
    )
    session.add(server)
    await session.flush()

    # Simulate a refresh that returns only web_search.
    async def fake_discover(_: MCPServer, **_kw: Any) -> tuple[bool, list[dict[str, Any]], None]:
        return True, [{"name": "web_search", "description": "", "input_schema": {}}], None

    await refresh_server_tools(
        server=server,
        server_repo=MCPServerRepository(session, org_id="org-x"),
        credential_or_token=None,
        discover=fake_discover,  # injection point — see note in step 3
    )

    refreshed = await MCPServerRepository(session, org_id="org-x").get_by_id(server.id)
    assert refreshed is not None
    assert refreshed.tool_citations == {
        "web_search": {"source_type": "web", "content_field": None, "mapping": {}}
    }
    assert "Removed citation mapping for vanished tools: ['old_tool']" in (refreshed.last_error or "")
```

> If `refresh_server_tools` doesn't take a `discover=` injection point today, also add a `_discover: Callable | None = None` keyword to it as part of this task. The test is the more important contract.

- [ ] **Step 2: Run test to confirm it fails**

Run: `cd backend && uv run pytest -q tests/unit/test_cubepi_admin_refresh.py::test_refresh_strips_orphan_citation_keys`

Expected: FAIL.

- [ ] **Step 3: Implement orphan cleanup in `refresh_server_tools`**

In `backend/cubeplex/mcp/cubepi_admin_refresh.py`, after the refreshed `tools_cache` is computed and before the repo update:

```python
# Strip orphan citation mapping keys whose tool no longer exists in tools_cache.
current_tool_names = {t["name"] for t in (tools or [])}
existing_citations = dict(server.tool_citations or {})
orphans = sorted(k for k in existing_citations if k not in current_tool_names)
if orphans:
    for k in orphans:
        existing_citations.pop(k, None)
    notice = f"Removed citation mapping for vanished tools: {orphans!r}"
    # Preserve any prior error context; append.
    server.last_error = notice if not server.last_error else f"{server.last_error}\n{notice}"
server.tool_citations = existing_citations
```

If `refresh_server_tools` reads `discover_tools_metadata` directly, expose a `discover` kwarg with a default of `discover_tools_metadata` so the test can inject a fake. Example signature change at the top of the function:

```python
from cubeplex.mcp.cubepi_admin_discovery import discover_tools_metadata as _default_discover

async def refresh_server_tools(
    *,
    server: MCPServer,
    server_repo: MCPServerRepository,
    credential_or_token: str | None,
    discover: Callable[..., Awaitable[tuple[bool, list[dict[str, Any]] | None, str | None]]] = _default_discover,
) -> None:
    success, tools, error = await discover(server, credential_or_token=credential_or_token)
    ...
```

- [ ] **Step 4: Re-run the test**

Run: `cd backend && uv run pytest -q tests/unit/test_cubepi_admin_refresh.py::test_refresh_strips_orphan_citation_keys`

Expected: PASS.

- [ ] **Step 5: Run the full refresh test slice**

Run: `cd backend && uv run pytest -q tests/unit/test_cubepi_admin_refresh.py`

Expected: all pass (any pre-existing test that constructs an MCPServer without `tool_citations` should still pass because the column defaults to `{}`).

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/mcp/cubepi_admin_refresh.py \
        backend/tests/unit/test_cubepi_admin_refresh.py
git commit -m "feat(mcp): strip orphan citation keys on admin tools refresh"
```

---

## Task 6: `CubepiMCPServerSpec.tool_citations` + per-run namespacing + citation_configs

**Files:**

- Modify: `backend/cubeplex/mcp/cubepi_discovery.py:30-40` (dataclass) and `:110` (spec construction)
- Modify: `backend/cubeplex/mcp/cubepi_runtime.py` — rewrite return signature + behavior
- Create: `backend/tests/unit/mcp/test_namespace_and_citations.py`

- [ ] **Step 1: Add the failing unit test**

Create `backend/tests/unit/mcp/test_namespace_and_citations.py`:

```python
"""Per-run MCP loader namespaces tool names and emits citation configs."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from cubepi.agent.types import AgentTool
from pydantic import BaseModel


class _StubParams(BaseModel):
    pass


def _fake_tool(name: str) -> AgentTool[_StubParams]:
    async def _exec(**_kwargs: Any) -> Any:
        return {"content": [], "isError": False}

    return AgentTool(
        name=name,
        description="",
        parameters=_StubParams,
        execute=_exec,
    )


@pytest.mark.asyncio
async def test_loader_namespaces_tool_names_and_returns_citation_configs() -> None:
    from cubeplex.middleware.citations.config import CitationConfig
    from cubeplex.mcp.cubepi_discovery import CubepiMCPServerSpec
    from cubeplex.mcp.cubepi_runtime import load_workspace_mcp_tools_for_cubepi

    specs = [
        CubepiMCPServerSpec(
            server_id="mcp-1",
            server_name="webtools",
            url="http://example.com/mcp",
            headers={},
            tool_citations={
                "web_search": {
                    "content_type": "json",
                    "source_type": "web",
                    "content_field": "results",
                    "mapping": {"snippet": "description"},
                }
            },
        ),
        CubepiMCPServerSpec(
            server_id="mcp-2",
            server_name="other",
            url="http://other.example.com/mcp",
            headers={},
            tool_citations={},
        ),
    ]

    with patch(
        "cubeplex.mcp.cubepi_runtime.discover_workspace_mcp_servers_for_cubepi",
        new=AsyncMock(return_value=specs),
    ), patch(
        "cubeplex.mcp.cubepi_runtime.load_mcp_tools_http",
        new=AsyncMock(side_effect=[
            [_fake_tool("web_search"), _fake_tool("web_fetch")],
            [_fake_tool("web_search")],  # same bare name, different server
        ]),
    ):
        tools, citation_configs = await load_workspace_mcp_tools_for_cubepi(
            session=None,            # type: ignore[arg-type]
            workspace_id="ws-x",
            org_id="org-x",
            user_id="usr-x",
            cred_service=None,       # type: ignore[arg-type]
            signer=None,             # type: ignore[arg-type]
        )

    names = {t.name for t in tools}
    assert names == {"webtools__web_search", "webtools__web_fetch", "other__web_search"}

    assert set(citation_configs.keys()) == {"webtools__web_search"}
    assert isinstance(citation_configs["webtools__web_search"], CitationConfig)
    assert citation_configs["webtools__web_search"].source_type == "web"
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `cd backend && uv run pytest -q tests/unit/mcp/test_namespace_and_citations.py`

Expected: FAIL — `CubepiMCPServerSpec.__init__()` doesn't accept `tool_citations`, and the loader signature doesn't return a tuple.

- [ ] **Step 3: Extend `CubepiMCPServerSpec`**

Edit `backend/cubeplex/mcp/cubepi_discovery.py:28` — add the field:

```python
@dataclass
class CubepiMCPServerSpec:
    """Resolved MCP server ready for cubepi.mcp.load_mcp_tools_http."""

    server_id: str
    server_name: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    tool_citations: dict[str, dict[str, Any]] = field(default_factory=dict)
```

- [ ] **Step 4: Populate `tool_citations` when building specs**

In `backend/cubeplex/mcp/cubepi_discovery.py:110-117`, change the `specs.append(CubepiMCPServerSpec(...))` block to include the new field:

```python
        specs.append(
            CubepiMCPServerSpec(
                server_id=server.id,
                server_name=server.name,
                url=server.server_url,
                headers=headers,
                tool_citations=dict(server.tool_citations or {}),
            )
        )
```

- [ ] **Step 5: Rewrite `load_workspace_mcp_tools_for_cubepi`**

Replace `backend/cubeplex/mcp/cubepi_runtime.py` body with:

```python
"""MCP tool loading for the cubepi runtime (M2.4)."""

from __future__ import annotations

import logging
from typing import Any

from cubepi.agent.types import AgentTool
from cubepi.mcp import load_mcp_tools_http
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.mcp.cubepi_discovery import discover_workspace_mcp_servers_for_cubepi
from cubeplex.mcp.user_token import MCPUserTokenSigner
from cubeplex.middleware.citations.config import CitationConfig
from cubeplex.services.credential import CredentialService

logger = logging.getLogger(__name__)


async def load_workspace_mcp_tools_for_cubepi(
    *,
    session: AsyncSession,
    workspace_id: str,
    org_id: str,
    user_id: str,
    cred_service: CredentialService,
    signer: MCPUserTokenSigner,
) -> tuple[list[AgentTool[Any]], dict[str, CitationConfig]]:
    """Load all enabled MCP servers' tools for a workspace as cubepi.AgentTool.

    Tool names are namespaced as ``{server_name}__{tool_name}`` so two MCP
    servers can ship the same bare tool name without colliding in the
    agent's tool list. The matching ``CitationConfig`` dict uses the same
    namespaced keys.

    Per-server failures are caught and logged, never aborting the load.

    Only HTTP/SSE transports are supported.
    """
    servers = await discover_workspace_mcp_servers_for_cubepi(
        session=session,
        workspace_id=workspace_id,
        org_id=org_id,
        user_id=user_id,
        cred_service=cred_service,
        signer=signer,
    )

    all_tools: list[AgentTool[Any]] = []
    all_citations: dict[str, CitationConfig] = {}
    for spec in servers:
        try:
            tools = await load_mcp_tools_http(
                spec.url,
                headers=spec.headers or None,
                timeout=30.0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to load MCP server %s (%s): %s",
                spec.server_name,
                spec.server_id,
                exc,
            )
            continue

        prefix = f"{spec.server_name}__"
        for tool in tools:
            bare_name = tool.name
            tool.name = f"{prefix}{bare_name}"
            all_tools.append(tool)
            raw = (spec.tool_citations or {}).get(bare_name)
            if raw is None:
                continue
            try:
                all_citations[tool.name] = CitationConfig(**raw)
            except ValidationError as exc:
                logger.warning(
                    "Bad tool_citations on %s/%s: %s — skipping",
                    spec.server_name,
                    bare_name,
                    exc,
                )

    return all_tools, all_citations
```

- [ ] **Step 6: Re-run the test**

Run: `cd backend && uv run pytest -q tests/unit/mcp/test_namespace_and_citations.py`

Expected: PASS.

- [ ] **Step 7: Run the broader MCP unit slice**

Run: `cd backend && uv run pytest -q tests/unit/mcp/ tests/unit/test_cubepi_admin_refresh.py tests/unit/test_cubepi_admin_discovery.py`

Expected: all pass. (Any caller that destructures the loader's return as a list will now break — fixed in Task 7.)

- [ ] **Step 8: Commit**

```bash
git add backend/cubeplex/mcp/cubepi_discovery.py \
        backend/cubeplex/mcp/cubepi_runtime.py \
        backend/tests/unit/mcp/test_namespace_and_citations.py
git commit -m "feat(mcp): namespace tool names and emit citation_configs from per-run loader"
```

---

## Task 7: `run_manager.py` wires citation_configs into `CitationMiddleware`

**Files:**

- Modify: `backend/cubeplex/streams/run_manager.py:640-720` (two adjacent code blocks)
- Test: extend `backend/tests/unit/mcp/test_namespace_and_citations.py` with a focused integration test

- [ ] **Step 1: Add the integration-style test**

Append to `backend/tests/unit/mcp/test_namespace_and_citations.py`:

```python
@pytest.mark.asyncio
async def test_run_manager_threads_citation_configs_to_middleware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The MCP loader's citation_configs reach CitationMiddleware ctor."""
    from cubeplex.middleware.citation import CitationMiddleware
    from cubeplex.middleware.citations.config import CitationConfig

    captured: dict[str, Any] = {}
    real_init = CitationMiddleware.__init__

    def spying_init(self: CitationMiddleware, *args: Any, **kwargs: Any) -> None:
        captured["citation_configs"] = kwargs.get("citation_configs")
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(CitationMiddleware, "__init__", spying_init)

    fake_cfg = CitationConfig(
        source_type="web", content_field="results", mapping={"snippet": "snippet"}
    )

    async def fake_loader(**_kwargs: Any) -> tuple[list[Any], dict[str, CitationConfig]]:
        return [], {"webtools__web_search": fake_cfg}

    monkeypatch.setattr(
        "cubeplex.mcp.cubepi_runtime.load_workspace_mcp_tools_for_cubepi", fake_loader
    )

    # Drive a single run setup. Reuse whatever harness existing run_manager tests
    # use; if none exists, the engineer should add a minimal one that calls only
    # the middleware-assembly portion of _run_cubepi_path. The point: capture the
    # `citation_configs` dict that ends up in the CitationMiddleware constructor.
    ...

    assert captured["citation_configs"] == {"webtools__web_search": fake_cfg}
```

> If wiring an isolated harness around `_run_cubepi_path` is too invasive, downgrade this to a fixed-narrow assertion: change `run_manager.py` to expose the tuple destructure as a small helper (e.g., `_assemble_mcp_state(...)`) returning `(tools, citation_configs)`, and unit-test that helper directly. Either is acceptable.

- [ ] **Step 2: Run test to confirm it fails**

Run: `cd backend && uv run pytest -q tests/unit/mcp/test_namespace_and_citations.py::test_run_manager_threads_citation_configs_to_middleware`

Expected: FAIL (loader returns a tuple, current `mcp_tools = await load_workspace_mcp_tools_for_cubepi(...)` tries to `.extend(mcp_tools)` into a list — `TypeError` or AttributeError).

- [ ] **Step 3: Update the loader call site at `:640-660`**

Edit `backend/cubeplex/streams/run_manager.py:650-660` — replace:

```python
                mcp_tools = await load_workspace_mcp_tools_for_cubepi(
                    session=mcp_session,
                    workspace_id=ctx.workspace_id,
                    org_id=ctx.org_id,
                    user_id=ctx.user_id,
                    cred_service=cred_service,
                    signer=self._app.state.mcp_user_token_signer,
                )
                _builtin_tools.extend(mcp_tools)
```

with:

```python
                mcp_tools, mcp_citation_configs = await load_workspace_mcp_tools_for_cubepi(
                    session=mcp_session,
                    workspace_id=ctx.workspace_id,
                    org_id=ctx.org_id,
                    user_id=ctx.user_id,
                    cred_service=cred_service,
                    signer=self._app.state.mcp_user_token_signer,
                )
                _builtin_tools.extend(mcp_tools)
```

Initialize `mcp_citation_configs` earlier in the function, just before the `try:` that wraps the loader call, so the citation block below can read it on the exception path too:

```python
        from cubeplex.middleware.citations.config import CitationConfig  # local import OK

        mcp_citation_configs: dict[str, CitationConfig] = {}
        try:
            # existing imports + with block …
```

And in the `except Exception as _exc:` of that block, leave `mcp_citation_configs` as the empty default (no change needed).

- [ ] **Step 4: Update the citation middleware site at `:712-720`**

Edit `backend/cubeplex/streams/run_manager.py:712-722` — replace `citation_configs={}` with `citation_configs=mcp_citation_configs`:

```python
        # 3. CitationMiddleware — needs citation_configs (empty dict = pass-through)
        try:
            from cubeplex.middleware.citation import CitationMiddleware
            from cubeplex.middleware.citations.counter import citation_event_queue

            cubepi_middleware.append(
                CitationMiddleware(
                    citation_configs=mcp_citation_configs,
                    event_queue=citation_event_queue.get(None),
                )
            )
        except Exception as _exc:
            logger.warning("CitationMiddleware unavailable: {}", _exc)
```

- [ ] **Step 5: Re-run the test**

Run: `cd backend && uv run pytest -q tests/unit/mcp/test_namespace_and_citations.py`

Expected: PASS (all tests in this file).

- [ ] **Step 6: Type-check + sanity-run the streams test slice**

```bash
cd backend
uv run mypy cubeplex/streams/run_manager.py cubeplex/mcp/cubepi_runtime.py
uv run pytest -q tests/unit/mcp/ tests/unit/test_citation.py
```

Expected: no mypy errors; all unit tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/streams/run_manager.py \
        backend/tests/unit/mcp/test_namespace_and_citations.py
git commit -m "feat(streams): wire MCP citation_configs into CitationMiddleware"
```

---

## Task 8: API — GET/PATCH server tool-citations

**Files:**

- Modify: `backend/cubeplex/api/routes/v1/ws_mcp.py` (extend with two endpoints)
- Modify: `backend/cubeplex/services/mcp.py` (small wrapper if needed for service-layer access; otherwise inline)
- Create: `backend/tests/unit/test_tool_citations_routes.py`

- [ ] **Step 1: Add the failing test**

Create `backend/tests/unit/test_tool_citations_routes.py`:

```python
"""GET/PATCH /ws/{wsId}/mcp/servers/{serverId}/tool-citations routes."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.asyncio
async def test_get_tool_citations_returns_current_state(
    member_client: Any,  # authenticated httpx.AsyncClient for a workspace member
    seeded_mcp_server: Any,  # has tool_citations={"web_search": {...}} and tools_cache
) -> None:
    ws_id = seeded_mcp_server.workspace_id
    server_id = seeded_mcp_server.id

    resp = await member_client.get(
        f"/api/v1/ws/{ws_id}/mcp/servers/{server_id}/tool-citations"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["server_id"] == server_id
    assert body["server_name"] == seeded_mcp_server.name
    assert "web_search" in body["tool_citations"]
    assert any(t["name"] == "web_search" for t in body["tools_cache"])
    assert body["orphan_keys"] == []


@pytest.mark.asyncio
async def test_patch_tool_citations_replaces_state(
    admin_client: Any,
    seeded_mcp_server: Any,
) -> None:
    ws_id = seeded_mcp_server.workspace_id
    server_id = seeded_mcp_server.id

    new_dict = {
        "web_search": {
            "content_type": "json",
            "source_type": "web",
            "content_field": "data",
            "mapping": {"url": "u", "snippet": "s"},
        }
    }
    resp = await admin_client.patch(
        f"/api/v1/ws/{ws_id}/mcp/servers/{server_id}/tool-citations",
        json={"tool_citations": new_dict},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tool_citations"] == new_dict


@pytest.mark.asyncio
async def test_patch_rejects_unknown_tool_name(
    admin_client: Any,
    seeded_mcp_server: Any,
) -> None:
    ws_id = seeded_mcp_server.workspace_id
    server_id = seeded_mcp_server.id

    resp = await admin_client.patch(
        f"/api/v1/ws/{ws_id}/mcp/servers/{server_id}/tool-citations",
        json={"tool_citations": {
            "ghost_tool": {
                "content_type": "json", "source_type": "web",
                "content_field": None, "mapping": {},
            }
        }},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any("ghost_tool" in str(d) for d in (detail if isinstance(detail, list) else [detail]))


@pytest.mark.asyncio
async def test_patch_rejects_invalid_citation_config(
    admin_client: Any,
    seeded_mcp_server: Any,
) -> None:
    ws_id = seeded_mcp_server.workspace_id
    server_id = seeded_mcp_server.id

    resp = await admin_client.patch(
        f"/api/v1/ws/{ws_id}/mcp/servers/{server_id}/tool-citations",
        json={"tool_citations": {
            "web_search": {"content_type": "binary", "source_type": "web",
                           "content_field": None, "mapping": {}},
        }},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_forbidden_for_member(
    member_client: Any,
    seeded_mcp_server: Any,
) -> None:
    ws_id = seeded_mcp_server.workspace_id
    server_id = seeded_mcp_server.id

    resp = await member_client.patch(
        f"/api/v1/ws/{ws_id}/mcp/servers/{server_id}/tool-citations",
        json={"tool_citations": {}},
    )
    assert resp.status_code in (403, 401)
```

> The `member_client`, `admin_client`, and `seeded_mcp_server` fixtures: reuse whatever existing route tests (`tests/e2e/test_ws_mcp*.py` or unit equivalents) already use. If they only exist in E2E form, this test file can live under `tests/e2e/` instead — pick by what the existing route tests do.

- [ ] **Step 2: Run test to confirm it fails**

Run: `cd backend && uv run pytest -q tests/unit/test_tool_citations_routes.py`

Expected: FAIL (404 — routes don't exist yet).

- [ ] **Step 3: Add pydantic schemas in `ws_mcp.py`**

Edit `backend/cubeplex/api/routes/v1/ws_mcp.py` — near the existing route models, add:

```python
from cubeplex.middleware.citations.config import CitationConfig


class ToolCitationsResponse(BaseModel):
    server_id: str
    server_name: str
    tools_cache: list[dict[str, Any]]
    tool_citations: dict[str, dict[str, Any]]
    catalog_defaults: dict[str, dict[str, Any]] | None
    orphan_keys: list[str]


class ToolCitationsPatch(BaseModel):
    tool_citations: dict[str, dict[str, Any]]
```

- [ ] **Step 4: Add GET endpoint**

Append to `backend/cubeplex/api/routes/v1/ws_mcp.py`:

```python
@router.get("/servers/{server_id}/tool-citations")
async def get_tool_citations(
    workspace_id: str,
    server_id: str,
    svc: MCPServerService = Depends(get_mcp_service),
) -> ToolCitationsResponse:
    server = await _get_workspace_visible_server(
        svc=svc, server_id=server_id, workspace_id=workspace_id,
    )
    tools_cache = list(server.tools_cache or [])
    known_names = {t["name"] for t in tools_cache}
    citations = dict(server.tool_citations or {})
    orphans = sorted(k for k in citations if k not in known_names)

    catalog_defaults: dict[str, dict[str, Any]] | None = None
    if server.catalog_connector_id is not None:
        catalog = await svc.catalog_repo.get_by_id(server.catalog_connector_id)
        if catalog is not None:
            catalog_defaults = dict(catalog.tool_citations or {})

    return ToolCitationsResponse(
        server_id=server.id,
        server_name=server.name,
        tools_cache=tools_cache,
        tool_citations=citations,
        catalog_defaults=catalog_defaults,
        orphan_keys=orphans,
    )
```

> `svc.catalog_repo`: if `MCPServerService` doesn't already expose the catalog repo, either add a property or build a temporary `MCPCatalogConnectorRepository(svc.session)` here. The simpler change wins.

- [ ] **Step 5: Add PATCH endpoint**

Append:

```python
@router.patch("/servers/{server_id}/tool-citations")
async def patch_tool_citations(
    workspace_id: str,
    server_id: str,
    body: ToolCitationsPatch,
    svc: MCPServerService = Depends(get_mcp_service),
    _ctx: RequestContext = Depends(require_admin),
    audit: AuditSink = Depends(get_audit_sink),
) -> ToolCitationsResponse:
    server = await _get_workspace_owned_server(
        svc=svc, server_id=server_id, workspace_id=workspace_id,
    )
    known_names = {t["name"] for t in (server.tools_cache or [])}

    # Validate per-entry: must reference a known tool and parse as CitationConfig.
    errors: list[dict[str, Any]] = []
    parsed: dict[str, dict[str, Any]] = {}
    for tool_name, raw in body.tool_citations.items():
        if tool_name not in known_names:
            errors.append({"tool": tool_name, "msg": "tool not in tools_cache"})
            continue
        try:
            CitationConfig(**raw)
        except ValidationError as exc:
            errors.append({"tool": tool_name, "msg": str(exc)})
            continue
        parsed[tool_name] = raw

    if errors:
        raise HTTPException(status_code=422, detail=errors)

    server.tool_citations = parsed
    await svc.server_repo.update(server)
    await svc.session.commit()
    await audit.record("mcp.tool_citations.patch", workspace_id=workspace_id, server_id=server_id)

    # Reuse GET-shape construction
    catalog_defaults: dict[str, dict[str, Any]] | None = None
    if server.catalog_connector_id is not None:
        catalog = await svc.catalog_repo.get_by_id(server.catalog_connector_id)
        if catalog is not None:
            catalog_defaults = dict(catalog.tool_citations or {})
    return ToolCitationsResponse(
        server_id=server.id, server_name=server.name,
        tools_cache=list(server.tools_cache or []),
        tool_citations=dict(server.tool_citations or {}),
        catalog_defaults=catalog_defaults,
        orphan_keys=[],
    )
```

Adjust import at the top of the file if not already present:

```python
from pydantic import BaseModel, ValidationError
from fastapi import HTTPException
from cubeplex.auth.dependencies import require_admin, require_member
```

- [ ] **Step 6: Re-run the tests**

Run: `cd backend && uv run pytest -q tests/unit/test_tool_citations_routes.py`

Expected: all pass.

- [ ] **Step 7: Run the broader route test slice to catch regressions**

Run: `cd backend && uv run pytest -q tests/unit/test_tool_citations_routes.py tests/unit/test_mcp_service_invariants.py`

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add backend/cubeplex/api/routes/v1/ws_mcp.py \
        backend/tests/unit/test_tool_citations_routes.py
git commit -m "feat(api): GET/PATCH /ws/{ws}/mcp/servers/{id}/tool-citations"
```

---

## Task 9: API — GET catalog tool-citations

**Files:**

- Modify: `backend/cubeplex/api/routes/v1/mcp_catalog.py` (add one endpoint to `catalog_member_router`)
- Modify: `backend/tests/unit/test_tool_citations_routes.py` (add test)

- [ ] **Step 1: Add the failing test**

Append to `backend/tests/unit/test_tool_citations_routes.py`:

```python
@pytest.mark.asyncio
async def test_get_catalog_tool_citations(
    member_client: Any,
    seeded_catalog: Any,  # fixture: catalog row with slug='webtools' + tool_citations
) -> None:
    ws_id = seeded_catalog.workspace_id
    resp = await member_client.get(
        f"/api/v1/ws/{ws_id}/mcp/catalog/webtools/tool-citations"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["slug"] == "webtools"
    assert "web_search" in body["tool_citations"]


@pytest.mark.asyncio
async def test_get_catalog_tool_citations_404_for_unknown_slug(
    member_client: Any,
    seeded_workspace_id: str,
) -> None:
    resp = await member_client.get(
        f"/api/v1/ws/{seeded_workspace_id}/mcp/catalog/does-not-exist/tool-citations"
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `cd backend && uv run pytest -q tests/unit/test_tool_citations_routes.py::test_get_catalog_tool_citations tests/unit/test_tool_citations_routes.py::test_get_catalog_tool_citations_404_for_unknown_slug`

Expected: FAIL — 404 (route missing).

- [ ] **Step 3: Add the endpoint**

In `backend/cubeplex/api/routes/v1/mcp_catalog.py`, append to `catalog_member_router`:

```python
class CatalogToolCitationsResponse(BaseModel):
    slug: str
    tool_citations: dict[str, dict[str, Any]]


@catalog_member_router.get("/catalog/{slug}/tool-citations")
async def get_catalog_tool_citations(
    workspace_id: str,
    slug: str,
    session: AsyncSession = Depends(get_async_session),
) -> CatalogToolCitationsResponse:
    repo = MCPCatalogConnectorRepository(session)
    row = await repo.get_by_slug(slug)
    if row is None:
        raise HTTPException(status_code=404, detail="catalog connector not found")
    return CatalogToolCitationsResponse(
        slug=row.slug,
        tool_citations=dict(row.tool_citations or {}),
    )
```

Add imports as needed (`HTTPException`, `BaseModel`, etc.).

- [ ] **Step 4: Re-run the tests**

Run: `cd backend && uv run pytest -q tests/unit/test_tool_citations_routes.py`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/api/routes/v1/mcp_catalog.py \
        backend/tests/unit/test_tool_citations_routes.py
git commit -m "feat(api): GET /ws/{ws}/mcp/catalog/{slug}/tool-citations"
```

---

## Task 10: Backend E2E — install → run → PATCH → orphan cleanup

**Files:**

- Create: `backend/tests/e2e/test_mcp_tool_citations.py`

This pulls all backend layers together. The fake MCP server uses the existing test fixture pattern from `tests/e2e/test_mcp_passthrough_jwt.py` (or whichever passes for in-test fake MCP) if available; otherwise mock the cubepi `load_mcp_tools_http` boundary.

- [ ] **Step 1: Create the E2E file with all four scenarios**

Create `backend/tests/e2e/test_mcp_tool_citations.py`:

```python
"""End-to-end: catalog install → agent run produces citations → PATCH replays → orphan cleanup."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_install_copies_catalog_tool_citations(
    e2e_workspace: ...,
    e2e_admin_client: ...,
) -> None:
    """Installing from catalog produces an mcp_servers row whose tool_citations matches the seeded catalog entry's."""
    # 1. Confirm 'webtools' catalog seed is present in DB.
    # 2. POST /ws/{ws}/mcp/catalog/webtools/install with auth_method=static + token.
    # 3. GET the installed server; assert tool_citations matches catalog's.


@pytest.mark.asyncio
async def test_run_with_mcp_tool_emits_citation_events(
    e2e_workspace: ...,
    e2e_admin_client: ...,
    fake_mcp_server_with_web_search: ...,
) -> None:
    """A run that triggers webtools__web_search yields citation_events in the SSE stream."""
    # 1. Install webtools pointing at the fake MCP fixture URL.
    # 2. POST a message that forces the agent to call web_search.
    # 3. Read the SSE stream; assert at least one CitationEvent payload references the expected URL.


@pytest.mark.asyncio
async def test_patch_replaces_mapping_visible_in_next_run(
    e2e_workspace: ...,
    e2e_admin_client: ...,
    fake_mcp_server_with_web_search: ...,
) -> None:
    """PATCH /tool-citations changes the runtime config picked up by the next agent run."""
    # 1. Install + run once → record citation field names emitted.
    # 2. PATCH /tool-citations to change `mapping.snippet` from "description" to "content".
    # 3. Make the fake server return a different field shape that matches "content".
    # 4. Run again → assert the snippet contents come from the new field.


@pytest.mark.asyncio
async def test_admin_refresh_strips_orphan_citations(
    e2e_workspace: ...,
    e2e_admin_client: ...,
    fake_mcp_server_with_web_search: ...,
) -> None:
    """Refreshing tools after the fake server drops a tool also drops its citation mapping."""
    # 1. Install; PATCH tool_citations to include both web_search and a fake old_tool.
    # 2. Change the fake server's tool list to web_search only.
    # 3. POST /ws/{ws}/mcp/servers/{id}/refresh-tools.
    # 4. GET tool-citations; assert old_tool key is gone; last_error mentions removal.
```

> The four `...` placeholders are fixture types — wire to actual fixtures present in `tests/e2e/conftest.py`. If a fake MCP HTTP server fixture doesn't exist, model it on what `test_mcp_passthrough_jwt.py` (referenced in PR #95's removed-test list — port equivalent from git history at `b5d374d7^`) used, or build a minimal aiohttp-based fake.

- [ ] **Step 2: Wire the fixtures and fill in the test bodies**

Replace each placeholder body with concrete code. Each test must:

1. Use a real DB connection (the worktree's `cubeplex_feat_mcp_tool_citations` DB; `pytest-asyncio` + the existing E2E session fixture handles this).
2. Hit the FastAPI test client end-to-end (no monkey-patches inside `cubepi_runtime`).
3. Use the fake MCP server fixture for any tool-call behavior, so the agent's MCP call path is exercised.

- [ ] **Step 3: Run the E2E file**

Run: `cd backend && uv run pytest -q tests/e2e/test_mcp_tool_citations.py`

Expected: all four tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/e2e/test_mcp_tool_citations.py
git commit -m "test(e2e): citation mapping end-to-end (install/run/patch/refresh)"
```

---

## Task 11: Frontend core types + API client

**Files:**

- Modify: `frontend/packages/core/src/types/mcp.ts`
- Modify: `frontend/packages/core/src/api/mcp.ts`
- Test: `frontend/packages/core/src/api/__tests__/mcp.test.ts` (or whichever convention is in use)

- [ ] **Step 1: Add the failing client test**

Create or extend `frontend/packages/core/src/api/__tests__/mcp.test.ts` (use vitest, mirror sibling tests):

```typescript
import { describe, it, expect, vi } from 'vitest'
import { McpClient } from '../mcp'
import type { ToolCitationsResponse } from '../../types/mcp'

describe('McpClient.tool_citations', () => {
  it('GET maps to the right URL and returns the response shape', async () => {
    const sample: ToolCitationsResponse = {
      server_id: 'mcp-1',
      server_name: 'webtools',
      tools_cache: [{ name: 'web_search', description: '', input_schema: {} }],
      tool_citations: {
        web_search: { content_type: 'json', source_type: 'web', content_field: 'results', mapping: {} },
      },
      catalog_defaults: null,
      orphan_keys: [],
    }
    const http = { get: vi.fn().mockResolvedValue({ data: sample }), patch: vi.fn() }
    const client = new McpClient(http as any, 'ws-x')
    const out = await client.getToolCitations('mcp-1')
    expect(http.get).toHaveBeenCalledWith('/api/v1/ws/ws-x/mcp/servers/mcp-1/tool-citations')
    expect(out).toEqual(sample)
  })

  it('PATCH sends the whole dict', async () => {
    const http = { get: vi.fn(), patch: vi.fn().mockResolvedValue({ data: {} }) }
    const client = new McpClient(http as any, 'ws-x')
    const payload = { web_search: { content_type: 'json' as const, source_type: 'web', content_field: 'results', mapping: {} } }
    await client.patchToolCitations('mcp-1', payload)
    expect(http.patch).toHaveBeenCalledWith(
      '/api/v1/ws/ws-x/mcp/servers/mcp-1/tool-citations',
      { tool_citations: payload },
    )
  })

  it('GET catalog defaults uses catalog URL', async () => {
    const http = { get: vi.fn().mockResolvedValue({ data: { slug: 'webtools', tool_citations: {} } }) }
    const client = new McpClient(http as any, 'ws-x')
    await client.getCatalogToolCitations('webtools')
    expect(http.get).toHaveBeenCalledWith('/api/v1/ws/ws-x/mcp/catalog/webtools/tool-citations')
  })
})
```

- [ ] **Step 2: Add the failing test run**

Run: `cd frontend && pnpm --filter @cubeplex/core test -- mcp`

Expected: FAIL — types don't exist, methods don't exist.

- [ ] **Step 3: Add types**

Edit `frontend/packages/core/src/types/mcp.ts` — append:

```typescript
export interface CitationConfigJSON {
  content_type: 'json' | 'text'
  source_type: string
  content_field: string | null
  mapping: Record<string, string>
  args_mapping?: Record<string, string> | null
  discriminator_field?: string | null
  discriminator_values?: string[] | null
}

export interface ToolCitationsResponse {
  server_id: string
  server_name: string
  tools_cache: Array<{ name: string; description: string; input_schema: unknown }>
  tool_citations: Record<string, CitationConfigJSON>
  catalog_defaults: Record<string, CitationConfigJSON> | null
  orphan_keys: string[]
}

export interface CatalogToolCitationsResponse {
  slug: string
  tool_citations: Record<string, CitationConfigJSON>
}
```

- [ ] **Step 4: Add client methods**

Edit `frontend/packages/core/src/api/mcp.ts` — add methods inside `McpClient` (match existing instance/method style):

```typescript
import type {
  CitationConfigJSON,
  ToolCitationsResponse,
  CatalogToolCitationsResponse,
} from '../types/mcp'

// inside McpClient (the wsId-scoped client):

async getToolCitations(serverId: string): Promise<ToolCitationsResponse> {
  const { data } = await this.http.get<ToolCitationsResponse>(
    `/api/v1/ws/${this.wsId}/mcp/servers/${serverId}/tool-citations`,
  )
  return data
}

async patchToolCitations(
  serverId: string,
  toolCitations: Record<string, CitationConfigJSON>,
): Promise<ToolCitationsResponse> {
  const { data } = await this.http.patch<ToolCitationsResponse>(
    `/api/v1/ws/${this.wsId}/mcp/servers/${serverId}/tool-citations`,
    { tool_citations: toolCitations },
  )
  return data
}

async getCatalogToolCitations(slug: string): Promise<CatalogToolCitationsResponse> {
  const { data } = await this.http.get<CatalogToolCitationsResponse>(
    `/api/v1/ws/${this.wsId}/mcp/catalog/${slug}/tool-citations`,
  )
  return data
}
```

- [ ] **Step 5: Rebuild core, re-run tests**

```bash
cd frontend
pnpm --filter @cubeplex/core build
pnpm --filter @cubeplex/core test -- mcp
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/core/src/types/mcp.ts \
        frontend/packages/core/src/api/mcp.ts \
        frontend/packages/core/src/api/__tests__/mcp.test.ts
git commit -m "feat(core): add tool-citations client methods + types"
```

---

## Task 12: Frontend — citation editor components

**Files:**

- Create: `frontend/packages/web/components/mcp/MCPCitationMappingTab.tsx`
- Create: `frontend/packages/web/components/mcp/MCPCitationEditor.tsx`
- Create: `frontend/packages/web/components/mcp/MCPCitationFieldRow.tsx`

This task is structurally large but contains no novel logic. Build it in three small parts: row → editor → tab.

- [ ] **Step 1: `MCPCitationFieldRow` — one mapping row**

Create `frontend/packages/web/components/mcp/MCPCitationFieldRow.tsx`:

```tsx
'use client'

import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Trash2 } from 'lucide-react'

interface Props {
  metaField: string
  outputField: string
  outputFieldCandidates: string[] | null  // null => no sample, render as plain text input
  onMetaFieldChange: (v: string) => void
  onOutputFieldChange: (v: string) => void
  onRemove: () => void
  readOnly?: boolean
}

export function MCPCitationFieldRow(props: Props) {
  const { metaField, outputField, outputFieldCandidates, readOnly } = props
  return (
    <div className="flex items-center gap-2">
      <Input
        className="w-40"
        value={metaField}
        onChange={(e) => props.onMetaFieldChange(e.target.value)}
        readOnly={readOnly}
      />
      <span className="text-muted-foreground">=</span>
      {outputFieldCandidates ? (
        <select
          className="flex-1 rounded-md border px-2 py-1"
          value={outputField}
          onChange={(e) => props.onOutputFieldChange(e.target.value)}
          disabled={readOnly}
        >
          <option value="">—</option>
          {outputFieldCandidates.map((f) => (
            <option key={f} value={f}>{f}</option>
          ))}
        </select>
      ) : (
        <Input
          className="flex-1"
          value={outputField}
          onChange={(e) => props.onOutputFieldChange(e.target.value)}
          readOnly={readOnly}
        />
      )}
      {!readOnly && (
        <Button variant="ghost" size="icon" onClick={props.onRemove}>
          <Trash2 className="h-4 w-4" />
        </Button>
      )}
    </div>
  )
}
```

- [ ] **Step 2: `MCPCitationEditor` — single-tool editor**

Create `frontend/packages/web/components/mcp/MCPCitationEditor.tsx`:

```tsx
'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { MCPCitationFieldRow } from './MCPCitationFieldRow'
import type { CitationConfigJSON } from '@cubeplex/core'

interface Props {
  toolName: string
  inputSchemaArgs: string[]                  // from tools_cache[*].input_schema.properties
  outputFieldCandidates: string[] | null     // null = no captured response sample
  value: CitationConfigJSON | null
  defaultFromCatalog: CitationConfigJSON | null
  onChange: (next: CitationConfigJSON | null) => void
  readOnly: boolean
}

export function MCPCitationEditor(props: Props) {
  const t = useTranslations('mcp.serverDetail.citations')
  const cfg = props.value ?? {
    content_type: 'json',
    source_type: 'web',
    content_field: null,
    mapping: { snippet: '' },
  }

  const updateMapping = (key: string, next: string) => {
    const m = { ...cfg.mapping, [key]: next }
    props.onChange({ ...cfg, mapping: m })
  }
  const renameMapping = (oldKey: string, newKey: string) => {
    const m: Record<string, string> = {}
    for (const [k, v] of Object.entries(cfg.mapping)) m[k === oldKey ? newKey : k] = v
    props.onChange({ ...cfg, mapping: m })
  }
  const removeMapping = (key: string) => {
    const m = { ...cfg.mapping }
    delete m[key]
    props.onChange({ ...cfg, mapping: m })
  }
  const addMapping = () => updateMapping('', '')

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <h3 className="text-lg font-medium">{props.toolName}</h3>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => props.onChange(null)} disabled={props.readOnly}>
            {t('disable')}
          </Button>
          {props.defaultFromCatalog && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => props.onChange({ ...props.defaultFromCatalog! })}
              disabled={props.readOnly}
            >
              {t('resetToCatalogDefault')}
            </Button>
          )}
        </div>
      </header>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <Label>{t('sourceType')}</Label>
          <Input
            value={cfg.source_type}
            onChange={(e) => props.onChange({ ...cfg, source_type: e.target.value })}
            readOnly={props.readOnly}
          />
        </div>
        <div>
          <Label>{t('contentType')}</Label>
          <select
            className="rounded-md border px-2 py-1"
            value={cfg.content_type}
            onChange={(e) => props.onChange({ ...cfg, content_type: e.target.value as 'json' | 'text' })}
            disabled={props.readOnly}
          >
            <option value="json">json</option>
            <option value="text">text</option>
          </select>
        </div>
      </div>

      <div>
        <Label>{t('resultLocation')}</Label>
        <div className="flex items-center gap-2 mt-1">
          <input
            type="checkbox"
            checked={cfg.content_field === null}
            onChange={(e) =>
              props.onChange({ ...cfg, content_field: e.target.checked ? null : '' })
            }
            disabled={props.readOnly}
          />
          <span className="text-sm">{t('wholeResponseIsOneItem')}</span>
        </div>
        {cfg.content_field !== null && (
          <Input
            className="mt-2"
            value={cfg.content_field}
            placeholder={t('contentFieldPlaceholder')}
            onChange={(e) => props.onChange({ ...cfg, content_field: e.target.value })}
            readOnly={props.readOnly}
          />
        )}
      </div>

      <div>
        <Label>{t('metadataMapping')}</Label>
        <div className="space-y-2 mt-2">
          {Object.entries(cfg.mapping).map(([meta, out]) => (
            <MCPCitationFieldRow
              key={meta}
              metaField={meta}
              outputField={out}
              outputFieldCandidates={props.outputFieldCandidates}
              onMetaFieldChange={(v) => renameMapping(meta, v)}
              onOutputFieldChange={(v) => updateMapping(meta, v)}
              onRemove={() => removeMapping(meta)}
              readOnly={props.readOnly}
            />
          ))}
          {!props.readOnly && (
            <Button variant="ghost" size="sm" onClick={addMapping}>+ {t('addField')}</Button>
          )}
        </div>
        {props.outputFieldCandidates === null && (
          <p className="text-xs text-muted-foreground mt-1">
            {t('noSampleHint')}
          </p>
        )}
      </div>

      {/* args_mapping + discriminator: collapsed details, omitted for brevity in this snippet; mirror the metadata-mapping pattern. */}
    </div>
  )
}
```

> The `args_mapping` and `discriminator` collapsible sections follow the same pattern as `metadata mapping`. Implement them the same way — same `MCPCitationFieldRow` for args mapping, and a simple `Input` + chip list for discriminator values.

- [ ] **Step 3: `MCPCitationMappingTab` — master-detail container**

Create `frontend/packages/web/components/mcp/MCPCitationMappingTab.tsx`:

```tsx
'use client'

import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { MCPCitationEditor } from './MCPCitationEditor'
import type { CitationConfigJSON, ToolCitationsResponse } from '@cubeplex/core'
import { useMcpClient } from '@/hooks/useMcpClient'   // or however mcp client hooks are exposed

interface Props {
  serverId: string
  canEdit: boolean
}

export function MCPCitationMappingTab({ serverId, canEdit }: Props) {
  const t = useTranslations('mcp.serverDetail.citations')
  const client = useMcpClient()
  const [state, setState] = useState<ToolCitationsResponse | null>(null)
  const [draft, setDraft] = useState<Record<string, CitationConfigJSON>>({})
  const [selectedTool, setSelectedTool] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    client.getToolCitations(serverId).then((r) => {
      setState(r)
      setDraft(r.tool_citations)
      setSelectedTool(r.tools_cache[0]?.name ?? null)
    })
  }, [client, serverId])

  if (!state) return <div>{t('loading')}</div>

  const knownNames = new Set(state.tools_cache.map((t) => t.name))
  const orphans = Object.keys(draft).filter((k) => !knownNames.has(k))

  const onChange = (toolName: string, next: CitationConfigJSON | null) => {
    setDraft((prev) => {
      const copy = { ...prev }
      if (next === null) delete copy[toolName]
      else copy[toolName] = next
      return copy
    })
    setDirty(true)
  }

  const save = async () => {
    setSaving(true)
    try {
      const updated = await client.patchToolCitations(serverId, draft)
      setState(updated)
      setDraft(updated.tool_citations)
      setDirty(false)
    } finally {
      setSaving(false)
    }
  }

  const inputSchemaArgs = selectedTool
    ? Object.keys(
        (state.tools_cache.find((t) => t.name === selectedTool)?.input_schema as { properties?: Record<string, unknown> })?.properties ?? {},
      )
    : []

  return (
    <div className="flex gap-6">
      <aside className="w-1/3 border-r pr-4 space-y-1">
        {state.tools_cache.map((t) => {
          const has = draft[t.name] !== undefined
          return (
            <button
              key={t.name}
              className={`w-full text-left px-2 py-1 rounded ${selectedTool === t.name ? 'bg-accent' : ''}`}
              onClick={() => setSelectedTool(t.name)}
            >
              <span className="mr-2">{has ? '✓' : '⚪'}</span>{t.name}
            </button>
          )
        })}
        {orphans.map((k) => (
          <div key={k} className="flex items-center gap-2 px-2 py-1 text-amber-600">
            <span>⚠</span>
            <span>{k}</span>
            <Button variant="ghost" size="sm" onClick={() => onChange(k, null)}>{t('remove')}</Button>
          </div>
        ))}
      </aside>

      <section className="flex-1">
        {selectedTool && (
          <MCPCitationEditor
            toolName={selectedTool}
            inputSchemaArgs={inputSchemaArgs}
            outputFieldCandidates={null}   /* null = no response sample available; falls back to text input per spec §3.5 — sister test-call feature populates this later */
            value={draft[selectedTool] ?? null}
            defaultFromCatalog={state.catalog_defaults?.[selectedTool] ?? null}
            onChange={(next) => onChange(selectedTool, next)}
            readOnly={!canEdit}
          />
        )}

        {canEdit && (
          <div className="mt-6 flex justify-end">
            <Button disabled={!dirty || saving} onClick={save}>
              {saving ? t('saving') : t('saveChanges')}
            </Button>
          </div>
        )}
      </section>
    </div>
  )
}
```

- [ ] **Step 4: Add "Copy from another server" dropdown to `MCPCitationEditor`**

In the editor's header (right next to "Reset to catalog default"), conditionally render a dropdown when at least one peer server in the same workspace has a non-empty mapping for the selected tool name.

The tab passes peer state down to the editor. In `MCPCitationMappingTab.tsx`, hold this state:

```tsx
import { useMcpStore } from '@cubeplex/core'  // or wherever the workspace MCP store hook lives
...
const allServers = useMcpStore((s) => s.serversForWorkspace(workspaceId))
const peerMappings = useMemo(() => {
  if (!selectedTool) return [] as Array<{ serverId: string; serverName: string; config: CitationConfigJSON }>
  return (allServers ?? [])
    .filter((srv) => srv.id !== serverId)
    .map((srv) => {
      const cfg = (srv.tool_citations ?? {})[selectedTool]
      return cfg ? { serverId: srv.id, serverName: srv.name, config: cfg } : null
    })
    .filter((x): x is { serverId: string; serverName: string; config: CitationConfigJSON } => x !== null)
}, [allServers, selectedTool, serverId])
```

Pass it through:

```tsx
<MCPCitationEditor
  ...existing props...
  peerMappings={peerMappings}
  onCopyFromPeer={(cfg) => onChange(selectedTool!, cfg)}
/>
```

In `MCPCitationEditor.tsx`, accept and render the dropdown next to the existing buttons:

```tsx
interface Props {
  // existing props ...
  peerMappings: Array<{ serverId: string; serverName: string; config: CitationConfigJSON }>
  onCopyFromPeer: (cfg: CitationConfigJSON) => void
}
```

Inside the header `<div className="flex gap-2">`:

```tsx
{props.peerMappings.length > 0 && (
  <select
    className="rounded-md border px-2 py-1"
    disabled={props.readOnly}
    value=""
    onChange={(e) => {
      const peer = props.peerMappings.find((p) => p.serverId === e.target.value)
      if (peer) props.onCopyFromPeer(peer.config)
    }}
  >
    <option value="">{t('copyFromPeer')}</option>
    {props.peerMappings.map((p) => (
      <option key={p.serverId} value={p.serverId}>{p.serverName}</option>
    ))}
  </select>
)}
```

> If `serversForWorkspace` selector / `tool_citations` aren't already exposed in `mcpStore`, that selector needs to be added. Look at `frontend/packages/core/src/stores/mcpStore.ts` for the existing shape — likely a single store-level extension is all that's needed.

- [ ] **Step 5: Type-check and build**

```bash
cd frontend
pnpm --filter @cubeplex/web type-check
```

Expected: no type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/components/mcp/MCPCitationFieldRow.tsx \
        frontend/packages/web/components/mcp/MCPCitationEditor.tsx \
        frontend/packages/web/components/mcp/MCPCitationMappingTab.tsx \
        frontend/packages/core/src/stores/mcpStore.ts
git commit -m "feat(web): MCP citation mapping tab + editor + field row"
```

---

## Task 13: Wire tab into `MCPServerDetail` + i18n + citation panel display

**Files:**

- Modify: `frontend/packages/web/components/mcp/MCPServerDetail.tsx`
- Modify: `frontend/packages/web/messages/en.json`, `frontend/packages/web/messages/zh.json` (or current i18n locations)
- Modify: citation chip / panel component (locate via grep for `tool_name` rendering)

- [ ] **Step 1: Wire the new tab in `MCPServerDetail`**

Edit `MCPServerDetail.tsx` — find the tab list (search for `t('toolsTab'`); add a sibling tab alongside the existing tools tab:

```tsx
<TabsTrigger value="citations">{t('citationsTab')}</TabsTrigger>
...
<TabsContent value="citations">
  <MCPCitationMappingTab serverId={server.id} canEdit={canEdit} />
</TabsContent>
```

Import `MCPCitationMappingTab` and pass through whatever `canEdit` derivation already exists (member vs admin).

- [ ] **Step 2: Add i18n entries**

Edit the message files. Under `mcp.serverDetail`:

```jsonc
"citations": {
  "tabTitle": "Citation mapping",
  "loading": "Loading…",
  "disable": "Disable",
  "resetToCatalogDefault": "Reset to catalog default",
  "sourceType": "Source type",
  "contentType": "Content type",
  "resultLocation": "Result location",
  "wholeResponseIsOneItem": "Whole response is one item",
  "contentFieldPlaceholder": "e.g. results",
  "metadataMapping": "Metadata mapping",
  "addField": "Add field",
  "noSampleHint": "No response sample yet. Go to Tools tab → Test call to capture one.",
  "remove": "Remove",
  "saveChanges": "Save changes",
  "saving": "Saving…",
  "copyFromPeer": "Copy from another server",
  "unsavedChanges": "You have unsaved citation changes. Discard them?"
},
"citationsTab": "Citation mapping"
```

Mirror in `zh.json` with translations matching the rest of the file's tone.

- [ ] **Step 3: Block tool switches and navigation while dirty**

In `MCPCitationMappingTab.tsx`, guard two transitions:

```tsx
// 1. Switching the selected tool while dirty.
const trySetSelectedTool = (next: string) => {
  if (dirty && !window.confirm(t('unsavedChanges'))) return
  setDirty(false)
  setDraft(state!.tool_citations)   // discard local edits
  setSelectedTool(next)
}
```

Replace the existing `onClick={() => setSelectedTool(t.name)}` in the list with `onClick={() => trySetSelectedTool(t.name)}`.

```tsx
// 2. beforeunload guard while dirty.
useEffect(() => {
  if (!dirty) return
  const h = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = '' }
  window.addEventListener('beforeunload', h)
  return () => window.removeEventListener('beforeunload', h)
}, [dirty])
```

The Next.js router-level guard is intentionally omitted — `beforeunload` covers the hard navigation case; within-app tab switches will trigger the `trySetSelectedTool` confirm above. If a stronger router guard is needed later, add it as a follow-up; it's not in this spec's scope.

- [ ] **Step 4: Citation chip — strip namespace prefix for display**

Search:

```bash
cd frontend && grep -rn "tool_name" packages/web/components/ | grep -v __tests__
```

Find the chat citation chip component (likely `CitationChip.tsx` or similar). Apply the split-on-`__` rule to the displayed tool name; preserve the raw `tool_name` in the tooltip / aria-label:

```tsx
const display = tool_name.includes('__')
  ? tool_name.split('__').slice(1).join('__')   // drop server prefix; survives bare names
  : tool_name
const serverHint = tool_name.includes('__') ? tool_name.split('__')[0] : null
```

Render `{display}` in the chip body; if `serverHint`, show it on the tooltip line.

- [ ] **Step 5: Type-check + smoke test the dev server**

```bash
cd frontend && pnpm --filter @cubeplex/web type-check
# In another shell, with the worktree's API up on its allocated port:
pnpm --filter @cubeplex/web dev
```

Open `http://localhost:$PORT/w/<wsId>/settings/mcp/<serverId>` (the port from `.worktree.env`). Confirm:
- Citations tab renders.
- Tool list populates from `tools_cache`.
- Picking a tool shows the editor.
- Save button enables only after an edit.
- A successful PATCH refreshes state.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/components/mcp/MCPServerDetail.tsx \
        frontend/packages/web/components/mcp/MCPCitationMappingTab.tsx \
        frontend/packages/web/components/**/Citation*.tsx \
        frontend/packages/web/messages/en.json \
        frontend/packages/web/messages/zh.json
git commit -m "feat(web): mount citations tab in MCPServerDetail + display split + dirty guard"
```

---

## Task 14: Frontend E2E — citation mapping editor

**Files:**

- Create: `frontend/packages/web/__tests__/e2e/mcp/citation-mapping.spec.ts`

- [ ] **Step 1: Author the Playwright spec**

Create `frontend/packages/web/__tests__/e2e/mcp/citation-mapping.spec.ts`:

```typescript
import { test, expect } from '@playwright/test'
import { loginAsWorkspaceAdmin, installWebtoolsFromCatalog } from './_helpers'

test('citation mapping editor: install → load defaults → edit → save → persist', async ({ page }) => {
  const { wsId, serverId } = await loginAsWorkspaceAdmin(page)
  await installWebtoolsFromCatalog(page, wsId)

  await page.goto(`/w/${wsId}/settings/mcp/${serverId}`)
  await page.getByRole('tab', { name: 'Citation mapping' }).click()

  // Defaults from the seeded catalog should be present.
  await expect(page.getByText('web_search')).toBeVisible()
  await expect(page.getByText('web_fetch')).toBeVisible()
  await page.getByRole('button', { name: 'web_search' }).click()
  await expect(page.getByLabel('Source type')).toHaveValue('web')

  // Edit: change source_type and save.
  await page.getByLabel('Source type').fill('webpage')
  await page.getByRole('button', { name: 'Save changes' }).click()
  await expect(page.getByText('Save changes')).toBeDisabled() // not dirty anymore

  // Reload → still 'webpage'.
  await page.reload()
  await page.getByRole('tab', { name: 'Citation mapping' }).click()
  await page.getByRole('button', { name: 'web_search' }).click()
  await expect(page.getByLabel('Source type')).toHaveValue('webpage')

  // Reset to catalog default → back to 'web'.
  await page.getByRole('button', { name: 'Reset to catalog default' }).click()
  await expect(page.getByLabel('Source type')).toHaveValue('web')
})
```

> `loginAsWorkspaceAdmin` and `installWebtoolsFromCatalog` are helper functions that the engineer either reuses (search `_helpers.ts` in the same directory) or stubs in if the existing ones don't cover catalog install.

- [ ] **Step 2: Run the spec**

```bash
cd frontend
pnpm --filter @cubeplex/web exec playwright install --with-deps chromium  # first time only
pnpm --filter @cubeplex/web test:e2e -- citation-mapping
```

Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/__tests__/e2e/mcp/citation-mapping.spec.ts \
        frontend/packages/web/__tests__/e2e/mcp/_helpers.ts
git commit -m "test(e2e): citation mapping editor flow"
```

---

## Pre-PR sweep

Before opening the PR:

- [ ] **Backend full check**

```bash
cd backend
make check
```

Expected: clean.

- [ ] **Frontend full check**

```bash
cd frontend
pnpm --filter @cubeplex/core build
pnpm --filter @cubeplex/web type-check
pnpm --filter @cubeplex/web lint
pnpm --filter @cubeplex/web test:e2e
```

Expected: all green.

- [ ] **Seed the catalog locally to smoke-test**

```bash
cd backend
uv run python -m cubeplex.cli seed-mcp-catalog
```

Expected: `webtools` connector upsert succeeds with non-empty `tool_citations`.

- [ ] **Open PR**

Push and open against `main`:

```bash
git push -u origin feat/mcp-tool-citations
gh pr create --title "feat(mcp): per-tool citation mapping + tool-name namespacing" \
  --body "$(cat <<'EOF'
## Summary
- DB-backed citation mapping for MCP tools (catalog defaults + per-install override)
- MCP tool names now namespaced as {server}__{tool}; fixes silent collision when two MCP servers expose the same bare tool name
- Frontend editor on MCP server detail page

Refs design: `docs/superpowers/specs/2026-05-14-mcp-tool-citations-design.md`

## Test plan
- [ ] `cd backend && make check` clean
- [ ] `cd frontend && pnpm --filter @cubeplex/web test:e2e` green
- [ ] Manual smoke: install webtools from catalog, run a web_search prompt, see citation in panel
EOF
)"
```
