# SSO relocation (stage 3)

Status: plan
Spec: [2026-07-07-oss-ee-split-design.md](../specs/2026-07-07-oss-ee-split-design.md) §11 stage 3

Stage 2 moved cost reporting into the licensed package. This stage moves SAML
and OIDC single sign-on. It is the last of the planned relocations, and the
first one that touches unauthenticated request paths.

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
| `api/routes/v1/sso.py` | 574 | Minus `org-info`, see below |
| `sso/saml.py` | 178 | Every function takes an `SSOConnection` |
| `sso/attribute_mapping.py` | 73 | Only caller is `sso.py` |
| `repositories/sso_connection.py` | 57 | Only caller is `admin_sso.py` (the CLI uses `select()` directly) |
| `sso/oidc.py:oidc_config_from_connection` | ~15 | The one function in `oidc.py` that takes an `SSOConnection` |

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

### Why a namespaced prefix rather than the current paths

The alternative — let the extension mount at `/api/v1` verbatim, keeping
`/api/v1/auth/sso/*` unchanged — reads as the smaller change but hands any
installed package the ability to shadow a core route. FastAPI resolves
first-match-wins, so an extension router with a colliding path silently takes
over a core endpoint depending on mount order. Guarding that needs a startup
path-collision check, which is more machinery than the namespace it replaces.

Reserving `/_extensions/` makes collisions impossible by construction and is
symmetric with what stage 2 already established. The cost is that the SAML ACS
URL changes, which is an externally-registered URL — acceptable only because
nothing has shipped. Choose the path once here and treat it as frozen.

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
  `test_sso_routes.py`, `test_sso_saml.py`, `test_sso_attribute_mapping.py`,
  `test_sso_connection_repository.py`
- `tests/e2e/test_sso_admin.py` — every test hits `/api/v1/admin/sso`

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
depend on no external system and belong in `unit/` by the placement rule. Both
Makefiles' test targets need the same path added — check `make check-ci` locally
with the package installed, not just CI.

`admin-sso.spec.ts` and `sso-login.spec.ts` run in `frontend-e2e`, which installs
the package, so they keep passing. The deliberately-deferred OSS/licensed
Playwright split is still deferred; note it, don't fix it here.

## 8. Frontend

`frontend/packages/core/src/api/sso.ts` holds every URL and is the only file
with real changes:

- 11 admin calls: `/api/v1/admin/sso/*` → `/api/v1/admin/_extensions/cubeplex_ee/sso/*`
- 1 login call: `/api/v1/auth/sso/initiate` → `/api/v1/_extensions/cubeplex_ee/sso/initiate`
- `getOrgInfo` is unchanged — `org-info` stays in core (§3)

Also:

- `frontend/packages/core/src/api/client.ts:49` lists workspace-neutral path
  prefixes; add `/api/v1/_extensions/cubeplex_ee/sso/`.
- `core/src/api/__tests__/sso.test.ts` asserts exact URLs — update.
- `__tests__/e2e/sso-login.spec.ts:64` mocks `**/api/v1/auth/org-info/…` —
  unchanged, since that endpoint stays.
- Backend redirect targets: `sso.py` builds IdP `redirect_uri`s from
  `public_base_url` plus its own route paths. Those strings move with the file,
  but grep for hardcoded `/auth/sso/` in the moved code — a stale literal here
  fails only at the IdP, which no unit test will catch.

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

## 11. Risks

| Risk | Detection |
|---|---|
| Autogenerate wants to drop the SSO tables | `alembic revision --autogenerate` on a default install must produce an empty diff. Run it; don't reason about it. |
| A stale `/auth/sso/` literal in moved code | Grep the moved files for the old prefix after the move; the SAML ACS URL is only exercised end-to-end |
| A licensed test left in the default lane | The default lane must stay green with the package uninstalled — the stage-1 conftest guard turns a silent skip into a `UsageError` |
| Password login broken by the auth-provider slot | Not applicable by construction once §4 uses its own slot — that is the reason for the choice |
| `frontend-e2e` green for the wrong reason | It installs the package, so it cannot catch an OSS regression. The 404 test in §6 is what covers that. |

## 12. Out of scope

- Audit dual-path collapse (spec §12.1) and audit UI (§12.2) — separate work,
  as agreed.
- A `FEATURE_SSO` license claim. Package presence is the gate; a per-feature
  claim can be added later if tiering needs one, without a format change.
- The OSS/licensed Playwright job split.
- An `admin/authentication` docs page (§9).
