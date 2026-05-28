# China-Vendor MCP Sources Implementation Plan

> Agentic-workers: execute tasks top-to-bottom. Each task is self-contained —
> write the failing test first, run it and confirm the expected failure, write
> the real implementation, run it and confirm pass, then commit. Stay on
> branch `feat/mcp-china-sources`. Do not amend, do not push, do not invoke
> codex. Run all `uv` / `pytest` commands from `backend/`.

## Goal

Land the catalog groundwork for China-vendor MCP sources **without** adding any
seed entry yet, because a local codex review of this plan found that the one
candidate we believed was a clean fit (Feishu / Lark) is in fact blocked by a
runtime auth gap. Concretely:

- The runtime resolves **static** auth by hardcoding
  `headers["Authorization"] = f"Bearer {plaintext}"`
  (`backend/cubebox/mcp/cubepi_runtime.py:244-250`). It **never reads**
  `static_auth_header_template`, so neither a custom header *name* nor a custom
  template is honored at install/runtime today.
- Feishu remote mode authenticates with **`X-Lark-MCP-UAT`** (user access
  token) and **`X-Lark-MCP-TAT`** (tenant access token) headers against
  `https://mcp.feishu.cn/mcp`, **not** `Authorization: Bearer`. So Feishu is
  **not** a schema-unchanged fit: it needs (a) a configurable static auth header
  *name* and (b) the runtime to actually apply that template.

**v1 (this plan): ship NO new connector source.** Adding the Feishu row now
would surface it in the catalog while every install silently sends the wrong
header (`Authorization: Bearer`) and fails against Feishu — false confidence.
Instead this plan:

1. Records the runtime gap and the corrected Feishu facts (URL + header names)
   so the next editor has accurate ground truth.
2. Refreshes the stale catalog runbook and the stale `template_seed.py` module
   docstring so the docs match the live system.
3. Defers Feishu and every other curated source until the auth plumbing exists
   (see the table below + §"Deferred").

The optional Task 2 sketches the *minimal* auth-plumbing change (configurable
static header name/template + runtime application) for whoever picks up Feishu
next; it is scoped but **not** required to be implemented in this PR.

**Deferred (NOT landed in this plan):**

- **Feishu / Lark** — needs a configurable static auth-header name/template
  (`X-Lark-MCP-UAT` / `X-Lark-MCP-TAT`) **and** runtime application of that
  template (the runtime currently ignores it). Correct endpoint:
  `https://mcp.feishu.cn/mcp`.
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

This plan is therefore deliberately small: it fixes doc drift, records the
runtime auth gap blocking Feishu, and leaves the catalog list unchanged.

## Architecture

The connector catalog is a frozen Python list (`CATALOG`) of
`MCPConnectorTemplateSeedEntry` dataclasses in
`backend/cubebox/mcp/template_seed.py`. `seed_templates()` upserts each entry by
`slug` into the `mcp_connector_templates` table (`MCPConnectorTemplate`,
`backend/cubebox/models/mcp.py`), encrypting any static-OAuth client secret into
a system-level `Credential`, and deprecating DB rows whose slug left the list.
The seeder runs idempotently (lock-guarded) on FastAPI startup
(`backend/cubebox/api/app.py`) and via `python -m cubebox.cli seed-mcp-templates`.
(The `template_seed.py` module docstring still claims it is *not* wired into
startup — that is stale and Task 1 fixes it.)

The admin route `GET /api/v1/admin/mcp/templates` reads the catalog through
`MCPConnectorTemplateService.list_active()`
(`backend/cubebox/services/mcp_templates.py`) and returns the active rows.

**The runtime auth gap.** At connect time, `_resolve_headers_from_spec()`
(`backend/cubebox/mcp/cubepi_runtime.py:244-250`) resolves a `static` credential
by hardcoding `headers["Authorization"] = f"Bearer {plaintext}"`. It does
**not** read the template's `static_auth_header_template`. So the seed entries
that carry a non-Bearer template today (e.g. the `Basic {b64(...)}` entry) are
*not* actually honored at runtime either — the field is stored but unused. Any
connector needing a custom header *name* (Feishu's `X-Lark-MCP-UAT` /
`X-Lark-MCP-TAT`) cannot work until this is fixed.

Adding a connector that fits the current behavior = appending one
`MCPConnectorTemplateSeedEntry` to `CATALOG` whose static auth is a plain
`Authorization: Bearer <token>` header. Anything else (custom header name,
URL-query-param key) needs runtime work first. This plan adds **no** new entry.

## Tech Stack

- Python 3.x, FastAPI, SQLModel / SQLAlchemy async, Postgres (sqlite in-memory
  for the seed unit tests, per `test_catalog_seed.py`).
- `pytest` (async); `uv run pytest` from `backend/`.
- mypy strict; 100-char lines; type annotations everywhere.

---

## Task 1 — Fix the stale `template_seed.py` module docstring

The module docstring (`backend/cubebox/mcp/template_seed.py:18`) still claims
the seeder is "Not wired into FastAPI startup; this is intentionally an explicit
deploy step." That is no longer true — the seeder runs lock-guarded on FastAPI
startup (`backend/cubebox/api/app.py`) **and** via the CLI. Doc-only change in
the module; no behavior change, no catalog change, no test.

**Files**

- Modify: `backend/cubebox/mcp/template_seed.py` (docstring only)

**Steps**

1. **Confirm the live wiring** so the rewritten docstring is accurate:

   ```bash
   grep -n "seed_templates" backend/cubebox/api/app.py
   ```

   Expect a startup call (lock-guarded). Note the function / lock used.

2. **Rewrite the last paragraph of the module docstring** with `Edit` to say
   the seeder runs idempotently on FastAPI startup (lock-guarded) **and** via
   `python -m cubebox.cli seed-mcp-templates`. Keep it ≤ 100-char lines, plain
   English. Do not touch any code outside the docstring.

3. **Verify nothing else changed** and the module still imports:

   ```bash
   uv run python -c "import cubebox.mcp.template_seed"
   ```

4. **Commit:**

   ```bash
   git add backend/cubebox/mcp/template_seed.py
   git commit -m "$(cat <<'EOF'
   docs(mcp): correct template_seed docstring re: startup wiring (#147)

   The seeder now runs lock-guarded on FastAPI startup and via the CLI; the
   module docstring still claimed it was only an explicit deploy step.
   EOF
   )"
   ```

---

## Task 2 (OPTIONAL — only if landing Feishu in this PR) — auth-header plumbing

This task is **not required** for the docs-only v1. Implement it only if the
decision is to ship Feishu in this same PR rather than deferring. It closes the
runtime gap so a connector can carry a custom static auth header name/template.

Whoever picks this up: do **not** rely solely on catalog-visibility tests —
those only prove the row appears in `GET /api/v1/admin/mcp/templates`. They give
false confidence because the runtime ignores `static_auth_header_template`. You
must add a test that exercises **header resolution** so the wrong header can't
ship silently.

**Files**

- Modify: `backend/cubebox/mcp/cubepi_runtime.py` (the `static` branch of
  `_resolve_headers_from_spec`, around lines 244-250)
- Modify: `backend/cubebox/mcp/template_seed.py` (add the Feishu entry once the
  runtime honors a custom header)
- Test (new): `backend/tests/unit/test_mcp_static_header_resolution.py`
- Test (new): `backend/tests/e2e/test_mcp_china_catalog.py` (catalog visibility,
  in addition to the unit header test — not as a substitute for it)

**Steps**

1. **Decide the schema-fitting shape for a custom header.** The template already
   stores `static_auth_header_template` (e.g. `"Bearer {token}"`); the runtime
   just doesn't apply it. The minimal change is: in the `static` branch, if the
   spec carries a header template, render it with the decrypted secret and the
   configured header *name* instead of hardcoding `Authorization: Bearer`. For
   Feishu the header name is `X-Lark-MCP-UAT` (user access token) and/or
   `X-Lark-MCP-TAT` (tenant access token); the endpoint is
   `https://mcp.feishu.cn/mcp`. Confirm whether the existing schema can carry a
   header *name* (it currently only has a value template) — if not, this needs
   a small field addition, which pushes Feishu out of a docs-only PR. Record the
   decision in the spec's §8 before coding.

2. **Write the failing unit test** in
   `backend/tests/unit/test_mcp_static_header_resolution.py`: build a runtime
   spec for a static connector whose template uses a non-`Authorization` header
   and assert `_resolve_headers_from_spec` emits exactly that header (name +
   rendered value), and that it does **not** emit a bogus
   `Authorization: Bearer` for such connectors. Run it, expect fail (the runtime
   currently always sets `Authorization`).

3. **Implement** the `static` branch so it honors the template/header name, then
   re-run the unit test to green.

4. **Add the Feishu seed entry** with the corrected `server_url`
   (`https://mcp.feishu.cn/mcp`) and the correct header name, plus the catalog
   visibility E2E. Run both the unit header test and the E2E.

5. **Commit** in logically separate commits (runtime+test, then seed entry).
   Scope every message `(#147)`.

---

## Task 3 — Fix the doc drift in `mcp_catalog_oauth.md`

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
   - Add one short line under the catalog section noting that **no**
     China-vendor entry ships in v1: Feishu is deferred because its remote mode
     uses custom `X-Lark-MCP-UAT` / `X-Lark-MCP-TAT` headers at
     `https://mcp.feishu.cn/mcp` and the runtime currently ignores
     `static_auth_header_template` (always sends `Authorization: Bearer`).
     URL-query-param-key sources (maps) and stdio/key-pair sources (Alipay,
     DingTalk, WeCom, MiniMax, Tushare) remain deferred pending a
     `static_auth_query_param` field / managed launcher. Cross-reference the
     design doc `docs/dev/specs/2026-05-27-mcp-china-sources-design.md`.

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
   live mcp_connector_templates / template_seed names, and note that no
   China-vendor source ships in v1 (Feishu + all others deferred).
   EOF
   )"
   ```

---

## Task 4 — Pre-PR sweep + self-review

**Files**

- (verification only)

**Steps**

1. **Run the seeder suite** to confirm nothing regressed (the catalog list is
   unchanged in the docs-only v1, so the existing count assertions still hold):

   ```bash
   uv run pytest tests/unit/test_catalog_seed.py tests/unit/test_cli_seed.py -q
   ```

   All must pass. (If Task 2 was implemented, also run
   `tests/unit/test_mcp_static_header_resolution.py` and
   `tests/e2e/test_mcp_china_catalog.py`.)

2. **Type check** the touched module:

   ```bash
   uv run mypy cubebox/mcp/template_seed.py
   ```

3. **Self-review checklist:**
   - [ ] `CATALOG` is unchanged — no Feishu, no deferred source slipped in
         (docs-only v1). If Task 2 was done, Feishu's runtime header is honored
         by a passing header-resolution test, not just catalog visibility.
   - [ ] No new column, migration, route, or service in the docs-only v1.
   - [ ] `template_seed.py` docstring no longer claims "not wired into startup".
   - [ ] Line length ≤ 100; plain English; no placeholder strings.
   - [ ] Doc drift fixed: zero `mcp_catalog_connectors` / `catalog_seed`
         references remain in `mcp_catalog_oauth.md`.
   - [ ] Every commit message scopes `(#147)`; no amend, no push, no codex.

4. **Confirm clean tree** (all work committed):

   ```bash
   git status
   git log --oneline -5
   ```

---

## Deferred (explicitly out of this plan)

Per design doc §6.2 / §7 / §8 plus the runtime gap found in local review, these
are recorded as future work, each blocked on a named gap:

| Source | Blocker | Unblocks when |
|---|---|---|
| Feishu / Lark | runtime ignores `static_auth_header_template`; needs custom header name `X-Lark-MCP-UAT` / `X-Lark-MCP-TAT` (not `Authorization: Bearer`) | runtime applies a configurable static header name/template (Task 2) |
| Amap, Baidu Maps, Tencent Location | API key in URL query param | `static_auth_query_param` field + install-time URL injection (secret stays in vault) |
| Alipay | stdio launch + RSA key-pair auth | managed launcher + key-pair credential kind |
| DingTalk, WeCom, MiniMax (official), Tushare | stdio-only packages | managed launcher or vendor remote endpoint |
| Bailian, ModelScope | marketplaces, not single connectors | curate individual hosted services as their own rows |
