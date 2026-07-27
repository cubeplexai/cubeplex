# OSS/EE Split: Editions, Licensing, and the EE Boundary

**Status:** Accepted
**Author:** xfgong
**Date:** 2026-07-07 (revised 2026-07-27)
**Scope:** Define cubeplex's two editions (OSS and Enterprise), the license-key
mechanism that separates them, where the boundary is enforced in backend and
frontend, and the repository licensing structure — before the first public
open-source release.

**Revision history:**

- **2026-07-07** — original decisions (§2.1–2.4).
- **2026-07-27** — follow-up review. Added §2.5–2.7 (EE source is public; one
  `cubeplex-ee` wheel; entry-points discovery is replaced by a single optional
  import). Added §6, the route-and-page inventory that closes the "what is in
  the OSS build" question. Rewrote §8.1 — enforcement moves off
  `PluginRegistry.discover()`. Corrected §10 from five gated pages to four.
  Settled the EE alembic lineage question in §11. Recorded two defects in §12.
- **2026-07-27 (later)** — surveyed prior art (§9, "Prior art"): four comparable
  Python projects all use the same top-level-module + `try: import` gate, which
  confirmed the mechanism. That survey also corrected §8: the top-level package
  name follows convention rather than being structurally required, and whether
  the OSS image contains EE code is a deferrable build decision, not an
  architectural one. Separate distribution remains the starting point.

## 1. Problem

cubeplex already contains a production-grade CE/EE plugin seam
(`backend/cubeplex/plugins/`): six protocol interfaces (`AuthProvider`,
`PermissionChecker`, `AuditSink`, `UserDirectorySyncer`, `AdminPanelExtension`,
`PluginManifest`), entry-points-based discovery of external wheels, and CE
default implementations. External plugins mount routes and admin nav without
touching OSS code.

But the boundary is unfinished in four ways:

1. **No enforcement.** "EE = install a wheel" is a gentleman's agreement. Any
   user can write a plugin (or call EE endpoints directly) and get SSO, audit
   sinks, and directory sync for free.
2. **EE-tier features live in the OSS core.** The SSO data layer
   (`models/sso_connection.py`, `models/external_identity.py`, `sso/`, the SSO
   routes) runs alembic migrations in the main package. Cost reporting routes
   mount unconditionally.
3. **The frontend has zero edition awareness.** Nothing distinguishes builds;
   an OSS user navigating to an EE admin page would hit bare API errors.
4. **No repository licensing.** There is no LICENSE file, no `ee/` boundary,
   and no defined key mechanism.

Before open-sourcing, we need to decide what the OSS build is, make the
boundary real, and do it without a large refactor — the plugin seam already
carries most of the weight.

## 2. Decisions

Fixed 2026-07-07:

1. **Multi-org is an EE gate.** OSS deployments are limited to one
   organization. Implemented as a count check at the org-creation entrypoint,
   not by touching the org/workspace scoping foundation (which stays OSS —
   it is load-bearing for everything).
2. **All IM connectors are OSS.** (Revised the same day; supersedes an initial
   "all-EE" call.) The full connector matrix and IM admin stay in the OSS
   build; IM is adoption/retention surface, not the paid boundary. No
   `cubeplex.im_platform` entry-point group is needed — the existing
   `im/registry.py` + hard-coded connector imports stay as they are.
3. **License-key validation ships before the open-source release.** No
   honor-system launch: startup verifies a signed key and refuses to run EE
   code without one.
4. **Core is Apache-2.0; EE lives in a monorepo `ee/` directory under a
   commercial EULA** (GitLab/n8n model). Single repo keeps CI, migrations, and
   solo maintenance sane; the boundary is the directory plus the license text
   plus wheel packaging.

Added 2026-07-27:

5. **EE source code is public.** `ee/` sits in the public repository,
   source-visible under the EULA. This is the n8n/GitLab posture: readable,
   not freely usable in production. It follows from §2.4 — a monorepo cannot
   hide `ee/` without making the whole repository private — and is accepted
   deliberately rather than as a side effect.
6. **One EE wheel, not several.** EE ships as a single `cubeplex-ee`
   distribution built from `ee/`, exposing a top-level `cubeplex_ee` package.
   The earlier sketch of `cubeplex-ee-sso` / `-audit` / `-cost` is dropped:
   with one licensee-facing key and per-feature checks living inside EE code
   (§7), multiple wheels multiply release overhead for no customer-visible
   benefit.
7. **Entry-points discovery is replaced by a single optional import.** The six
   `cubeplex.*` entry-point groups, the `PluginManifest` validation, and the
   `resolve_singular` / `resolve_plural` arbitration exist to mediate between
   an unknown number of third-party plugins from unknown authors. §2.4 and
   §2.6 collapse that population to exactly one first-party package on the
   same release train, so the machinery no longer earns its cost. The six
   Protocols and the CE defaults stay — they are the dependency inversion that
   lets OSS code call EE without importing it. See §9.

## 3. Goals

1. A crisp, enforced definition of the OSS build: agent runtime, sandboxes,
   skills, artifacts, memory, MCP, IM connectors, email/password auth, flat
   admin/member roles, one organization — complete and useful on its own.
2. Offline license verification: no phone-home, no license server, works
   air-gapped. A key is a signed statement of licensee + features + expiry.
3. The backend is the single edition authority. The frontend renders what
   `/api/v1/system/info` says; it contains no license logic.
4. EE ships as a separate Python wheel. Installing or removing it never
   modifies OSS code or data.
5. Graceful OSS UX: EE surfaces are hidden from nav and gated with an
   explanatory card on direct navigation — never a bare API error.

## 4. Non-Goals

- **DRM.** The source is open; a determined operator can patch out any check.
  Enforcement is a legal/contractual boundary plus honest-operator friction,
  same as GitLab EE. We do not obfuscate, and we do not phone home.
- **Seat counting / usage metering** in the first cut. The key carries a
  feature set and expiry; per-seat enforcement can come later without format
  changes (add a claim).
- **A license issuance service.** Keys are signed offline with a founder-held
  private key; the first customers get keys generated by a CLI tool.
- **Feature-flag granularity in the frontend** beyond edition + a features
  array. Per-feature UI gating can be layered on `features` later.
- **Third-party plugin extensibility.** Dropping entry-points discovery (§2.7)
  gives up the public extension API: a customer wanting a bespoke auth
  provider must fork, or ask us to build it in `ee/`. With no customers today
  this is the right trade, and it is reversible — re-adding entry-points later
  is purely additive, because the Protocols do not change.
- **Deleting breadth.** Nothing in the product is removed; EE code moves
  behind the boundary rather than being rewritten.

## 5. Edition Model

| Capability | OSS (Apache-2.0) | EE (commercial) |
|---|---|---|
| Agent runtime, sandboxes, skills, artifacts, memory, MCP | ✓ | ✓ |
| Org/workspace scoping (`OrgScopedMixin`, `ScopedRepository`) | ✓ (foundation, stays) | ✓ |
| Email/password auth (`DefaultAuthProvider`) | ✓ | ✓ |
| Google social login | ✓ | ✓ |
| Flat admin/member roles (`DefaultPermissionChecker`) | ✓ | ✓ |
| Audit → structlog no-op sink (`DefaultAuditSink`) | ✓ | ✓ |
| Number of organizations | exactly 1 | unlimited (`multi_org` feature) |
| SSO (SAML/OIDC) + external identities | — | ✓ |
| Fine-grained RBAC (third+ roles) | — | ✓ |
| Persistent audit sinks | — | ✓ |
| Trace viewer (`admin_traces` / Tempo) | — | ✓ |
| Cost reporting/insights (read path) | — | ✓ |
| Cost write path (basic counters, `CostMiddleware`) | ✓ | ✓ (enhanced) |
| IM connectors (full matrix) + IM admin | ✓ | ✓ |

The org/workspace multi-tenant *architecture* stays OSS because it is the
foundation, not a feature: 15 repository classes subclass `ScopedRepository`
and 24 model classes carry `OrgScopedMixin` (counted 2026-07-27 — an earlier
draft said "34 repositories", which was wrong). What EE sells on top is the
governance evidence: SSO, RBAC, audit, and running multiple isolated orgs in
one deployment.

## 6. Surface Inventory

The table above says what each edition can do. This is the file-level inventory
of what actually moves — verified against the tree on 2026-07-27, and the
answer to "what is in the OSS default build".

**Admin pages.** OSS keeps eleven: `settings`, `members`, `models`, `presets`,
`web-tools`, `skills`, `skill-registries`, `mcp`, `im`, `sandbox`,
`sandbox-env` — plus `/admin/ext/*`, the plugin mount point, which is OSS
infrastructure that stays empty until EE is installed. Three move to EE:
`authentication`, `insights`, `traces` (including `traces/[traceId]`).

Two things worth knowing about those three:

- `/admin/authentication` is **pure SSO** — `getOrgSso` plus
  `SSOConfigForm` / `SSOStatusPanel` / `SSOIdentitiesList`, nothing else.
  Password policy is config-driven and surfaced through
  `/system/info.password_policy` with no admin page, and Google social login
  has no admin page either. Gating the whole page therefore hides no OSS
  functionality.
- `/admin/insights` **is** the cost page. Everything under it is cost and token
  reporting (`useCostData`, `components/admin/insights/cost/*`, i18n namespace
  `adminInsights.cost`). `app/admin/cost/page.tsx` is a five-line
  `redirect('/admin/insights')` stub, and `AdminSubNav` has no cost entry.

**Backend route modules.** Four move to EE: `admin_sso.py`, `sso.py`,
`cost.py`, `admin_traces.py`. Everything else stays, including
`social_login.py` (Google) and `admin_extensions.py` (the plugin mount point).

**Backend data and service layer moving to EE:** `models/sso_connection.py`,
`models/external_identity.py`, `sso/`, `schemas/billing.py`,
`repositories/billing.py`.

**Gates that add a check without moving code:** the multi-org count check, and
license verification at EE load.

So the entire paid surface is **three admin pages and four route modules**.
That makes the relocation tractable. It also states plainly the whole of what a
buyer sees, which bears on whether the boundary is compelling — see §12.2.

## 7. License Key Design

**Format:** `CBX1.<b64url(payload-json)>.<b64url(signature)>`

- Ed25519 signature over the exact payload bytes (no JSON canonicalization
  needed — verify what was signed).
- Payload claims: `licensee: str`, `features: list[str]`,
  `issued_at`, `expires_at` (ISO-8601, timezone-aware, required).
- The production public key is embedded as a constant in
  `cubeplex/plugins/license.py`. The private key never enters the repo.
- Version prefix `CBX1` allows a future format change without ambiguity.

**Configuration:** `license.key` (`CUBEPLEX_LICENSE__KEY`). A second key,
`license.public_key_hex` (`CUBEPLEX_LICENSE__PUBLIC_KEY_HEX`), overrides the
embedded signer public key so tests and dev environments can mint their own
keys with `backend/scripts/dev/license_keygen.py`; production deployments
never set it.

**Semantics:**

- No key configured → edition `oss`. All CE defaults active.
- Valid key → edition `ee`; `features` come from the key.
- Configured but invalid/expired key → log a warning, run as `oss`
  (misconfiguration must not brick a deployment that isn't using EE code).
- Invalid/missing key **while `cubeplex-ee` is installed** → `RuntimeError` at
  startup. Fail-fast is deliberate: silently dropping an SSO `AuthProvider`
  would lock every user out in a far more confusing way.

**Named features (initial):** `multi_org`. SSO, audit, and RBAC are implicitly
licensed by "any valid license is required to load EE at all"; finer
per-feature checks live inside `cubeplex_ee` and can be added without touching
OSS code. The parsed `License` is handed to `cubeplex_ee.register()` (§9) so EE
code does not re-read global config to make those checks.

## 8. Enforcement Points

1. **EE load** — at app startup, the single optional import of `cubeplex_ee`
   (§9). If the import succeeds and `load_license()` returns nothing, refuse
   startup with an actionable message (set the key, or uninstall the wheel).
   This is one condition in one place; it replaces the earlier design, which
   introspected plugin manifests inside `PluginRegistry.discover()`.
2. **Multi-org** — the only org-creation entrypoint (onboarding full mode)
   calls `ensure_additional_org_allowed()` in `single_tenant` mode: a second
   org without the `multi_org` feature → HTTP 403
   `multi_org_requires_license`. The existing single-tenant startup
   consistency check (refuse >1 orgs) gains the same license bypass.
   `multi_tenant` mode (hosted cloud, one org per user by design) is not
   gated — it is our own licensed deployment, and gating it would break the
   per-user bootstrap for zero enforcement value.
3. **Route surface** — after the follow-up stages (§11), EE routes do not exist
   in an OSS deployment, because the code that mounts them lives in
   `cubeplex_ee` (`AdminPanelExtension.get_router()` /
   `AuthProvider.get_auth_routers()`). Route-level enforcement is therefore
   structural, not conditional.

Two things about the package name that are easy to conflate, so they are
separated here.

**Top-level `cubeplex_ee`, not a `cubeplex.ee` subpackage.** The reason is
convention, not mechanism: every comparable project uses a top-level name (see
"Prior art" in §9). A `cubeplex.ee` subpackage would additionally require
`cubeplex` to become a PEP 420 namespace package to be separately
distributable, and it is a regular package today
(`backend/cubeplex/__init__.py` exists, `packages = ["cubeplex"]` in hatch
config). Mixing regular and namespace packages is fragile for no gain.

**Whether the OSS image contains EE code is a build decision, not an
architectural one, and it is deferred.** Import name and distribution boundary
are independent axes: `cubeplex_ee` can ship inside the OSS wheel or beside it,
and the gate in §9 is byte-identical either way. PostHog demonstrates this —
their OSS image contains `ee/` and the license check is the operative gate,
while their scrubbed `posthog-foss` build omits the directory entirely and runs
the same `try: … except ImportError` code path. We start with a separate
distribution, because absence-by-filesystem is one fewer thing to remember than
a license check at every mount point. If packaging that separately proves
annoying, moving to "ships in the image, license check is the gate" changes no
application code.

## 9. The Plugin Seam After Simplification

What stays, because it is the boundary itself:

- `plugins/protocols.py` — the six Protocols and
  `CUBEPLEX_PLUGIN_API_VERSION`. This is the dependency inversion that lets
  OSS call into EE without importing it.
- `plugins/defaults/` — the CE implementations bound when EE is absent.
- The registry as a binding point: `get_auth_provider()`,
  `get_permission_checker()`, `get_audit_sinks()`,
  `get_user_directory_syncers()`, `get_admin_panel_extensions()`, and the
  module singleton. Call sites across the codebase depend on these.
- `AdminPanelExtension` route/nav/static injection, and
  `api/routes/v1/admin_extensions.py`. How an extension object is *discovered*
  is independent of how it is *mounted*; mounting is unchanged.

What goes, with line counts against the 260-line `registry.py`:

| Removed | ~lines | What it defended against |
|---|---|---|
| `discover()`, `_dist_name()`, the six group constants, `RESERVED_NAME` | 65 | wheels of unknown provenance needing runtime discovery |
| `resolve_singular` / `resolve_plural` | 59 | several candidates competing for one Protocol slot |
| `bind_defaults` config plumbing (`selected` / `disabled`) and `_cfg` | 35 | an operator picking among third-party implementations by name |

Roughly 160 of 260 lines. `bind_defaults` keeps its "instantiate the CE
defaults" half.

The replacement lives where `discover()` is called today
(`backend/cubeplex/api/app.py:91`):

```python
try:
    import cubeplex_ee
except ImportError:
    pass  # OSS build
else:
    lic = load_license()
    if lic is None:
        raise RuntimeError(
            "cubeplex-ee is installed but no valid license key is configured; "
            "set license.key (CUBEPLEX_LICENSE__KEY) or uninstall the wheel"
        )
    cubeplex_ee.register(registry, license=lic)
```

`cubeplex_ee.register()` is the single EE entry point: it binds its
`AuthProvider`, appends its `AuditSink`, and registers its
`AdminPanelExtension`s on the registry it is handed.

**Test consequences.** `backend/tests/plugins/` is 913 lines. About 611 of
them — `test_contracts.py` (351), `test_registry_manifest.py` (72),
`test_registry_singular.py` (72), `test_registry_plural.py` (62),
`test_registry_getters.py` (54) — exercise scenarios that can no longer occur:
two auth providers competing, a missing manifest, an `api_version` mismatch, a
reserved-name collision, `disabled` filtering by entry-point name. Under
`docs/testing.md` these no longer protect a business invariant, so they should
be deleted rather than ported. `test_protocols.py` (148) and the
`test_default_*` files stay. `tests/e2e/test_plugin_architecture_e2e.py` needs
reading before a decision — it likely builds synthetic wheels, and should
become "boots with EE installed / boots without".

While that directory is being touched, `tests/plugins/` should also move into
the `unit/` / `integration/` / `e2e/` taxonomy `docs/testing.md` requires; it
currently sits outside all three.

### Prior art

Surveyed 2026-07-27, because the gate above looks almost too simple to be the
real mechanism. It is the real mechanism — four comparable Python projects all
use a top-level module name plus `try: import … except ImportError`, and none
uses a `<core>.ee` subpackage.

- **Label Studio** (Django; paid tier is RBAC + SSO/SAML + audit logs, nearly
  our exact bundle) ships EE as a separate closed distribution and detects it in
  `label_studio/core/utils/common.py`:

  ```python
  def is_community():
      try:
          import label_studio_enterprise  # noqa: F401
          return False
      except ImportError:
          return True
  ```

- **PostHog** keeps `ee/` in the same public repo under its own license (MIT
  core), and detects it in `posthog/settings/web.py` with
  `try: from ee.apps import EnterpriseConfig / except ImportError: pass`. The
  package is top-level `ee`, not `posthog.ee`, even though it ships with the
  core. A separate `posthog-foss` mirror repo carries the same code with `ee/`
  removed. Their license check calls a billing service rather than verifying
  offline — we deliberately diverge there (§7: offline Ed25519, so air-gapped
  deployments work).
- **Odoo** puts Enterprise addons in separate subscriber-only repositories,
  loaded from the addons path and keyed to a subscription.
- **Sentry** is the useful counter-example: `getsentry` is a separate closed
  Django app that imports the open `sentry` app and hooks in through Django
  signals — but they are explicitly *not* open-core, keeping all product
  features in the OSS repo and only billing/account management closed. Same
  mechanism, a very different boundary. Worth knowing that the mechanism does
  not commit us to a particular strategy.

## 10. Frontend Edition Surface

- `GET /api/v1/system/info` (public, already cached client-side) gains
  `edition: "oss" | "ee"` and `features: string[]`.
- `@cubeplex/core` gains `useEdition()` (subpath export, same SWR key as
  `useDeploymentMode` — zero extra requests) returning
  `{ edition, features, hasFeature, loading }`.
- `<EEGate>` wraps EE admin pages: children when `ee`, an "Enterprise
  feature" card otherwise. Applied to **four** page files:
  `/admin/authentication`, `/admin/insights`, `/admin/traces`, and
  `/admin/traces/[traceId]`. `/admin/cost` is deliberately excluded — it is a
  bare `redirect()` stub, so a gate there would never render (the redirect
  fires first); its destination is gated instead. `/admin/im` stays ungated
  (IM is OSS).
- `AdminSubNav` entries carry an `ee: true` flag and are filtered out unless
  the edition is `ee`. Three entries are tagged: Authentication, Insights,
  Traces. EE injects its own nav via the `AdminPanelExtension` manifest
  endpoint, so the long-term OSS nav simply has no EE entries at all; the flag
  covers the transition period while EE pages still live in the web package.

The frontend never decides edition; it mirrors the backend. There is no
feature-flag framework, no license parsing, and no edition build variant in
the web bundle.

## 11. Repository & Delivery Structure

```
LICENSE                  Apache-2.0 (root; governs everything outside ee/)
ee/
  LICENSE                commercial notice (EULA; lawyer pass before first sale)
  README.md              what lives here, how the wheel is built
  pyproject.toml         name = "cubeplex-ee"
  src/cubeplex_ee/
    __init__.py          register(registry, *, license) -> None
    sso/ cost/ audit/    EE features, added stage by stage
```

`ee/` is the **source location and license boundary**; `cubeplex_ee` is the
**installed package name**. They are deliberately different — see the end of §8
for the naming rationale.

**Dev-environment wiring is unresolved in detail.** As of 2026-07-27 there is
no root `pyproject.toml` and no uv workspace: the backend is a single
hatchling package at `backend/pyproject.toml` with a flat (non-src) layout, so
`ee/` cannot simply be declared a workspace member. Two options, to be settled
when Task 1 of the stage-1 plan runs:

- Add a `[tool.uv.sources]` path entry plus an optional dependency group in
  `backend/pyproject.toml`, pointing at `../ee` as editable. No restructuring;
  the preferred starting point.
- Introduce a root `pyproject.toml` with
  `[tool.uv.workspace] members = ["backend", "ee"]`. Conceptually cleaner, but
  it moves the lockfile and changes how backend dependencies resolve — a real
  toolchain change for a packaging detail.

Either way, dev machines run EE with a locally minted license key, and the OSS
container image simply does not install `cubeplex-ee`. And per §8, if separate
packaging turns out to cost more than it is worth, shipping `cubeplex_ee` inside
the OSS image and letting the license check be the gate is a build-config change
with no application-code consequences — this is not a decision the codebase gets
locked into.

**EE migration lineage: one shared alembic lineage.** EE tables
(`sso_connection`, `external_identity`) keep their migrations in the main
lineage, so an OSS deployment carries two unused empty tables. This is what
GitLab does — the CE schema contains EE tables — and it retires the
alternative (a second version table per distribution, or a branched lineage)
along with the cross-package migration-ordering tax that would come with it.
Two empty tables is an honest, cheap wart; migration ordering across
independently released packages is not.

**Delivery stages** (each independently shippable):

0. **Plugin seam simplification** —
   [`docs/dev/plans/2026-07-27-plugin-seam-simplification.md`](../plans/2026-07-27-plugin-seam-simplification.md):
   delete entry-points discovery and arbitration, keep the Protocols, replace
   with the single optional import. **This is a prerequisite of stage 1**,
   because stage 1's license check attaches to the import gate; doing it
   afterwards means writing that check twice.
1. **Edition foundation** —
   [`docs/dev/plans/2026-07-07-oss-ee-edition-foundation.md`](../plans/2026-07-07-oss-ee-edition-foundation.md):
   license module + startup enforcement + `/system/info` edition + multi-org
   gate + frontend scaffolding + repo licenses.
2. **Cost extraction** — add the `cubeplex.cost_middleware` seam (OSS keeps
   the basic counter middleware; EE swaps in the enhanced one) and move the
   cost read path (`cost.py` routes, billing schemas and repo reports) into
   `cubeplex_ee`, mounted via `AdminPanelExtension`. Plan to be written.
3. **SSO relocation** — move `SSOConnection` / `ExternalIdentity` models,
   `sso/`, and SSO routes into `cubeplex_ee`, and decouple `social_login`
   (Google, stays OSS) from `sso/identity.py:resolve_identity` so it no longer
   takes an `SSOConnection`. With the lineage question settled above, this is a
   relocation plus one contained refactor, not migration surgery. Plan to be
   written.

Ordering rationale: stage 0 shapes the seam stage 1 enforces on; stage 1 builds
the machinery stages 2–3 gate on; stages 2–3 are relocations once the machinery
exists. §12.1 should land before stage 2.

## 12. Defects Found While Taking the Inventory

Both were found on 2026-07-27 while walking the surface in §6. Neither is
caused by the split, but both undermine what EE is meant to sell.

### 12.1 Two parallel audit paths; EE would see only one

There are two unrelated audit abstractions:

- `plugins/audit.py:audit_log()` dispatches to registry-bound sinks. Seven
  call sites: `auth/users.py` (2), `api/routes/v1/auth.py` (2),
  `api/routes/v1/workspaces.py` (2), `api/routes/v1/admin.py` (1).
- `audit/sink.py` defines a *different* `AuditSink` Protocol with a different
  `record()` signature, plus `NoOpAuditSink`, installed on
  `app.state.audit_sink` (`api/app.py:67`) and injected via
  `mcp/dependencies.py:60`. Used only by `admin_mcp.py` and `ws_mcp.py`, and it
  **never consults the plugin registry**.

Consequence: with the EE audit sink installed, MCP audit events — connector
installs, credential operations, precisely the actions an auditor asks about —
reach no persistent sink at all. The two paths must be collapsed into one
before EE audit is sold. This should land before stage 2.

### 12.2 EE audit has no UI

There is no audit entry in `AdminSubNav` and no audit surface anywhere in the
frontend. The paid feature is "persistent audit logs", but what a customer
receives is a database table. A security questionnaire may pass on that; a demo
will not. Either `cubeplex_ee` ships an audit page through
`AdminPanelExtension` — exactly what that Protocol is for — or the first
release scopes the feature honestly as "exportable audit records". Decide
before pricing copy is written.

## 13. Open Questions

- **EULA text** — `ee/LICENSE` ships as an n8n-style notice; needs legal
  review before the first commercial sale (not before open-sourcing).
- **Dev-environment packaging** — path source vs. uv workspace (§11). Settle
  when stage 1 Task 1 runs. Low-stakes: the fallback (ship EE in the OSS image,
  license check is the gate) needs no application changes.
- **Seat limits** — whether the first paid tier needs a `max_users` claim in
  the key, or ships unlimited-seat feature keys. The format supports adding the
  claim later either way.
- **"Insights" naming** — if the page stays cost-only, the broader name blurs
  the EE boundary by implying non-cost content will appear there. Either rename
  it to what it is, or decide what non-cost content it will hold; the answer
  scopes stage 2.
- **Audit UI scope** — §12.2.

Settled since the first draft, recorded so the reasoning is not re-litigated:
the EE alembic lineage (§11 — one shared lineage) and whether EE is one wheel
or several (§2.6 — one).
