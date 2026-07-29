# Cost Relocation (edition split, stage 2)

**Goal:** Move the cost *reporting* HTTP surface out of the core package and into
`cubeplex_ee`, mounted through `AdminPanelExtension`, so an unlicensed deployment does not
serve those endpoints. The cost *write* path — the middleware that records usage on every run
— stays in core.

**Architecture:** `cubeplex_ee.register()` gains a cost feature module that registers an
`AdminPanelExtension` supplying **only a router**. The host already mounts every registered
extension's router under `/api/v1/admin/_extensions/<package>/`, so nothing in the host
changes — the extension mechanism is the seam.

**Tech Stack:** FastAPI, SQLModel/Postgres (no schema changes), the `cubeplex-ee` path package
added in the scaffold branch.

**Spec:** [`docs/dev/specs/2026-07-07-oss-ee-split-design.md`](../specs/2026-07-07-oss-ee-split-design.md)
§11, delivery stage 2. This plan **corrects that spec in two places** — see "Spec
corrections".

**Base:** stacked on `feat/2026-07-28-licensed-package-scaffold` (PR #446), because the
package this fills does not exist on `main` yet.

**Revised after plan review.** Eight findings, all verified against the code before being
accepted. The three that changed the design are called out inline: the nav item is dropped
entirely, the existing test file gets split rather than skipped wholesale, and a licensed
backend CI lane is added. A fourth found a consumer this plan had missed.

## Spec corrections

Two statements in §11 do not survive contact with the code. Both were found by reading the
imports before planning, and both change the shape of the work.

### 1. `repositories/billing.py` cannot move

§6 lists it among the files moving to the licensed package. It cannot: the OSS write path
imports it. `cubeplex/middleware/cost.py:38` does
`from cubeplex.repositories.billing import BillingRepository`, and the class holds two
disjoint halves:

| Half | Methods | Caller |
|---|---|---|
| write | `insert_llm_event`, `record_fallback_failure` | `middleware/cost.py` — stays core |
| read | `get_workspace_spend`, `get_org_spend`, `get_timeseries`, `stream_events_for_export` | `routes/v1/cost.py` — moves |

Options, and the call:

- **(a) Split the class** — write half stays, the four read methods move. Purer boundary, but
  it cuts one cohesive repository across a licence line and the moved half still needs the
  core `BillingEvent` / `LlmBillingEvent` models.
- **(b) Move only the HTTP surface** — routes and response schemas move; the repository stays
  in core in full. The aggregate SQL sits unused in an unlicensed build.

**Taking (b).** The precedent is set and accepted in this design: §11 keeps the
`sso_connection` / `external_identity` tables in the core migration lineage and calls two
unused empty tables "an honest, cheap wart" rather than paying the cross-package tax. Unused
read methods are the same trade — inert without a route to call them.

Review confirmed this is not a licensing bypass: the billing response schemas are consumed
only by `cost.py`; no other HTTP route exposes those aggregates or the CSV export. Callable
Python methods with no route in front of them are not a reachable surface. It also confirmed
the billing models and migrations must stay core — the write middleware uses them and
user/workspace deletion maintains their foreign-key rows — so **no migration is needed**.

### 2. There is no `cubeplex.cost_middleware` entry-point group to add

§11 says to add one. Stage 0 retired entry-points discovery entirely (§2.7, §9) — there is no
group mechanism left.

This plan does **not** build an enhanced middleware. Nothing exists to swap in, so a seam for
it would be dead scaffolding. Scope here is the read surface only. When an enhanced cost
middleware is actually specified it needs a registry slot of its own
(`register_cost_middleware` alongside the existing five), and `run_manager.py:3112` — which
imports `CostMiddleware` directly — has to consult the registry instead. That is a separate,
larger change to a hot path governed by `backend/docs/prompt-cache-discipline.md`, and it does
not belong bundled with a route move.

## The extension provides a router and nothing else

**This is the correction that most changed the plan.** The first draft had
`CostPanel.get_nav_items()` return an Insights entry. That is wrong twice over, and the
mechanism is worth writing down because it is not obvious from the Protocol:

- `AdminSubNav.tsx:166` already hard-codes `{ href: '/admin/insights', … ee: true }`, shown
  only when `/system/info` reports the licensed edition. A manifest entry would be a **second**
  Insights-like link.
- `AdminSubNav.tsx:178` turns manifest nav items into links to
  `/admin/ext/{plugin}/{url_path}`, and that page (`app/admin/ext/[plugin]/[...path]/page.tsx:39`)
  renders an **iframe** pointing at `ext.iframe_base_url + subPath`. The moved router serves
  JSON at `/cost/summary` etc. and has no HTML panel root, so the link would load an API
  response — or a 404 — inside an iframe.

`/admin/ext/*` is a surface for panels that ship their own UI, not an alias for native pages.
Cost reporting has a native page already. So:

```python
def get_nav_items(self) -> list[AdminNavItem]: return []
```

The extension is used purely to mount the API. Navigation stays where it is, gated on edition
as it already is. `GET /admin/_extensions/manifest` therefore keeps returning `[]` even when
licensed, because the manifest endpoint skips extensions with no nav items
(`admin_extensions.py:25`).

## Testing strategy

Three environment states exist, and every test must be explicit about which it belongs to.
Review confirmed there is no state where both suites are silently vacuous.

| State | conftest | What runs |
|---|---|---|
| package absent (default, current CI) | proceeds | core repository tests + the absence test |
| package installed + valid key | proceeds | core repository tests + licensed HTTP tests |
| package installed, no key | **aborts collection** | nothing, deliberately |

Three consequences, each of which the first draft got wrong:

**Split `test_billing_cost_api.py` rather than skipping it.** The file holds 8 tests: four
drive HTTP, four call `BillingRepository` directly and have nothing to do with the licensed
package. A module-level `importorskip` would have silently dropped the repository coverage
that decision (b) exists to preserve.

| Stays on default install (repository) | Moves to the licensed module (HTTP) |
|---|---|
| `test_get_timeseries_workspace_two_workspaces_two_days` | `test_cost_summary_returns_by_user` |
| `test_get_timeseries_weekly_granularity_aggregates_days` | `test_timeseries_workspace_happy_path` |
| `test_get_timeseries_top_n_collapses_to_other` | `test_timeseries_rejects_invalid_dimension` |
| `test_get_timeseries_rank_by_tokens_with_zero_cost` | `test_timeseries_requires_admin` |

**The absence test must skip when the package is present.** Otherwise a correctly licensed
full run cannot pass. Encode the precondition rather than describing it in prose.

**A licensed backend lane is required, not optional.** With CI installing no package, a
module-level skip means the authorisation contract (`test_timeseries_requires_admin`) and the
validation contract (`test_timeseries_rejects_invalid_dimension`) stop being checked anywhere.
Frontend Playwright against a licensed backend does not substitute for those.

## What Must Keep Working

1. Every run still records usage. `middleware/cost.py` and its write calls are untouched.
2. `/admin/insights` keeps working on a licensed deployment, which means the frontend's API
   paths follow the routes. Callers are in `frontend/packages/core/src/api/billing.ts` (five
   literals) — **not** a `useCostData.ts`, which the first draft guessed at.
3. `/admin/cost` stays a frontend redirect to `/admin/insights`, and
   `admin-insights.spec.ts`'s redirect test stays unchanged — the destination is still a
   native page.
4. `GET /api/v1/admin/_extensions/manifest` returns `[]` in both editions (see above).

---

### Task 1: Move the read surface into the licensed package

**Files:**
- Create: `backend/ee/src/cubeplex_ee/cost/__init__.py`
- Create: `backend/ee/src/cubeplex_ee/cost/routes.py` (from `cubeplex/api/routes/v1/cost.py`)
- Create: `backend/ee/src/cubeplex_ee/cost/schemas.py` (from `cubeplex/api/schemas/billing.py`)
- Create: `backend/ee/src/cubeplex_ee/cost/panel.py`
- Modify: `backend/ee/src/cubeplex_ee/__init__.py` — register it
- Delete: `backend/cubeplex/api/routes/v1/cost.py`
- Delete: `backend/cubeplex/api/schemas/billing.py`
- Modify: `backend/cubeplex/api/routes/v1/admin.py` — drop the import and `include_router`

The resulting path is `/api/v1/admin/_extensions/cubeplex_ee/cost/<endpoint>`: the host adds
`_extensions/<name>` where `<name>` is `type(ext).__module__.split(".")[0]` (`app.py:109`),
which for a class in `cubeplex_ee.cost.panel` is `cubeplex_ee`, and the router keeps
`prefix="/cost"`. Both are literals; the licensed route test in Task 4 is what confirms it,
replacing the first draft's throwaway stub-and-boot measurement.

- [ ] **Step 1: Copy, then adjust imports**

The router body moves unchanged, including `_require_org_admin` (defined locally at
`cost.py:51`, so authorisation moves with it). Core imports may stay module-level in a
*feature* module — it is only imported after `register()` runs.

Keep the `admin` tag: `APIRouter(prefix="/cost", tags=["admin", "cost"])`. Mounting directly
loses the parent router's tag, and Swagger grouping should not change as a side effect of a
relocation.

- [ ] **Step 2: Write the panel**

```python
class CostPanel:
    def get_router(self) -> APIRouter | None: ...
    def get_nav_items(self) -> list[AdminNavItem]: return []   # see above — deliberate
    def get_static_path(self) -> Path | None: return None
```

- [ ] **Step 3: Unmount from core, then mypy both projects**

```bash
cd backend && uv run mypy cubeplex 2>&1 | tail -1
uv run mypy ee/src/cubeplex_ee 2>&1 | tail -1
```

---

### Task 2: Split the existing test file

**Files:**
- Modify: `backend/tests/e2e/test_billing_cost_api.py` — keep the four repository tests
- Create: `backend/tests/e2e/licensed/test_cost_routes.py` — the four HTTP tests
- Create: `backend/tests/e2e/licensed/__init__.py` if the directory needs one

- [ ] **Step 1: Move the four HTTP tests, retargeted at the new path**

Head the new module with:

```python
pytest.importorskip("cubeplex_ee", reason="cost reporting lives in the licensed package")
```

- [ ] **Step 2: Confirm the repository tests still run on a default install**

```bash
cd backend && uv run pytest tests/e2e/test_billing_cost_api.py --no-cov -q 2>&1 | tail -3
```

Expected: 4 passed, 0 skipped. If it reports skipped, the split leaked an import.

---

### Task 3: The absence test

**Files:**
- Create: `backend/tests/e2e/test_cost_routes_absent_by_default.py`

- [ ] **Step 1: Guard it to the unlicensed state**

```python
if importlib.util.find_spec("cubeplex_ee") is not None:
    pytest.skip("licensed package installed; absence is not the contract here",
                allow_module_level=True)
```

- [ ] **Step 2: Assert both paths 404 for an authenticated admin**

Old `/api/v1/admin/cost/summary` and new
`/api/v1/admin/_extensions/cubeplex_ee/cost/summary`. Authenticated deliberately: a 401 would
prove nothing about whether the route exists. The old-path assertion catches an accidentally
retained core mount; the new-path one proves the licensed surface was not registered.

- [ ] **Step 3: Assert the unlicensed OpenAPI schema omits them**

Same module. Extension routes are added during lifespan, so `/openapi.json` reflects edition —
worth pinning in both directions rather than leaving it to chance.

---

### Task 4: Handle the real-LLM consumer this plan had missed

**Files:**
- Modify: `backend/tests/e2e/test_billing.py`

`test_cost_summary_endpoint_returns_data` (line ~99) polls `/api/v1/admin/cost/summary`. The
module is `pytest.mark.real_llm`, so Task 7's `-m "not real_llm"` sweep would never have
caught it and the nightly lane would have started 404ing after the move.

Its invariant is "a real run writes usage that reporting can read", which straddles the
boundary: the write is core, the read is licensed.

- [ ] **Step 1: Keep the core half in the real-LLM lane**

Change it to assert the billing row was written — query `BillingRepository` or the table
directly — rather than reading it back through an HTTP endpoint that no longer exists by
default. The invariant worth guarding nightly is that a real run records usage.

- [ ] **Step 2: Leave the read-back contract to the licensed module**

Task 2's HTTP tests already cover the reporting side against seeded data, which does not need
a real LLM call.

---

### Task 5: Point the frontend at the new prefix

**Files:**
- Modify: `frontend/packages/core/src/api/billing.ts` — five literal paths
- Modify: `frontend/packages/web/__tests__/e2e/admin-insights.spec.ts:19` — the direct
  `request.get('/api/v1/admin/cost/export.csv')`

- [ ] **Step 1: One constant, not five literals**

```ts
const COST_API_BASE = '/api/v1/admin/_extensions/cubeplex_ee/cost'
```

So the next relocation is a one-line change.

- [ ] **Step 2: Verify**

```bash
cd frontend && pnpm --filter @cubeplex/core build && pnpm --filter web exec tsc --noEmit
pnpm --filter web test
```

Confirm no `admin/cost` literals survive outside the redirect stub and its test:

```bash
grep -rn "api/v1/admin/cost" --include="*.ts" --include="*.tsx" packages | grep -v node_modules
```

---

### Task 6: CI — two lanes

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Install the licensed package in `frontend-e2e`**

The job already mints a key next to where the backend starts; add
`uv sync --all-extras --group licensed`. Without it `admin-insights.spec.ts` drives a
dashboard whose endpoints do not exist. Both flags — `--group` alone re-resolves and drops the
test dependencies.

- [ ] **Step 2: Add a licensed backend lane**

A step or job that installs the package, mints a key, and runs `tests/e2e/licensed/`. Without
it the authorisation and validation contracts in Task 2 are checked nowhere, since the default
lane skips them.

Cheapest shape: an extra step in the existing `backend-e2e` job after the default run —
`uv sync --all-extras --group licensed`, mint, then `pytest tests/e2e/licensed/`. Reuses the
services already stood up rather than duplicating postgres/redis/rustfs.

---

### Task 7: Docs + spec

- [ ] Spec §6: `repositories/billing.py` no longer moves; §11: no entry-point group, enhanced
      middleware out of scope with the reason recorded.
- [ ] `docs/site/docs/admin/cost-tracking.md` — reporting requires a licence; what gets
      recorded does not change.
- [ ] `docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/admin/cost-tracking.md`
      — the same qualification. It mirrors the English page and would otherwise contradict it.

---

### Task 8: Pre-PR sweep

- [ ] `uv run mypy cubeplex` + `uv run mypy ee/src/cubeplex_ee`
- [ ] `uv run pytest tests/unit tests/integration --no-cov`
- [ ] `uv run pytest tests/e2e --no-cov -m "not real_llm"` — watch for anything that imported
      `cubeplex.api.schemas.billing`
- [ ] **Collect the real-LLM lane too** (`-m real_llm`, or at minimum
      `tests/e2e/test_billing.py`) — the default sweep excludes it, which is how Task 4's
      consumer stayed hidden
- [ ] Licensed lane locally: install, mint, `pytest tests/e2e/licensed/`
- [ ] Frontend: core build, `tsc --noEmit`, lint, vitest

## Self-Review

- **Both spec corrections came from reading imports, not from trusting the spec.** §6 would
  have had me move a repository the write path depends on; §11 would have had me add an
  entry-point group stage 0 deleted.
- **Plan review changed the design in three places and caught a missed consumer.** The nav
  item was actively harmful (duplicate link into an iframe against a JSON API), the
  wholesale `importorskip` would have silently dropped the repository coverage that decision
  (b) exists to preserve, and there was no lane anywhere that would run the licensed
  authorisation contract. `test_billing.py`'s real-LLM consumer was invisible to the sweep I
  had planned.
- **Verified as sound, so left alone:** decision (b) is not a bypass; no migration is needed;
  the mount move does not weaken CSRF, exception handling, middleware ordering, rate limiting
  or trailing-slash behaviour (all app-wide; these endpoints are GET-only with no rate-limit
  decorators); `StreamingResponse` works from an extension-mounted router; the `/admin/cost`
  redirect stub and its test stay untouched.
- **Known risk — this is the first time the split moves a URL** rather than hiding a page. Any
  external script against `/api/v1/admin/cost/*` breaks. Nothing outside our own frontend
  should be calling admin cost endpoints, but it belongs in the PR description.
- **Not in scope, deliberately:** the enhanced cost middleware, the audit collapse (spec
  §12.1), and the trace viewer.
