# Registration Flow Optimization — Design

**Date:** 2026-06-29
**Status:** Design (pre-implementation)
**Branch:** `feat/2026-06-29-registration-flow`

## Goal

Optimize the registration flow with three changes:

1. **Email OTP verification gate.** When SMTP is enabled, a newly registered
   user must enter a one-time code sent to their email before they can log in.
   Replaces the existing magic-link (JWT-in-URL) verification entirely.
2. **Configurable password policy.** Two presets — `low` (length ≥ 8) and
   `high` (length ≥ 10 + upper + lower + digit + symbol). Default `high`.
   Selectable via config.
3. **Post-registration onboarding wizard.** `multi_tenant` no longer silently
   auto-creates an org/workspace at register time. The user is created in a
   pending-onboarding state and fills a wizard (`org name + slug + workspace
   name`) before any org/workspace is created. `single_tenant` first owner is
   unified onto the same wizard. Invite-link registrations skip the relevant
   wizard steps.

## Non-goals

- 2FA / per-login OTP. Verification is a one-time gate at registration only.
- A product-tour / sample-data onboarding experience. The wizard is purely
  org/workspace provisioning.
- Changing the existing `single_tenant` "subsequent users auto-attach to the
  singleton org" behavior. Only the first-owner path is unified onto the
  wizard.
- Backwards-compat shims. The project has not shipped publicly; magic-link
  verify routes, `/system/setup`, and the `/setup` page are removed cleanly.

## Current state (what exists today)

- **Register** (`backend/cubebox/api/routes/v1/auth.py:46`): custom
  `POST /api/v1/auth/register`. Creates user via fastapi-users
  `UserManager.create`. Does **not** set an auth cookie; the frontend
  auto-calls `/login` after. Returns `{id, email, default_workspace_id}`.
- **Login** (`auth.py:75`): custom `POST /api/v1/auth/login`,
  `OAuth2PasswordRequestForm`. Checks `is_active`. SSO enforcement returns
  403 `sso_required`. Sets `cubebox_auth` cookie.
- **Bootstrap** (`backend/cubebox/auth/users.py:86`, `on_after_register`):
  - `multi_tenant` (`_on_register_multi_tenant`, lines 136-183): silently
    creates org `"{local}'s Org"`, slug, `OrganizationMembership(OWNER)`,
    "Personal" workspace, `Membership(ADMIN)`, `AgentConfig`, MCP enrollment,
    preinstalled skills. Sets transient `_default_workspace_id`.
  - `single_tenant` (`_on_register_single_tenant`, lines 185-277): first user
    is a "pending owner" (advisory lock, no org yet) → must call
    `POST /api/v1/system/setup` (`system.py:47`); subsequent users auto-attach
    to the singleton org as `OrgRole.MEMBER` + Personal workspace.
- **Email verification**: magic-link via fastapi-users `get_verify_router`
  (`auth.py:621`) → `/request-verify-token` + `/verify` (JWT token in URL).
  `UserManager.on_after_register` auto-sends a verification email at register
  (failures swallowed). `User.is_verified` field exists but is **not
  enforced** — unverified users log in freely. `VerificationBanner` nags
  in-app.
- **Password validation**: effectively none. fastapi-users'
  `BaseUserManager.validate_password` is a no-op and cubebox does not override
  it. Only `ChangePasswordRequest.new_password: str = Field(min_length=8)`
  (`auth.py:277`) enforces anything, and only on change-password.
- **Config**: dynaconf. `config.yaml` has `auth:` block (lines 278-288) and
  `email:` block (lines 298-305, `backend: "log"` default, `smtp` option).
- **Invites**: workspace-scoped only. `POST /workspaces/invites/accept`
  (`workspaces.py:534`) requires an **already-logged-in** user; consumes
  token, grants `OrgRole.MEMBER` on the ws's org + `Membership` at the invite
  role. No org-scoped invite exists. Unregistered users must ordinary-register
  (which today auto-creates their own org) then manually click the invite link
  — the `next` param is currently dropped by `RegisterForm`.
- **Frontend**: `RegisterForm` (auto-logins after register, drops `next`),
  `LoginForm`, `/setup` page + `SetupForm` (org name + slug, single_tenant
  only), `/verify-email` magic-link page, `VerificationBanner`,
  `useDeploymentMode()` hook reads `GET /api/v1/system/info`.

## Design

### §1. Configuration

New keys under `auth:` in `config.yaml`, mirrored across
`config.{development,test,production}.yaml` and read via `config.get(...)`:

```yaml
auth:
  password_policy: high          # high | low, default high
  email_verification:
    enabled: auto                # auto | true | false
                                 # auto = enabled iff email.backend == "smtp"
    code_length: 6               # OTP digit count
    code_ttl_seconds: 600        # OTP lifetime
    max_attempts: 5              # wrong guesses before code is invalidated
    resend_cooldown_seconds: 60  # min wait between resend calls
    rate_limit_per_hour: 10      # per-email send cap (independent of REGISTER_LIMIT)
```

Resolution of `enabled`:
- `auto` → enabled iff `config.email.backend == "smtp"`.
- `true` → force enabled even with `log` backend (used by tests).
- `false` → force disabled.

When disabled, register sets `is_verified = True` immediately and the user
proceeds straight to onboarding — no OTP step.

### §2. Password policy

New pure-function module `backend/cubebox/auth/password_policy.py`:

```python
class PasswordPolicy(StrEnum):
    LOW = "low"
    HIGH = "high"

LOW_RULES  = PasswordRules(min_length=8)
HIGH_RULES = PasswordRules(
    min_length=10, require_uppercase=True, require_lowercase=True,
    require_digit=True, require_symbol=True,
)  # symbol = visible non-alphanumeric ASCII

def validate_password(password: str, policy: PasswordPolicy) -> PasswordValidationResult
```

`validate_password` returns `{ok: bool, errors: list[str]}` where `errors`
are i18n message keys (one per failed rule). LOW checks length only; HIGH
checks length + each character class.

Integration points (single source of truth = the pure function):
1. **Register**: `UserManager.validate_password(self, password, user)`
   overrides the fastapi-users no-op, reads `config.auth.password_policy`,
   calls `validate_password`, raises `InvalidPasswordException(reason)` on
   failure. The register endpoint catches it → 400
   `{code: "weak_password", errors: [...]}`.
2. **Change password** (`POST /auth/change-password`): replace the hardcoded
   `min_length=8` Field constraint with a call to the same function.
3. **Reset password** (fastapi-users reset router): routes through
   `validate_password` automatically once overridden — no extra change.

A TypeScript mirror lives in `@cubebox/core` (`validatePassword(policy)`)
for frontend pre-submit UX. The backend remains the authority.

### §3. OTP verification (replaces magic-link)

**Storage — Redis (key `email_otp:{email}`):** value is a hash
`{code, attempts, created_at}` with TTL `code_ttl_seconds` (default 600s).
- `code`: `code_length` digits, generated with `secrets.choice`
  (cryptographic randomness).
- `attempts`: incremented on each wrong verify; on reaching `max_attempts`
  the key is deleted (code invalidated, must resend).
- Verification success deletes the key immediately (no replay).
- Resend cooldown enforced via a separate `email_otp_sent:{email}` key with
  TTL `resend_cooldown_seconds`.
- Per-email send rate limit: counter key `email_otp_rl:{email}` (TTL 3600s);
  exceeding `rate_limit_per_hour` rejects the send.

**New module** `backend/cubebox/auth/email_otp.py` (service layer, no HTTP):
- `issue_otp(email) -> code`: generate, write Redis, send via
  `get_email_service()`. New template `email_otp_verification.{html,txt}`
  replaces `email_verification.*`.
- `verify_otp(email, code) -> VerifyResult`: compare → success (delete key,
  `{ok: True}`) / wrong (`{ok: False, reason: "invalid_otp",
  remaining_attempts}`) / missing-or-expired (`{ok: False,
  reason: "expired_or_unknown"}`).

**Endpoint changes (`auth.py`):**
- **Remove** `get_verify_router` inclusion (line 621) → deletes
  `/request-verify-token` and `/verify` (magic-link paths).
- **`POST /auth/register`**: when verification enabled, register succeeds
  without setting a cookie, calls `issue_otp(email)`, returns
  `{id, email, verification_required: true}`. When disabled, sets
  `is_verified = True`, returns `verification_required: false`.
- **New `POST /auth/verify-otp`** `{email, code}` → `verify_otp` → success
  returns `{ok: true}` (frontend then calls `/login`). Failure → 400 with
  `code` ∈ `invalid_otp` / `otp_expired` / `otp_max_attempts`.
- **New `POST /auth/resend-otp`** `{email}` → cooldown + rate-limit check →
  `issue_otp`. Non-existent emails return the same shape as success (no
  enumeration leak).
- **`POST /auth/login`**: when verification enabled and the user's
  `is_verified` is false → 403 `code: "email_not_verified"` (frontend routes
  to `/verify-otp` with resend). This is the "cannot log in unverified"
  backstop.

`User.is_verified` is reused; no new column. `VerificationBanner`'s resend
switches to `/resend-otp`.

### §4. Onboarding wizard + invite registration

**Three onboarding modes**, auto-selected from the user's membership state:

| Mode | org name/slug | workspace name | Trigger |
|---|---|---|---|
| Full | ✓ | ✓ | `multi_tenant` first registrant; `single_tenant` first owner |
| Workspace-only | ✗ (already in an org) | ✓ | org-invite accepted, no workspace yet |
| Skip | ✗ | ✗ | workspace-invite accepted (lands directly in `/w/{id}`) |

**Backend changes:**

1. `on_after_register` refactor:
   - `multi_tenant`: register creates the user **only** — no org/workspace.
     User is pending-onboarding.
   - `single_tenant`: first owner stays pending; subsequent users keep the
     existing auto-attach-to-singleton-org behavior.
   - When verification is enabled, no bootstrap runs at register at all
     (deferred past the OTP gate).

2. `MeResult` gains `needs_onboarding: bool` — true when the user has no org
   membership (and, for `single_tenant`, has not run setup). The existing
   `needs_org_setup` field is replaced by `needs_onboarding` at the call sites
   that switch on it.

3. **New `POST /api/v1/onboarding`** (auth required):
   - Body `{org_name?, org_slug?, workspace_name}` (org fields optional).
   - Mode is inferred from the caller's current memberships — no `mode`
     parameter is exposed (avoids the `?scope=`/role-body smell).
   - **Full**: caller has no org → requires `org_name`/`org_slug` → creates
     org + workspace + `OWNER`/`ADMIN` memberships + `AgentConfig` + MCP
     enrollment + preinstalled skills.
   - **Workspace-only**: caller has an org membership but no workspace →
     requires `workspace_name` → creates workspace in the existing org +
     `ADMIN` + `AgentConfig` + MCP + skills.
   - Bootstrap logic is extracted from the current
     `_on_register_multi_tenant` / `_on_register_single_tenant` into shared
     helpers `_bootstrap_org_and_workspace(...)` and
     `_bootstrap_workspace_in_org(...)`; the register path no longer calls
     them, only `/onboarding` does. No logic duplication.
   - Slug validated via the existing `validateSlug` rules; org-name length
     bounded (2-64). Slug collision → 409.
   - `single_tenant` subsequent users calling `/onboarding` → 409
     `onboarding_not_required`.
   - Concurrency: DB unique constraints (org slug, membership PK) make a
     double-submit return 409 `onboarding_already_done`.
   - Success returns `{workspace_id}`; frontend goes to `/w/{id}`.
   - Whole bootstrap is one DB transaction; on failure, rollback + cleanup
     via the existing `_best_effort_cleanup_register` pattern.

4. **New org-scoped invite** (coexists with workspace invite):
   - New model `OrgInviteToken` (`org_id`, `role: OrgRole`, `token`,
     `expires_at`, `used_at`, `created_by`). New table → public ID prefix in
     `public_id.py` + alembic autogen migration (tz-aware time columns).
   - Create endpoint under org-admin scope
     (`/api/v1/admin/orgs/{org}/invites`).
   - Accept endpoint `POST /api/v1/orgs/invites/accept` (auth required) →
     consumes token, grants `OrganizationMembership(role=invite role)`.
     Invite role is limited to `ADMIN`/`MEMBER` (never `OWNER` — preserves
     the one-owner-per-org DB invariant).
   - After accepting, the user has an org but no workspace →
     `needs_onboarding` still true → enters the **workspace-only** wizard.

5. **Invite-registration routing priority** (frontend): when `next` points
   at an invite-accept path (`/invite/accept` or `/orgs/invites/accept`),
   the post-register/post-verify flow runs invite acceptance first (which
   establishes the org/workspace membership), then `needs_onboarding`
   decides whether the wizard is needed:
   - workspace invite → `needs_onboarding = false` → straight to `/w/{id}`.
   - org invite → `needs_onboarding = true` (no ws) → workspace-only wizard.

6. **`POST /system/setup` retired**; logic folded into `/onboarding`. The
   `/setup` route and `SetupForm` are deleted.

**Frontend changes:**
- New `/onboarding` page + `<OnboardingForm>` rendering full or
  workspace-only field set based on `me`. Slug auto-suggested from org name,
  editable, real-time uniqueness check.
- `RegisterForm`: after register, if `next` is an invite-accept path → go
  there; else if `needs_onboarding` → `/onboarding`; else → `/w/{id}`.
  **Fixes the dropped-`next` bug** — `next` is threaded through the OTP step
  (`/verify-otp` and resend carry `next`).
- `(app)/layout.tsx` guard: `needs_onboarding` → redirect `/onboarding`
  (replaces the `needs_org_setup` guard).
- New `/orgs/invites/accept` page mirroring the workspace invite-accept page.

### §5. Frontend flow (OTP + invites threaded in)

```
/register?next=<path>
  → RegisterForm (frontend pre-validates password via core validatePassword)
  → POST /auth/register
      ├─ verification on:  {verification_required: true}
      │     → /verify-otp?next=<path>&email=<email>
      └─ verification off: {verification_required: false}
            → /login (auto) → loadMe → route by next/onboarding
/verify-otp?next=&email=
  → <OtpInput> (6 digits, paste support, countdown resend button)
  → POST /auth/verify-otp {email, code}
      ├─ ok   → /login (auto, sets cookie) → loadMe →
      │        ├─ next is invite-accept → accept invite → then by needs_onboarding
      │        ├─ needs_onboarding      → /onboarding
      │        └─ else                  → /w/{default_ws}
      └─ fail → show code (invalid_otp/otp_expired/otp_max_attempts), keep next/email
  → resend → POST /auth/resend-otp {email} (button disabled during countdown)
```

- `next` is preserved across `/register → /verify-otp → /login →
  invite/onboarding/workspace`, including the verification-off path.
- `/login` returns 403 `email_not_verified` for unverified users →
  `LoginForm` renders an "email not verified" notice and a link to
  `/verify-otp?email=<email>` (with resend).
- Frontend password pre-validation is UX only; the backend is authoritative.

**New frontend components:**
- `<OtpInput>` (`components/auth/`): 6-cell input, paste, countdown resend.
- `<OnboardingForm>`: full / workspace-only states.
- `/orgs/invites/accept` page.

**Removed:** `VerifyEmailPage` + `/verify-email` route,
`verifyEmail`/`requestVerifyToken` client functions; `VerificationBanner`
resend retargeted to `/resend-otp`.

### §6. Error handling & security

- **OTP**: `secrets.choice` for generation; Redis TTL auto-expiry; success
  deletes key (no replay); `max_attempts` brute-force guard; per-email
  `rate_limit_per_hour` + `resend_cooldown`; `verify-otp`/`resend-otp`
  slowapi-limited (IP + email). Non-existent emails get the same response
  shape as success (no enumeration).
- **Password**: backend `validate_password` is authoritative; error messages
  never echo the password; `weak_password` 400 carries structured per-rule
  errors.
- **Onboarding atomicity**: one transaction; failure rolls back and cleans up
  via the existing best-effort pattern; concurrent double-submit → 409.
- **Invites**: tokens single-use (atomic `consume` sets `used_at`); expired
  or reused → 400; org-invite role limited to `ADMIN`/`MEMBER`.

### §7. Testing

**Backend e2e (`backend/tests/e2e/`, real Postgres + Redis + FastAPI app):**
- `test_register_otp_flow.py`: verification on → register sets no cookie,
  returns `verification_required` → OTP in Redis → `/verify-otp` success →
  `/login` sets cookie → `is_verified=true`. Covers wrong code,
  `max_attempts` invalidation, expiry, resend cooldown, `rate_limit_per_hour`.
- `test_register_smtp_disabled.py`: `log` backend → register sets
  `is_verified=true`, `verification_required=false`, login works.
- `test_login_unverified_blocked.py`: unverified `/login` → 403
  `email_not_verified`.
- `test_password_policy.py`: high/low rules pass/fail; change-password and
  reset endpoints honor the same rules; config switch takes effect.
- `test_onboarding.py`: full mode (multi_tenant first + single_tenant first)
  creates org+ws+memberships+AgentConfig+MCP+skills; workspace-only mode;
  slug collision 409; subsequent-user 409 `onboarding_not_required`;
  rollback on injected failure.
- `test_invite_onboarding.py`: workspace-invite register → accept →
  `needs_onboarding=false` → straight to ws; org-invite register → accept →
  `needs_onboarding=true` → workspace-only wizard; expired/reused token 400;
  org-invite role limited to ADMIN/MEMBER.
- Real-LLM not involved; OTP template rendering asserted via the log backend
  output containing the code.

**Backend unit (`backend/tests/unit/`):**
- `password_policy.py` rule pure functions (high/low boundaries, symbol
  definition, empty/overlong).
- `email_otp.py` verify logic with a fake Redis client (internal-boundary
  mock, per the unit-layer rules).

**Frontend e2e (Playwright):**
- Register → enter OTP (code scraped from log backend) → verify → land in
  `/onboarding` → fill org+ws → land in `/w/{id}` (full user flow, the
  business invariant).
- Verification-off path: register → land directly in `/onboarding`.
- Workspace-invite register flow: skip onboarding, land in ws.
- Unverified-login interception renders the notice.
- `<OtpInput>` state machine: paste, countdown-disabled resend (client state
  machine — allowed).
- No element-count / nav-smoke tests.

**Config / migration:**
- `config.test.yaml` sets `email_verification.enabled: true` +
  `password_policy: high`.
- alembic autogen migration adds `org_invite_tokens` (tz-aware time columns,
  public ID prefix).
