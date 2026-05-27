# China-Vendor MCP Sources Implementation Plan

> Agentic-workers: execute tasks top-to-bottom. Each task is self-contained —
> write the failing test first, run it and confirm the expected failure, write
> the real implementation, run it and confirm pass, then commit. Stay on
> branch `feat/mcp-china-sources`. Do not amend, do not push, do not invoke
> codex. Run all `uv` / `pytest` commands from `backend/`.

## Goal

Add the China-vendor MCP connector(s) that fit the **current**
`MCPConnectorTemplateSeedEntry` schema unchanged to the seeded catalog
(`backend/cubebox/mcp/template_seed.py`), so a workspace/org admin sees them in
the one-click install list. Honor the spec's v1 scope decision:

- **v1 (this plan):** **Feishu / Lark** only — official, supports a documented
  remote mode, and authenticates with an `Authorization: Bearer <token>`
  header, which maps cleanly onto the existing `static` auth path with the
  shared `_TOKEN_FIELD` + `_BEARER_TEMPLATE`. No schema change, no migration.
- **Deferred (NOT in this plan):** every other curated source. They are blocked
  on a schema/runtime gap the spec calls out (§6.2, §8):
  - **Amap, Baidu Maps, Tencent Location** — API key lives in a URL query
    param (`?key=` / `?ak=`), which `static_auth_header_template` cannot
    express. Needs a new `static_auth_query_param` field + install-time URL
    injection. Blocked.
  - **Alipay** — stdio-only launch + asymmetric (App ID + RSA key-pair) auth.
    Needs a managed launcher and a key-pair credential kind. Blocked.
  - **DingTalk, WeCom, MiniMax (official), Tushare** — stdio-only packages;
    cubebox installs remote URLs only. Blocked until a managed launcher or a
    vendor-published remote endpoint exists.
  - **Bailian, ModelScope** — hosting marketplaces, not single connectors;
    each hosted service would be its own future row.

This plan is therefore deliberately small: it lands the one clean fit, asserts
it loads/validates and surfaces in the catalog, and refreshes the one stale doc.

## Architecture

The connector catalog is a frozen Python list (`CATALOG`) of
`MCPConnectorTemplateSeedEntry` dataclasses in
`backend/cubebox/mcp/template_seed.py`. `seed_templates()` upserts each entry by
`slug` into the `mcp_connector_templates` table (`MCPConnectorTemplate`,
`backend/cubebox/models/mcp.py`), encrypting any static-OAuth client secret into
a system-level `Credential`, and deprecating DB rows whose slug left the list.
The seeder runs idempotently (lock-guarded) on FastAPI startup
(`backend/cubebox/api/app.py`) and via `python -m cubebox.cli seed-mcp-templates`.

The admin route `GET /api/v1/admin/mcp/templates` reads the catalog through
`MCPConnectorTemplateService.list_active()`
(`backend/cubebox/services/mcp_templates.py`) and returns the active rows.

Adding a connector = appending one `MCPConnectorTemplateSeedEntry` to `CATALOG`.
No new tables, columns, routes, services, or migrations for v1.

## Tech Stack

- Python 3.x, FastAPI, SQLModel / SQLAlchemy async, Postgres (sqlite in-memory
  for the seed unit tests, per `test_catalog_seed.py`).
- `pytest` (async); `uv run pytest` from `backend/`.
- mypy strict; 100-char lines; type annotations everywhere.

---

## Task 1 — Add the Feishu seed entry to `CATALOG`

The single v1 connector. Feishu's remote mode is reached over an SSE endpoint
and authenticates with a Bearer App Access Token — a clean `static` fit.

**Files**

- Modify: `backend/cubebox/mcp/template_seed.py`
- Test (new): `backend/tests/unit/test_catalog_seed_china.py`

**Steps**

1. **Write the failing test.** Create
   `backend/tests/unit/test_catalog_seed_china.py`. Reuse the in-memory
   `session` / `backend` fixture pattern from
   `backend/tests/unit/test_catalog_seed.py` (copy the two fixtures and the
   `_make_get_env` helper verbatim — they are small and self-contained). Add a
   pure-data test that asserts the Feishu entry exists and is shaped as a
   header-Bearer `static` connector:

   ```python
   """Tests for the China-vendor additions to the MCP connector catalog."""

   from collections.abc import AsyncIterator, Callable

   import pytest
   from cryptography.fernet import Fernet
   from sqlalchemy.ext.asyncio import (
       AsyncSession,
       async_sessionmaker,
       create_async_engine,
   )
   from sqlmodel import SQLModel

   from cubebox.credentials.encryption import FernetBackend
   from cubebox.mcp.template_seed import CATALOG, seed_templates
   from cubebox.repositories.mcp import MCPConnectorTemplateRepository


   @pytest.fixture
   async def session() -> AsyncIterator[AsyncSession]:
       engine = create_async_engine("sqlite+aiosqlite:///:memory:")
       async with engine.begin() as conn:
           await conn.run_sync(SQLModel.metadata.create_all)
       maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
       async with maker() as s:
           yield s
       await engine.dispose()


   @pytest.fixture
   def backend() -> FernetBackend:
       return FernetBackend([Fernet.generate_key()])


   def _make_get_env(values: dict[str, str]) -> Callable[[str], str | None]:
       def _getter(key: str) -> str | None:
           return values.get(key)

       return _getter


   def test_feishu_entry_is_header_bearer_static() -> None:
       by_slug = {e.slug: e for e in CATALOG}
       assert "feishu" in by_slug
       entry = by_slug["feishu"]

       assert entry.provider == "Feishu"
       assert entry.transport == "sse"
       assert entry.supported_auth_methods == ["static"]
       assert entry.default_credential_policy == "workspace"
       # Bearer-in-header static auth — the schema-fitting shape.
       assert entry.static_auth_header_template == "Bearer {token}"
       assert entry.static_form_schema is not None
       assert entry.static_form_schema[0]["name"] == "token"
       # Not an OAuth-app / URL-query-param / stdio connector.
       assert entry.oauth_static_client_id_env is None
       assert entry.oauth_static_client_secret_env is None
       assert entry.oauth_dcr_supported is None
       assert entry.server_url.startswith("https://")
       assert "{token}" not in entry.server_url  # secret never baked into URL
       assert entry.template_metadata["docs_url"].startswith("https://")
   ```

2. **Run it, expect fail** (KeyError / assertion — `feishu` not yet in
   `CATALOG`):

   ```bash
   uv run pytest tests/unit/test_catalog_seed_china.py::test_feishu_entry_is_header_bearer_static -q
   ```

3. **Implement.** In `backend/cubebox/mcp/template_seed.py`, append this entry
   to the `CATALOG` list (after the existing `webtools` entry, before the
   closing `]`). It reuses `_TOKEN_FIELD` and `_BEARER_TEMPLATE` already defined
   in the module:

   ```python
       MCPConnectorTemplateSeedEntry(
           slug="feishu",
           name="Feishu / Lark",
           provider="Feishu",
           description=(
               "Feishu/Lark OpenAPI MCP (remote mode): docs, messages, "
               "calendar, bitable."
           ),
           server_url="https://lark-mcp.feishu.cn/mcp",
           transport="sse",
           supported_auth_methods=["static"],
           default_credential_policy="workspace",
           oauth_dcr_supported=None,
           oauth_default_scope=None,
           oauth_static_client_id_env=None,
           oauth_static_client_secret_env=None,
           static_form_schema=_TOKEN_FIELD,
           static_auth_header_template=_BEARER_TEMPLATE,
           template_metadata={
               "docs_url": (
                   "https://open.larksuite.com/document/mcp_open_tools/"
                   "call-feishu-mcp-server-in-remote-mode"
               ),
               "region": "cn",
           },
       ),
   ```

4. **Run it, expect pass:**

   ```bash
   uv run pytest tests/unit/test_catalog_seed_china.py::test_feishu_entry_is_header_bearer_static -q
   ```

5. **Commit:**

   ```bash
   git add backend/cubebox/mcp/template_seed.py \
       backend/tests/unit/test_catalog_seed_china.py
   git commit -m "$(cat <<'EOF'
   feat(mcp): add Feishu/Lark connector to the v1 catalog (#147)

   Feishu remote mode authenticates with a Bearer App Access Token, which
   maps onto the existing static header path with no schema change. It is the
   only curated China-vendor source that fits MCPConnectorTemplateSeedEntry
   unchanged; maps (URL-query-param key) and stdio/key-pair sources are
   deferred per the design doc.
   EOF
   )"
   ```

---

## Task 2 — Assert Feishu seeds and validates through `seed_templates()`

Prove the new entry isn't just well-shaped data but actually upserts a row and
passes the seeder's existing invariants (no env vars required, no skip, no
deprecation), the same way the existing catalog is exercised.

**Files**

- Test (modify): `backend/tests/unit/test_catalog_seed_china.py`

**Steps**

1. **Write the failing test.** Append to `test_catalog_seed_china.py`:

   ```python
   async def test_feishu_seeds_as_active_template_without_env(
       session: AsyncSession, backend: FernetBackend
   ) -> None:
       # Feishu needs no OAuth-app env vars → seeds even with an empty env.
       result = await seed_templates(
           session, backend, get_env=_make_get_env({})
       )

       # The empty env only skips connectors that require an OAuth client
       # secret; Feishu must not be among the skipped.
       repo = MCPConnectorTemplateRepository(session)
       active = {row.slug for row in await repo.list_active()}
       assert "feishu" in active
       assert result.deprecated == 0

       row = await repo.get_by_slug("feishu")
       assert row is not None
       assert row.status == "active"
       assert row.supported_auth_methods == ["static"]
       assert row.transport == "sse"
       assert row.static_auth_header_template == "Bearer {token}"
   ```

2. **Run it, expect pass already** (Task 1 added the entry, so this should pass
   immediately — that is acceptable; it locks the behavior in). Confirm:

   ```bash
   uv run pytest tests/unit/test_catalog_seed_china.py -q
   ```

   If it fails, debug the seeder mapping for the Feishu entry before
   proceeding.

3. **Regression sweep on the seeder suite** — the count-based assertions in
   `test_catalog_seed.py` (`upserted == len(CATALOG)`) must still hold after the
   catalog grew by one:

   ```bash
   uv run pytest tests/unit/test_catalog_seed.py tests/unit/test_catalog_seed_china.py -q
   ```

   If any count assertion in `test_catalog_seed.py` references a hard-coded
   number, update it to track `len(CATALOG)` (it already uses `len(CATALOG)`,
   so no change is expected — verify).

4. **Commit:**

   ```bash
   git add backend/tests/unit/test_catalog_seed_china.py
   git commit -m "$(cat <<'EOF'
   test(mcp): assert Feishu seeds as an active template (#147)

   Locks in that the Feishu entry upserts a static/SSE row with no OAuth env
   vars and survives the existing seeder invariants.
   EOF
   )"
   ```

---

## Task 3 — E2E: Feishu appears in the admin catalog endpoint

E2E-first per project discipline: assert the connector is reachable through the
real `GET /api/v1/admin/mcp/templates` route after seeding, not just in the
Python list. Mirror the existing admin-MCP E2E setup.

**Files**

- Test (new): `backend/tests/e2e/test_mcp_china_catalog.py`

**Steps**

1. **Locate the pattern.** Read an existing admin-MCP E2E test that seeds
   templates and calls the admin templates/installs routes (e.g.
   `backend/tests/e2e/test_mcp_four_layer_routes.py`,
   `backend/tests/e2e/test_mcp_oauth_handoff.py`) plus
   `backend/tests/e2e/conftest.py` for the seeded-catalog / authed-admin-client
   fixtures. Reuse those fixtures; do not invent a new harness.

2. **Write the failing test.** Create
   `backend/tests/e2e/test_mcp_china_catalog.py` that:
   - uses the existing fixture that seeds the catalog and yields an authed
     admin HTTP client (copy the fixture wiring from the chosen reference
     test verbatim — adapt only the assertions);
   - `GET`s `/api/v1/admin/mcp/templates`;
   - asserts the response is 200 and that an item with `slug == "feishu"` is
     present, with `transport == "sse"`, `supported_auth_methods == ["static"]`,
     and a non-secret token field in its `static_form_schema`.

   Shape of the assertion body (adapt request mechanics to the reference
   fixture's client/URL helpers):

   ```python
   async def test_feishu_appears_in_admin_catalog(...) -> None:
       resp = await admin_client.get("/api/v1/admin/mcp/templates")
       assert resp.status_code == 200
       items = resp.json()["items"]
       by_slug = {it["slug"]: it for it in items}
       assert "feishu" in by_slug
       feishu = by_slug["feishu"]
       assert feishu["transport"] == "sse"
       assert feishu["supported_auth_methods"] == ["static"]
   ```

3. **Run it, expect pass** (the seeder runs in the E2E harness, so Feishu should
   surface once Task 1 landed). If the reference fixture seeds via
   `seed_templates(...)` or app startup, confirm Feishu is included:

   ```bash
   uv run pytest tests/e2e/test_mcp_china_catalog.py -q
   ```

   If the route field names differ from the assumed JSON keys, read
   `backend/cubebox/api/schemas/mcp.py` (`MCPConnectorTemplateListOut` and the
   item schema) and `_template_to_out` in
   `backend/cubebox/api/routes/v1/admin_mcp.py`, then fix the assertion keys —
   this is a test-only correction, not an implementation change.

4. **Commit:**

   ```bash
   git add backend/tests/e2e/test_mcp_china_catalog.py
   git commit -m "$(cat <<'EOF'
   test(mcp): e2e assert Feishu surfaces in the admin catalog (#147)

   Proves the seeded Feishu template is reachable through the real admin
   templates route, not just the Python CATALOG list.
   EOF
   )"
   ```

---

## Task 4 — Fix the doc drift in `mcp_catalog_oauth.md`

The spec (§8) flags that `backend/docs/mcp_catalog_oauth.md` still uses the old
M2 naming — `mcp_catalog_connectors` table and `cubebox.mcp.catalog_seed.CATALOG`
— whereas the live schema is the `mcp_connector_templates` table seeded from
`cubebox.mcp.template_seed.CATALOG`. This is **in scope** because we are adding
a row to that exact catalog and the runbook should be correct for the next
editor. Doc-only change; no code, no tests.

**Files**

- Modify: `backend/docs/mcp_catalog_oauth.md`

**Steps**

1. **Find every stale reference:**

   ```bash
   grep -n "mcp_catalog_connectors\|catalog_seed" backend/docs/mcp_catalog_oauth.md
   ```

2. **Rewrite each occurrence** to the live names, using `Edit` (not sed):
   - `mcp_catalog_connectors` → `mcp_connector_templates`
   - `cubebox.mcp.catalog_seed.CATALOG` / `catalog_seed.py` →
     `cubebox.mcp.template_seed.CATALOG` / `template_seed.py`
   - Keep prose accurate: the source-of-truth dataclass is
     `MCPConnectorTemplateSeedEntry`; the CLI command is
     `python -m cubebox.cli seed-mcp-templates`; the seeder also runs
     idempotently on FastAPI startup (lock-guarded). Correct any sentence that
     claims it is *only* an out-of-band step if the doc says so.
   - Add one short line under the catalog section noting Feishu is the first
     China-vendor entry and that URL-query-param-key sources (maps) and
     stdio/key-pair sources (Alipay, DingTalk, WeCom, MiniMax, Tushare) are
     deferred pending a `static_auth_query_param` field / managed launcher
     (cross-reference the design doc
     `docs/dev/specs/2026-05-27-mcp-china-sources-design.md`).

3. **Verify no stale names remain:**

   ```bash
   grep -n "mcp_catalog_connectors\|catalog_seed" backend/docs/mcp_catalog_oauth.md
   ```

   Expect zero matches.

4. **Commit:**

   ```bash
   git add backend/docs/mcp_catalog_oauth.md
   git commit -m "$(cat <<'EOF'
   docs(mcp): refresh catalog runbook to template_seed naming (#147)

   Replace stale M2 mcp_catalog_connectors / catalog_seed references with the
   live mcp_connector_templates / template_seed names, and note the v1 Feishu
   addition plus the deferred China-vendor sources.
   EOF
   )"
   ```

---

## Task 5 — Pre-PR sweep + self-review

**Files**

- (verification only)

**Steps**

1. **Run the full MCP-affected suite:**

   ```bash
   uv run pytest tests/unit/test_catalog_seed.py \
       tests/unit/test_catalog_seed_china.py \
       tests/unit/test_cli_seed.py \
       tests/e2e/test_mcp_china_catalog.py -q
   ```

   All must pass.

2. **Type check** the changed module + tests:

   ```bash
   uv run mypy cubebox/mcp/template_seed.py
   ```

3. **Self-review checklist:**
   - [ ] Only Feishu added to `CATALOG`; no deferred source (maps / Alipay /
         DingTalk / WeCom / MiniMax / Tushare / marketplaces) slipped in.
   - [ ] No new column, migration, route, or service — schema unchanged.
   - [ ] Feishu `server_url` carries no secret; auth is header-Bearer only.
   - [ ] `static_form_schema` reuses `_TOKEN_FIELD`; header uses
         `_BEARER_TEMPLATE` — no duplicated literals.
   - [ ] Line length ≤ 100; full type annotations; no placeholder strings.
   - [ ] Doc drift fixed: zero `mcp_catalog_connectors` / `catalog_seed`
         references remain.
   - [ ] Every commit message scopes `(#147)`; no amend, no push, no codex.

4. **Confirm clean tree** (all work committed):

   ```bash
   git status
   git log --oneline -5
   ```

---

## Deferred (explicitly out of this plan)

Per design doc §6.2 / §7 / §8, these are recorded as future work, each blocked
on a named gap:

| Source | Blocker | Unblocks when |
|---|---|---|
| Amap, Baidu Maps, Tencent Location | API key in URL query param | `static_auth_query_param` field + install-time URL injection (secret stays in vault) |
| Alipay | stdio launch + RSA key-pair auth | managed launcher + key-pair credential kind |
| DingTalk, WeCom, MiniMax (official), Tushare | stdio-only packages | managed launcher or vendor remote endpoint |
| Bailian, ModelScope | marketplaces, not single connectors | curate individual hosted services as their own rows |
