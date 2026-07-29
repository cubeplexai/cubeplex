# SSO relocation (stage 3)

Status: plan
Spec: [2026-07-07-oss-ee-split-design.md](../specs/2026-07-07-oss-ee-split-design.md) §11 stage 3

Stage 2 moved cost reporting into the licensed package. This stage moves SAML
and OIDC single sign-on. It is the last of the planned relocations, and the
first one that touches unauthenticated request paths.

**Revised 2026-07-29 after review.** Six things the first draft got wrong, all
verified against the code before being accepted: `sso.py` cannot move wholesale
because OSS Google login lazily imports two helpers out of it (§3.1); deleting
modules needs named cleanup edits or the default install stops importing at all
(§3.2); uninstalling the package with an active connection row locks an org out
of both login methods (§3.3); SSO needs **two** mount objects and rewritten
router prefixes, not one (§4); `test_sso_routes.py` splits rather than moving
(§6); and the public SSO path is written in eight places across two languages,
not one (§8). The namespacing rationale in §4 was also wrong on its own terms —
corrected there after measuring it.

## 1. What "done" means

On a default install (licensed package absent):

- Nothing under `/api/v1/admin/sso/*` or `/api/v1/auth/sso/*` exists — 404, and
  absent from the OpenAPI schema.
- Email/password login, Google social login, and the operator break-glass CLI
  all still work, with their tests in the default lane.
- `alembic revision --autogenerate` produces an empty diff. In particular it
  does **not** want to drop `sso_connection` or `external_identity`.

With the package installed and a valid key: SAML/OIDC login and the admin CRUD
surface behave exactly as they do today.

## 2. Three corrections to the spec

The spec's one-paragraph sketch of this stage does not survive contact with the
code. All three corrections narrow the scope.

### 2.1 The models do not move

Spec §11 says to move "the `SSOConnection` / `ExternalIdentity` models". That
contradicts the lineage decision three paragraphs above it, which settled on one
shared alembic lineage with the EE tables present-but-empty in OSS.

`alembic/env.py` sets `target_metadata = [SQLModel.metadata, cubepi_metadata]`
and populates it by importing `cubeplex.models`. If the model classes leave that
package, then on a default install autogenerate sees two tables in the database
with no model behind them and emits `DROP TABLE`. The next person to run
`alembic revision --autogenerate` would generate a migration that destroys
customer SSO configuration.

So: **both models stay in `cubeplex/models/`.** This is not a compromise forced
by tooling — it is what "one shared lineage" already meant. The spec paragraph
should be corrected to say so.

`ExternalIdentity` has a second, independent reason to stay: it is the link row
for *any* external identity provider, and Google social login (OSS) writes it.

### 2.2 `resolve_identity`'s decoupling is not the one the spec describes

Spec §11 asks to "decouple `social_login` from `resolve_identity` so it no
longer takes an `SSOConnection`". `social_login` already doesn't pass one — the
parameter is `sso_connection: SSOConnection | None = None` and the social path
leaves it `None`. That half is already done.

The decoupling still worth doing is the other direction: core's
`sso/identity.py` currently reads `sso_connection.status`, `.provisioning`, and
`.org_id`, so the *enterprise* policy rules — which statuses may sign in, what
`invite_only` means — are hardcoded in the OSS package. The licensed package
cannot change its own provisioning policy without editing core. §5 replaces the
parameter with a small core-owned value object.

This is a design-cleanliness change, not a blocker: with the model staying in
core (§2.1), the licensed package *could* keep passing `SSOConnection` and
everything would work. It is in scope because leaving it means core keeps
encoding EE business rules, which is the thing this whole split is meant to
stop.

### 2.3 The admin extension seam cannot carry these routes

Stage 2 needed one mount point and `AdminPanelExtension` provided it:
`/api/v1/admin/_extensions/<pkg>/`. SSO needs a second one. Four of its five
public endpoints are part of the *login* flow and therefore unauthenticated:

| Endpoint | Who calls it |
|---|---|
| `POST /api/v1/auth/sso/initiate` | login page |
| `GET /api/v1/auth/sso/oidc/callback` | the IdP, via browser redirect |
| `POST /api/v1/auth/sso/saml/acs` | the IdP, form POST |
| `GET /api/v1/auth/sso/saml/metadata/{sso_id}` | the IdP operator, out of band |

None of these belong under `/admin/`. §4 adds the seam.

## 3. Inventory

Every file that mentions SSO, and where it lands. "→ licensed" means it moves to
`backend/ee/src/cubeplex_ee/sso/`.

### Moves

| File | Lines | Why it moves |
|---|---|---|
| `api/routes/v1/admin_sso.py` | 825 | The EE admin CRUD surface, whole file |
| `api/routes/v1/sso.py` | 574 | **Partially** — three pieces stay, see §3.1 |
| `sso/saml.py` | 178 | Every function takes an `SSOConnection` |
| `sso/attribute_mapping.py` | 73 | Only caller is `sso.py` |
| `repositories/sso_connection.py` | 57 | Only caller is `admin_sso.py` (the CLI uses `select()` directly) |
| `sso/oidc.py:oidc_config_from_connection` | ~15 | The one function in `oidc.py` that takes an `SSOConnection` |

### 3.1 `sso.py` is not a wholesale move

Three things in that file are core, and the review of this plan is what caught
the second one.

- **`_enforce_forced_sso_for_user`** — refuses a login when the user belongs to
  an org with active forced SSO and this flow didn't use it. `social_login.py:171`
  imports it **lazily, inside `google_callback`**. Move the file and OSS Google
  login raises `ImportError` at the moment a user finishes signing in — not at
  startup, where a test would see it. It is also a security control, so shipping
  it in the licensed package and importing it back into core inverts the
  boundary.
- **`_login_and_redirect`** — issues the session cookie via `auth_backend` and
  redirects. Core auth, same lazy import, same failure.
- **`get_org_info`** (`/auth/org-info/{slug}`) — the OSS login page calls it.

Extract before deleting anything. Concretely, a new core module
`cubeplex/auth/external_login.py` — "finish a login that arrived from an external
identity provider", which is exactly what both callers are doing — holding:

| Moved out of `sso.py` | Who calls it afterwards |
|---|---|
| `enforce_forced_sso_for_user` | OSS Google callback (`allowed_org_id=None`); EE SSO callbacks (`allowed_org_id=conn.org_id`) |
| `login_and_redirect` | both, as the last step of a successful callback |
| `_frontend_base_url` | dependency of `login_and_redirect`; EE's `_sso_error_redirect` also needs it |

`get_org_info` is a route, not a helper, so it goes to a small core
`api/routes/v1/org_info.py` with its own router rather than into this module.

No cycle: this module imports `auth.jwt`, models, and config only. It does not
import `api/routes/v1/auth.py`, which is what keeps the existing
`sso/identity.py` → `routes/v1/auth.py` (`UserCreate`) edge harmless.

**`_base_url` is not in the list.** `social_login.py:48` already defines its own
copy, so Google's `redirect_uri` does not depend on `sso.py` at all. The
duplication is pre-existing; flag it, don't fold it into this change.

**The two forced-SSO guards are near-duplicates but not identical**, so do not
merge them here. Both select active connections joined to the user's org
memberships, and both raise 403 `sso_required`. But the password-login guard in
`auth.py` also joins `Organization` to put `login_url: /login/{slug}` in the
response, while this one takes `allowed_org_id` so an SSO callback can satisfy
enforcement for its own org. Unifying them changes a response payload the
frontend reads and needs its own before/after — a separate change.

### 3.2 Cleanup edits, named explicitly

Deleting a module is not the whole edit. These must be in the diff or the
default install won't import at all:

- `repositories/__init__.py:30,60` eagerly imports and re-exports
  `SSOConnectionRepository`. Leaving it turns every `cubeplex.repositories`
  import — most routes and services — into `ModuleNotFoundError`.
- `api/app.py` statically imports and mounts `sso` and `admin_sso`
  (lines ~546, ~585, ~602 and the `admin_sso` include). Both go.
- `models/__init__.py` is **not** touched: the models stay (§2.1).

### 3.3 Uninstalling the package with active rows must fail loudly

The claim that the SSO table is "always empty" on a default install holds for a
fresh install and is **false for a downgrade**. If an org activates SSO and the
package is then removed — a botched upgrade, or a deliberate downgrade — the row
survives in the shared database and the deployment reaches a state where:

- `org-info` still reports `sso_enabled: true`, so the login page shows an SSO
  button,
- password login still refuses with the forced-SSO error,
- and `/api/v1/_extensions/cubeplex_ee/sso/*` 404s.

Every member of that org loses both login methods, with nothing in the logs
naming the cause.

Add the symmetric startup check to the one stage 1 already has. Stage 1 refuses
to boot when the package is installed without a valid key; this refuses to boot
when an `active` or `testing` connection exists and the package is absent. The
error names the two ways out: reinstall the package, or run the break-glass
`disable-sso`, which works precisely because §3 keeps that CLI in core.

Ignoring the row instead would silently disable forced SSO — a security control
downgrading itself on a packaging accident. Fail fast.

### Stays in core

| File | Why |
|---|---|
| `models/sso_connection.py`, `models/external_identity.py` | §2.1 — alembic lineage |
| `repositories/external_identity.py` | `sso/identity.py` writes it for social login |
| `sso/identity.py` | Shared by social login; loses its SSO-model import in §5 |
| `sso/state.py` | `social_login.py` uses `SSOStateStore` for the same CSRF-state job |
| `sso/oidc.py` (rest) | Its docstring already says it serves both persisted connections and Google; the licensed package imports from it |
| `api/routes/v1/social_login.py` | Google login is OSS |
| `api/routes/v1/auth.py` SSO enforcement | Below |
| `cli/admin.py` `disable-sso` / `list-sso` | Below |

**The password-login enforcement query stays.** `auth.py:224` refuses password
login when the user's org has an active `SSOConnection`. On a default install
the table is always empty — there is no route and no CLI command that can create
a row — so the guard never fires, at the cost of one indexed join per password
login. Moving it behind a registry hook would buy back that query and cost a new
Protocol; not worth it. Leave a comment saying why the query is there when the
feature isn't.

**The break-glass CLI stays, and this is deliberate.** `disable-sso` and
`list-sso` exist for the case where SSO is misconfigured and every admin is
locked out of the UI. That is precisely the moment the licensed package might be
the broken thing. Tooling that recovers from a broken SSO config must not live
inside the SSO package. Both commands already use `select(SSOConnection)`
directly against a core model, so they need no change at all.

**`GET /api/v1/auth/org-info/{org_slug}` stays in core**, even though it lives
in `sso.py` today. The login page calls it to decide whether to render an SSO
button, so on a default install it must answer, not 404. It reads a core-owned
table and correctly returns `sso_enabled: false` when that table is empty. It
moves to `api/routes/v1/auth.py`'s router or a small `org_info.py` — decide at
execution; `org_info.py` is likelier, since `auth.py` is already large.

## 4. The seam for non-admin routes

Add a fifth registration slot, mirroring `AdminPanelExtension` one level up in
the URL space.

```python
# cubeplex/plugins/protocols.py
@runtime_checkable
class RouteExtension(Protocol):
    """A router mounted outside the admin surface.

    Mounted under /api/v1/_extensions/<pkg>/, where <pkg> is the top-level
    module name — the same rule AdminPanelExtension uses.
    """

    def get_router(self) -> APIRouter | None: ...
```

Registry gains `register_route_extension()` / `get_route_extensions()`, matching
the existing plural slots. `bind_defaults()` binds **no** default: an OSS
deployment has zero route extensions, and the mount loop over an empty list is
the correct no-op. (`AdminPanelExtension` has a CE default only because the
admin nav manifest endpoint needs something to ask.)

`api/app.py` mounts them next to the admin extensions:

```python
for _ext_obj in _reg.get_route_extensions():
    _ext = cast(RouteExtension, _ext_obj)
    _router = _ext.get_router()
    if _router is not None:
        _app.include_router(
            _router, prefix=f"/api/v1/_extensions/{type(_ext).__module__.split('.')[0]}"
        )
```

### Both mount objects, and the exact final URLs

SSO needs **two** extension objects, not one. The first draft of this section
described only the `RouteExtension` and left the admin CRUD surface unmounted —
`/api/v1/admin/sso/*` would simply have stopped existing while §8 pointed the
frontend at a path nothing served.

`cubeplex_ee.register()` registers both:

| Object | Protocol | Router prefix | Final URL |
|---|---|---|---|
| `SSOAdminPanel` | `AdminPanelExtension` | `/sso` | `/api/v1/admin/_extensions/cubeplex_ee/sso/*` |
| `SSOLoginRoutes` | `RouteExtension` | `/sso` | `/api/v1/_extensions/cubeplex_ee/sso/*` |

The prefixes must be **rewritten, not inherited**. The routers carry
`prefix="/admin/sso"` and `prefix="/auth"` today; moving them mechanically would
produce `/api/v1/admin/_extensions/cubeplex_ee/admin/sso/...` and
`/api/v1/_extensions/cubeplex_ee/auth/sso/initiate` — neither of which is what
§8 points the frontend at, and the second is not what gets registered with the
IdP. Set both to `/sso` and assert every final URL through a real lifespan
mount, not by reading the source.

Resulting login-flow paths, which are also the strings the IdP is configured
with — see §8 for the eight places they are currently written:

```
POST /api/v1/_extensions/cubeplex_ee/sso/initiate
GET  /api/v1/_extensions/cubeplex_ee/sso/oidc/callback
POST /api/v1/_extensions/cubeplex_ee/sso/saml/acs
GET  /api/v1/_extensions/cubeplex_ee/sso/saml/metadata/{sso_id}
```

### Why a namespaced prefix rather than the current paths

The alternative — let the extension mount at `/api/v1` verbatim, keeping
`/api/v1/auth/sso/*` unchanged — reads as the smaller change.

**Corrected after measuring it.** The first version of this section claimed an
unprefixed extension could shadow a core route and take over a core endpoint.
That is false with the current mount order: core routers register inside
`create_app()`, extensions mount later in lifespan, and Starlette matches in
registration order, so a colliding extension path *loses*. Probed directly by
mounting a stand-in that declares `/api/v1/system/info` without a prefix — the
response still came from core.

The namespace is still the right choice, for two smaller and true reasons.
Losing the collision is itself a silent bug: the extension serves a route that
never receives a request, with no error anywhere. And relying on "core registers
first" makes correctness a load-order property that nothing asserts — one
refactor that mounts extensions earlier would turn it into the hijack the first
draft wrongly claimed. Prefixing removes the question in both directions, and is
symmetric with what stage 2 established.

The cost is that the SAML ACS URL changes, which is an externally-registered
URL — acceptable only because nothing has shipped. Choose the path once here and
treat it as frozen.

### Why not implement `AuthProvider` instead

`AuthProvider.get_auth_routers()` already exists and is already mounted at
`/api/v1`, so it looks like a free ride. It is a trap. The registry's
`register_auth_provider()` is **singular** and raises if called twice, so a
licensed package registering an auth provider *replaces* `DefaultAuthProvider`
— and with it `get_auth_routers()`, which is what mounts core's entire
`/api/v1/auth/*` router: login, register, password reset. The licensed package
would have to import and re-export core's auth router to avoid deleting password
login, and would silently drop any router core adds later.

That failure mode is "installing the enterprise package disables password
login", in the auth path, discoverable only at runtime. A 25-line new slot is
much cheaper than that risk.

SSO also does not need the slot's actual purpose: it issues the same session
cookie core already understands, so `authenticate()` needs no override at all.

### What PR 1 tests

Both halves. The OSS side: the registry binds no route extension by default, and
`/api/v1/_extensions/*` 404s on a real app. The positive side: a stand-in
extension registered before lifespan is reachable under its prefix and nowhere
else.

The positive test was initially deferred to PR 2 on the argument that a real
router would prove the same thing. Codecov disagreed — the mount loop is new
code with no covering test — and it was right to: writing the test is what
surfaced the false shadowing rationale above. `create_app()` is a factory and the
mount happens in lifespan, so registering first costs about twenty lines.

`cubeplex_ee.__init__._Registry` mirrors the host registry so mypy catches drift.
Its `register_route_extension` member lands in PR 2, with the call that needs it.

## 5. Decoupling `resolve_identity`

Replace the `sso_connection: SSOConnection | None` parameter with a value object
that core owns and the licensed package fills in:

```python
# cubeplex/sso/identity.py
@dataclass(frozen=True)
class EnterpriseLoginPolicy:
    """What an enterprise connection asserts about a login. Built by whoever
    owns the connection; core only reads these three answers."""

    org_id: str
    connection_active: bool
    auto_provision: bool
```

Then `resolve_identity(..., policy: EnterpriseLoginPolicy | None = None)` and
`_provision_org_membership(session, user, org_id)`. The three call sites in the
licensed package build the policy from their `SSOConnection`:

```python
EnterpriseLoginPolicy(
    org_id=conn.org_id,
    connection_active=conn.status in {"active", "testing"},
    auto_provision=conn.provisioning != "invite_only",
)
```

Rejection codes (`sso_connection_inactive`, `not_org_member`) and the
`SSOProvisioningDenied` message are unchanged — the frontend matches on those
strings.

After this, `identity.py` imports no SSO model and `test_sso_identity.py` needs
no database row to construct a policy, which is why the rewritten tests get
smaller.

## 6. Test split

Same three-bucket shape as stage 2.

**Stay in the default lane, unchanged:**

- `tests/unit/test_sso_state.py`, `test_sso_oidc.py`, `test_external_identity_repository.py`,
  `test_social_login_routes.py`, `test_sso_avatar_gating.py`, `test_cli_sso.py`,
  `test_sso_models.py`, `test_auth_login_sso_enforcement.py`
- `test_sso_oidc.py` needs one edit: its `oidc_config_from_connection` cases
  follow that function into the licensed lane.

**Rewritten in place** (core, but the signature changed):

- `tests/unit/test_sso_identity.py` — `SSOConnection` rows become
  `EnterpriseLoginPolicy` values.

**Move to `tests/e2e/licensed/` or `tests/unit/licensed/`** behind
`pytest.importorskip("cubeplex_ee")`:

- `tests/unit/test_admin_sso_routes.py` (the largest single file here),
  `test_sso_saml.py`, `test_sso_attribute_mapping.py`,
  `test_sso_connection_repository.py`
- `tests/e2e/test_sso_admin.py` — every test hits `/api/v1/admin/sso`

**`test_sso_routes.py` splits — it does not move wholesale.** It imports
`_enforce_forced_sso_for_user` and `_login_and_redirect`, which §3.1 keeps in
core because OSS Google login calls them. Moving the file would delete
default-lane coverage of a forced-SSO security control. Its `sso_initiate` /
`sso_oidc_callback` / `sso_saml_acs` cases go to the licensed lane; the two
two helper-focused cases stay in the default lane, following their code.

The `_policy_for` tests added in PR 1 go to the **licensed** lane, because
`_policy_for` itself moves: reading `status` and `provisioning` is exactly the
enterprise policy §5 pushed out of core. `EnterpriseLoginPolicy` and its
consumer tests stay in core.

**Split, like `billing_fixtures.py` in stage 2:**

- `tests/e2e/test_sso_enforcement.py`. Its point is the OSS invariant "password
  login is refused when the org has active SSO", and it already writes
  `SSOConnection` rows directly, with a comment saying it bypasses the admin
  route on purpose. Those tests stay in the default lane. Only the one baseline
  test that POSTs `/api/v1/admin/sso/{id}/activate` (lines 68, 84) moves to the
  licensed lane.

**New:**

- `tests/e2e/test_sso_routes_absent_by_default.py` — mirrors
  `test_cost_routes_absent_by_default.py`: skips when the package is present,
  otherwise asserts old and new paths both 404 and that OpenAPI omits them.
- One test that a default install binds zero route extensions, so §4's empty
  list stays the OSS reality rather than an accident.

## 7. CI

The licensed lanes already exist from stage 2 and need no structural change:
`backend-e2e` shard 0 runs a licensed pass with
`env -u CUBEPLEX_E2E_SHARD_INDEX -u CUBEPLEX_E2E_SHARD_TOTAL`, and
`frontend-e2e` installs the package and mints a key.

One change is required, and it is easy to miss. The lane runs exactly
`uv run pytest tests/e2e/licensed/` (`ci.yml:346`), so a `tests/unit/licensed/`
directory would be **collected by nothing** — the tests would neither run in the
default lane (importorskip skips them) nor in the licensed lane (wrong path).
Failing silently in both directions is the worst outcome, so add the path:

```
uv run pytest tests/e2e/licensed/ tests/unit/licensed/ --no-cov -q
```

`tests/unit/licensed/` is the right home for these despite touching a session:
they build their own `sqlite+aiosqlite:///:memory:` engine per test, so they
depend on no external system and belong in `unit/` by the placement rule.

**Corrected: the Makefiles need no change.** This section first said both needed
the path added. `backend/Makefile:102` runs
`pytest --ignore=tests/e2e --ignore=tests/diagnostic`, so it already collects
`tests/unit/licensed/` — where `importorskip` skips it on a default install and
runs it on a licensed one. Only the explicit CI lane, which names its directory,
had to learn the second path.

`admin-sso.spec.ts` and `sso-login.spec.ts` run in `frontend-e2e`, which installs
the package, so they keep passing. The deliberately-deferred OSS/licensed
Playwright split is still deferred; note it, don't fix it here.

## 8. Frontend

**Corrected: `sso.ts` is not the only file.** The first draft said it was. The
public SSO paths are written in **eight** places across two languages, and the
ones a real login depends on most are not in the API client at all.

`frontend/packages/web/components/admin/SSOConfigForm.tsx:231-234` builds the
three URLs an administrator copies into their identity provider:

```ts
const redirectUri   = `${origin}/api/v1/auth/sso/oidc/callback`
const spAcsUrl      = `${origin}/api/v1/auth/sso/saml/acs`
const spMetadataUrl = `${origin}/api/v1/auth/sso/saml/metadata/${connection.id}`
```

and `api/routes/v1/sso.py` writes the same paths five more times (lines 195,
207, 276, 370, 473) to build the `redirect_uri` it sends the IdP and the ACS URL
it puts in SAML metadata. Miss any one and admin CRUD still passes, API client
tests still pass, and real OIDC/SAML login fails at the IdP — where no test
looks.

Centralize the public SSO base path on each side (one constant in the licensed
package, one in the web package) so the API calls, the displayed copy-paste
URLs, the backend-generated metadata, and the tests cannot drift apart.

`frontend/packages/core/src/api/sso.ts` holds the API-client URLs:

- 11 admin calls: `/api/v1/admin/sso/*` → `/api/v1/admin/_extensions/cubeplex_ee/sso/*`
- 1 login call: `/api/v1/auth/sso/initiate` → `/api/v1/_extensions/cubeplex_ee/sso/initiate`
- `getOrgInfo` is unchanged — `org-info` stays in core (§3)

Also:

- `frontend/packages/core/src/api/client.ts:49` lists workspace-neutral path
  prefixes; add `/api/v1/_extensions/cubeplex_ee/sso/`.
- `core/src/api/__tests__/sso.test.ts` asserts exact URLs — update.
- `__tests__/e2e/sso-login.spec.ts:64` mocks `**/api/v1/auth/org-info/…` —
  unchanged, since that endpoint stays. Its **initiate** mock in the same file
  does change, as do the old-path mocks in `admin-sso.spec.ts`.
- After the move, `grep -rn "/auth/sso/"` across `backend/` and `frontend/` must
  return nothing. A stale literal here fails only at the IdP, which no test in
  either suite reaches.

`/admin/authentication` is already `<EEGate>`-wrapped from stage 1, so page
gating needs nothing. Long term the licensed package supplies its own nav entry
and the `ee: true` flag in `AdminSubNav` goes away — out of scope here.

## 9. Docs

There is no `docs/site/docs/admin/authentication.md`. SSO has never had a
user-facing page — a pre-existing gap, not one this stage creates. Don't fix it
in a relocation PR; the API paths that change are not documented anywhere.

`admin/editions.md` already lists SSO as an enterprise feature and stays
correct. If any doc line does change, the zh-Hans mirror changes in the same
commit — a missing mirror broke the docs build once in stage 2.

## 10. PR sequence

Two PRs.

| PR | Base | Contents | Size |
|---|---|---|---|
| 1 | stage-2 branch | This plan, the §2 spec corrections, §5 `resolve_identity` decoupling, §4 `RouteExtension` slot | ~230 lines |
| 2 | PR 1 | §3 the relocation, §6 test split, §7 CI, §8 frontend | large but mechanical |

The *code* in PR 1 touches only files identical to `main` and could branch from
there, which would keep the stack shallower. The spec corrections can't: the
stage-2 corrections they build on live on the unmerged stage-2 branch, so
basing PR 1 on `main` would mean either resolving that text twice or splitting
the prose from the code it describes. Not worth a third PR — stack it.

§4 and §5 are independent of each other but share one concern — making core
ready to give SSO up — and neither is large enough to justify its own review
round. They ride together, as separate commits.

PR 2 is the one that needs a review pass: it is where a stale path literal or a
test left in the wrong lane would hide.

### Cutover

PR 2 changes public URLs, so backend and frontend must ship together and any
configured identity provider must be updated to the new callback and ACS URLs by
hand. A rollout where the two artifacts move independently leaves SSO login
broken in both directions — old frontend calling removed paths, or new frontend
calling paths that don't exist yet.

The review of this plan proposed a staged transition where the licensed package
temporarily accepts both old and new callback paths. **Declining that**: the
project has not shipped, so there is no deployment carrying live IdP
registrations to protect, and the repo rule is to cut over cleanly rather than
add compatibility shims. What it does need is the operator-facing note — the
same IdP reconfiguration step belongs in the PR description, because existing
dev and staging IdP registrations will break silently otherwise.

## 11. Risks

| Risk | Detection |
|---|---|
| Autogenerate wants to drop the SSO tables | `alembic revision --autogenerate` on a default install must produce an empty diff. Run it; don't reason about it. |
| A stale `/auth/sso/` literal | `grep -rn "/auth/sso/" backend/ frontend/` returns nothing after the move. Eight sites today (§8) — the IdP-facing ones fail where no test looks. |
| OSS Google login broken by the move | It lazily imports two helpers out of `sso.py` (§3.1), so the break appears at callback time, not import time — a green import and a green startup prove nothing. `test_social_login_routes.py` stays in the default lane; additionally run the whole Google callback with the package uninstalled, since that is the only path that executes the lazy import. |
| A licensed test left in the default lane | The default lane must stay green with the package uninstalled — the stage-1 conftest guard turns a silent skip into a `UsageError` |
| A core module still importing a deleted one | §3.2 names them; `uv run python -c "import cubeplex.api.app"` on a default install is the cheap check |
| Password login broken by the auth-provider slot | Not applicable by construction once §4 uses its own slot — that is the reason for the choice |
| `frontend-e2e` green for the wrong reason | It installs the package, so it cannot catch an OSS regression. The 404 test in §6 is what covers that. |
| Downgrade leaves an org with no way in | §3.3's startup check; assert it with an e2e test that seeds an active row on a default install and expects a boot failure naming `disable-sso` |

## 12. Out of scope

- Audit dual-path collapse (spec §12.1) and audit UI (§12.2) — separate work,
  as agreed.
- A `FEATURE_SSO` license claim. Package presence is the gate; a per-feature
  claim can be added later if tiering needs one, without a format change.
- The OSS/licensed Playwright job split.
- An `admin/authentication` docs page (§9).
