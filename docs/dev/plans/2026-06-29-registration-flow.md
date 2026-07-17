# Registration Flow Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace magic-link email verification with an OTP gate, add a configurable password policy, and replace silent org/workspace auto-creation with an explicit post-registration onboarding wizard (with org-scoped invites).

**Architecture:** Three backend pure/service modules (`password_policy.py`, `email_otp.py`, refactored bootstrap helpers) feed thin route changes in `auth.py` plus a new `onboarding.py` router. `OrgInviteToken` is a new table. The frontend adds an `<OtpInput>` + `/verify-otp` page, an `<OnboardingForm>` + `/onboarding` page, an org-invite accept page, threads `next` through the whole flow, and removes the magic-link UI + `/setup`. Backend is the authority for password and OTP; the frontend mirrors password rules for UX only.

**Tech Stack:** FastAPI + fastapi-users v15, SQLModel + Alembic, Redis (aioredis), slowapi, dynaconf, Jinja2 email templates; Next.js + React 19, next-intl, Zustand, `@cubeplex/core`.

## Global Constraints

- Type annotations everywhere (mypy strict backend, strict TS frontend). Line length 100.
- Datetimes tz-aware: `Column(DateTime(timezone=True), ...)`, `datetime.now(UTC)`, `utc_isoformat()` on DB→API. New tz column migrations hand-add `postgresql_using="<col> AT TIME ZONE 'UTC'"` on each `alter_column`.
- New business table → public ID prefix in `backend/cubeplex/models/public_id.py`; `default_factory=generate_public_id(PREFIX_X)`.
- Migrations: `alembic revision --autogenerate -m "..."` only; do not hand-edit migration files.
- Dependencies via `uv add` (backend) / `pnpm add` (frontend) only.
- Scope-isolated APIs/pages: no `?scope=` / `mode?` props. Org-admin routes under `/admin/...` with `require_org_admin` + `resolve_current_org_id`; the org-invite **accept** endpoint is auth-scoped (not org-admin) at `/api/v1/orgs/invites/accept`.
- No backwards-compat shims: magic-link verify routes, `/system/setup`, and the `/setup` page are removed cleanly.
- OTP: `secrets.choice` for generation; Redis TTL; success deletes key; `max_attempts` guard; per-email rate limit + resend cooldown; no email enumeration (non-existent emails return success-shape). Org-invite role limited to `ADMIN`/`MEMBER` (never `OWNER`).
- Worktree discipline: read `.worktree.env` first; backend port 8001, frontend port 3001, DB `cubeplex_feat_2026_06_29_registration_flow`, redis key prefix `cubeplex-feat-2026-06-29-registration-flow`.
- `@cubeplex/core` must build (`pnpm --filter @cubeplex/core build`) before web sees API/type changes.
- Pipe noisy output through `tee tmp/<task>.log` then `tail`; grep the saved log on failure, don't re-run.
- Docs ship with code: update the matching `docs/site/docs/` page in the same PR.

## File Structure

**Backend — new files:**
- `backend/cubeplex/auth/password_policy.py` — pure password rule functions + `validate_password`. Single source of truth.
- `backend/cubeplex/auth/email_otp.py` — OTP service (issue/verify/resend) over Redis; no HTTP.
- `backend/cubeplex/api/routes/v1/onboarding.py` — `POST /api/v1/onboarding` router.
- `backend/cubeplex/api/routes/v1/org_invites.py` — org-invite create (admin) + accept (auth) router.
- `backend/cubeplex/models/org_invite_token.py` — `OrgInviteToken` SQLModel table.
- `backend/cubeplex/repositories/org_invite_token.py` — `OrgInviteTokenRepository` (issue/consume).
- `backend/cubeplex/templates/email/email_otp_verification.{html,txt}` — OTP email template.
- `backend/tests/unit/test_password_policy.py`, `backend/tests/unit/test_email_otp.py`.
- `backend/tests/e2e/test_register_otp_flow.py`, `test_register_smtp_disabled.py`, `test_login_unverified_blocked.py`, `test_password_policy_e2e.py`, `test_onboarding.py`, `test_invite_onboarding.py`.

**Backend — modified files:**
- `backend/config.yaml`, `backend/config.test.yaml`, `backend/config.development.yaml`, `backend/config.production.yaml` — new `auth.password_policy` + `auth.email_verification` keys.
- `backend/cubeplex/models/public_id.py` — add `PREFIX_ORG_INVITE = "oinv"`.
- `backend/cubeplex/models/__init__.py` — export `OrgInviteToken`.
- `backend/cubeplex/api/routes/v1/__init__.py` + `backend/cubeplex/app.py` — register onboarding + org_invites routers.
- `backend/cubeplex/auth/users.py` — override `validate_password`; refactor bootstrap into shared helpers; remove magic-link `request_verify` sending; `on_after_register` defers bootstrap when verification enabled.
- `backend/cubeplex/api/routes/v1/auth.py` — remove `get_verify_router`; add `verification_required` to register response; add `/verify-otp` + `/resend-otp`; add `email_not_verified` 403 in login; replace `needs_org_setup` with `needs_onboarding` in `_me_payload`; route change-password through the policy.
- `backend/cubeplex/api/routes/v1/system.py` — retire `POST /system/setup`; keep `/system/info` (drop `needs_org_setup`, keep deployment_mode/version/sandbox).
- `backend/cubeplex/api/schemas/system.py` — drop `needs_org_setup` from `SystemInfoResponse` (or keep as deprecated false); see Task 9.
- `backend/cubeplex/i18n/messages/{en,zh}/LC_MESSAGES/messages.po` — new message keys.

**Frontend — new files:**
- `frontend/packages/core/src/api/onboarding.ts`, `frontend/packages/core/src/api/orgInvites.ts`, `frontend/packages/core/src/auth/passwordPolicy.ts`.
- `frontend/packages/web/components/auth/OtpInput.tsx`.
- `frontend/packages/web/components/onboarding/OnboardingForm.tsx`.
- `frontend/packages/web/app/(auth)/verify-otp/page.tsx`.
- `frontend/packages/web/app/(setup)/onboarding/page.tsx`.
- `frontend/packages/web/app/(auth)/orgs/invites/accept/page.tsx`.

**Frontend — modified files:**
- `frontend/packages/core/src/api/auth.ts` — `RegisterResult.verification_required`; `MeResult.needs_onboarding` (replace `needs_org_setup`); add `verifyOtp`/`resendOtp`; remove `verifyEmail`/`requestVerifyToken`.
- `frontend/packages/core/src/api/system.ts` — remove `postSetup`/`SetupRequest`/`SetupResponse`; drop `needs_org_setup`.
- `frontend/packages/core/src/hooks/useDeploymentMode.ts` — `needsOnboarding` replaces `needsOrgSetup`.
- `frontend/packages/web/components/auth/RegisterForm.tsx` — thread `next`; route on `verification_required`/`needs_onboarding`/invite/`/w/{id}`.
- `frontend/packages/web/components/auth/LoginForm.tsx` — handle 403 `email_not_verified`.
- `frontend/packages/web/components/layout/VerificationBanner.tsx` — resend retargets to `/resend-otp`.
- `frontend/packages/web/app/(app)/layout.tsx` — `needs_onboarding` → `/onboarding` guard.
- `frontend/packages/web/messages/{en,zh}.json` — new keys.

**Frontend — deleted files:**
- `frontend/packages/web/app/(auth)/verify-email/` (magic-link page).
- `frontend/packages/web/app/(setup)/setup/` + `frontend/packages/web/components/setup/SetupForm.tsx`.

## Task Order (dependency chain)

1. **Task 1** — Config keys (no deps).
2. **Task 2** — `password_policy.py` pure module + unit tests (no deps).
3. **Task 3** — Wire `validate_password` into `UserManager` + register/change-password endpoints (depends 1, 2).
4. **Task 4** — `email_otp.py` service + unit tests (depends 1).
5. **Task 5** — OTP endpoints + register response + login gate + remove magic-link + OTP template (depends 1, 4).
6. **Task 6** — `OrgInviteToken` model + prefix + migration + repository (no deps).
7. **Task 7** — Org-invite create + accept endpoints (depends 6).
8. **Task 8** — Refactor bootstrap into shared helpers in `users.py` (no deps; pure refactor).
9. **Task 9** — Onboarding router + retire `/system/setup` + `needs_onboarding` in `_me_payload` (depends 1, 8).
10. **Task 10** — Backend i18n keys + e2e tests for OTP/password/login-gate/onboarding/invite (depends 5, 7, 9).
11. **Task 11** — Frontend `@cubeplex/core` API/types + `passwordPolicy` mirror (depends 5, 9 backend shapes).
12. **Task 12** — `<OtpInput>` + `/verify-otp` page (depends 11).
13. **Task 13** — `RegisterForm` `next`-threading + `LoginForm` `email_not_verified` + `VerificationBanner` (depends 11).
14. **Task 14** — `<OnboardingForm>` + `/onboarding` page + `(app)/layout` guard + delete `/setup` (depends 11).
15. **Task 15** — Org-invite accept page (depends 11, 14).
16. **Task 16** — Frontend i18n + Playwright e2e + docs update (depends 12–15).

---


### Task 1: Config keys for password policy + email verification

**Files:**
- Modify: `backend/config.yaml` (auth block, ~lines 278-296)
- Modify: `backend/config.test.yaml` (auth block)
- Modify: `backend/config.development.yaml` (auth block)
- Modify: `backend/config.production.yaml` (auth block)

**Interfaces:**
- Produces config keys read by later tasks via `config.get("auth.password_policy", "high")` and `config.get("auth.email_verification.<key>", <default>)`. Resolution helper `is_email_verification_enabled()` lives in Task 4 (`email_otp.py`).

- [ ] **Step 1: Add keys to `backend/config.yaml` auth block**

In `backend/config.yaml`, inside the `auth:` block (after the `rate_limit:` sub-block, before the closing of `auth:`), add:

```yaml
    password_policy: "high"   # "high" | "low", default high. ENV: CUBEPLEX_AUTH__PASSWORD_POLICY
    email_verification:
      enabled: "auto"         # "auto" | "true" | "false". auto = enabled iff email.backend == "smtp"
      code_length: 6          # OTP digit count
      code_ttl_seconds: 600   # OTP lifetime
      max_attempts: 5         # wrong guesses before code is invalidated
      resend_cooldown_seconds: 60  # min wait between resend calls
      rate_limit_per_hour: 10      # per-email send cap (independent of REGISTER_LIMIT)
```

- [ ] **Step 2: Add keys to `backend/config.test.yaml`**

In `config.test.yaml` `auth:` block (which currently only sets `rate_limit`), add (forces verification ON for the log backend so OTP e2e works, and pins high policy):

```yaml
  auth:
    rate_limit:
      login_per_minute: 1000
      register_per_minute: 1000
    password_policy: "high"
    email_verification:
      enabled: "true"
      code_length: 6
      code_ttl_seconds: 600
      max_attempts: 5
      resend_cooldown_seconds: 60
      rate_limit_per_hour: 100
```

- [ ] **Step 3: Add keys to `backend/config.development.yaml`**

In the `auth:` block (currently only `cookie_secure: false`), add `password_policy: "high"` and the full `email_verification:` sub-block with `enabled: "auto"` (same values as `config.yaml`).

- [ ] **Step 4: Add keys to `backend/config.production.yaml`**

In the `auth:` block (currently `jwt_secret`/`csrf_secret`/`cookie_secure`), add `password_policy: "high"` and the `email_verification:` sub-block with `enabled: "auto"` and the same defaults.

- [ ] **Step 5: Verify config loads**

Run:
```bash
cd backend && uv run python -c "from cubeplex.config import config; print(config.get('auth.password_policy')); print(config.get('auth.email_verification.enabled')); print(config.get('auth.email_verification.code_length'))"
```
Expected: prints `high`, `auto` (or `true` under test env), `6`. (Run from worktree; `.worktree.env` sets `CUBEPLEX_ENV` etc. — if `config` resolves the wrong env, run `uv run python -c "..."` after `source .worktree.env`.) If a KeyError/AttributeError appears, the nested key path is wrong — dynaconf reads `auth.email_verification.code_length` as nested dict, confirm the YAML indentation is 6 spaces under `email_verification:`.

- [ ] **Step 6: Commit**

```bash
git add backend/config.yaml backend/config.test.yaml backend/config.development.yaml backend/config.production.yaml
git commit -m "feat(config): add auth.password_policy + auth.email_verification keys"
```

---

### Task 2: Password policy pure module

**Files:**
- Create: `backend/cubeplex/auth/password_policy.py`
- Test: `backend/tests/unit/test_password_policy.py`

**Interfaces:**
- Produces: `PasswordPolicy` (StrEnum: LOW/HIGH), `PasswordRules` (dataclass), `PasswordValidationResult` (TypedDict/dataclass `{ok: bool, errors: list[str]}`), `LOW_RULES`, `HIGH_RULES`, `validate_password(password: str, policy: PasswordPolicy) -> PasswordValidationResult`, `get_password_policy() -> PasswordPolicy` (reads config), `validate_password_from_config(password: str) -> PasswordValidationResult`.
- `errors` are i18n message keys: `password_too_short`, `password_no_uppercase`, `password_no_lowercase`, `password_no_digit`, `password_no_symbol`. Each carries the bound (e.g. min length) via a separate `errors_params` list of dicts so the translator can interpolate — kept simple: error strings are the key only; the frontend shows the rule text. (Backend authority; the per-rule key is enough for the 400 body.)
- Consumes: `config.get("auth.password_policy", "high")` from Task 1.

- [ ] **Step 1: Write the failing unit test**

`backend/tests/unit/test_password_policy.py`:

```python
from cubeplex.auth.password_policy import (
    HIGH_RULES,
    LOW_RULES,
    PasswordPolicy,
    validate_password,
)


def test_low_only_checks_length():
    assert validate_password("12345678", PasswordPolicy.LOW).ok is True
    assert validate_password("1234567", PasswordPolicy.LOW).ok is False
    # low does NOT require character classes
    assert validate_password("alllowercase", PasswordPolicy.LOW).ok is True


def test_high_requires_all_classes():
    ok = "Aa1!longenough"
    assert validate_password(ok, PasswordPolicy.HIGH).ok is True

    short = validate_password("Aa1!short", PasswordPolicy.HIGH)
    assert short.ok is False
    assert "password_too_short" in short.errors

    no_upper = validate_password("aa1!longenough", PasswordPolicy.HIGH)
    assert no_upper.ok is False
    assert "password_no_uppercase" in no_upper.errors

    no_lower = validate_password("AA1!LONGENOUGH", PasswordPolicy.HIGH)
    assert "password_no_lowercase" in no_lower.errors

    no_digit = validate_password("Aa!!longenough", PasswordPolicy.HIGH)
    assert "password_no_digit" in no_digit.errors

    no_symbol = validate_password("Aa1longenough", PasswordPolicy.HIGH)
    assert "password_no_symbol" in no_symbol.errors


def test_symbol_is_visible_non_alphanumeric_ascii():
    # space is not a symbol; punctuation is
    assert validate_password("Aa1 longenough", PasswordPolicy.HIGH).ok is False
    assert validate_password("Aa1.longenough", PasswordPolicy.HIGH).ok is True


def test_empty_and_overlong():
    assert validate_password("", PasswordPolicy.LOW).ok is False
    long_pw = "Aa1!" + "x" * 200
    assert validate_password(long_pw, PasswordPolicy.HIGH).ok is True


def test_rules_constants():
    assert LOW_RULES.min_length == 8
    assert HIGH_RULES.min_length == 10
    assert HIGH_RULES.require_symbol is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/unit/test_password_policy.py -v --no-cov 2>&1 | tee tmp/password_policy.log | tail -5
```
Expected: FAIL with `ModuleNotFoundError: No module named 'cubeplex.auth.password_policy'`.

- [ ] **Step 3: Write the implementation**

`backend/cubeplex/auth/password_policy.py`:

```python
"""Password policy — pure functions, single source of truth.

The backend is authoritative for password strength. The frontend mirrors
these rules for pre-submit UX only (see @cubeplex/core passwordPolicy).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PasswordPolicy(StrEnum):
    LOW = "low"
    HIGH = "high"


@dataclass(frozen=True)
class PasswordRules:
    min_length: int
    require_uppercase: bool = False
    require_lowercase: bool = False
    require_digit: bool = False
    require_symbol: bool = False


@dataclass(frozen=True)
class PasswordValidationResult:
    ok: bool
    errors: list[str]


LOW_RULES = PasswordRules(min_length=8)
HIGH_RULES = PasswordRules(
    min_length=10,
    require_uppercase=True,
    require_lowercase=True,
    require_digit=True,
    require_symbol=True,
)

_RULES = {
    PasswordPolicy.LOW: LOW_RULES,
    PasswordPolicy.HIGH: HIGH_RULES,
}


def _is_symbol(ch: str) -> bool:
    # Visible non-alphanumeric ASCII (0x21..0x7e excluding alnum). Space excluded.
    return 33 <= ord(ch) <= 126 and not ch.isalnum()


def validate_password(password: str, policy: PasswordPolicy) -> PasswordValidationResult:
    rules = _RULES[policy]
    errors: list[str] = []
    if len(password) < rules.min_length:
        errors.append("password_too_short")
    if rules.require_uppercase and not any(c.isupper() for c in password):
        errors.append("password_no_uppercase")
    if rules.require_lowercase and not any(c.islower() for c in password):
        errors.append("password_no_lowercase")
    if rules.require_digit and not any(c.isdigit() for c in password):
        errors.append("password_no_digit")
    if rules.require_symbol and not any(_is_symbol(c) for c in password):
        errors.append("password_no_symbol")
    return PasswordValidationResult(ok=not errors, errors=errors)


def get_password_policy() -> PasswordPolicy:
    from cubeplex.config import config

    raw = str(config.get("auth.password_policy", "high")).lower()
    try:
        return PasswordPolicy(raw)
    except ValueError:
        return PasswordPolicy.HIGH


def validate_password_from_config(password: str) -> PasswordValidationResult:
    return validate_password(password, get_password_policy())
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && uv run pytest tests/unit/test_password_policy.py -v --no-cov 2>&1 | tee tmp/password_policy.log | tail -5
```
Expected: PASS (all tests green).

- [ ] **Step 5: Run mypy on the new module**

```bash
cd backend && uv run mypy cubeplex/auth/password_policy.py 2>&1 | tee tmp/mypy_password_policy.log | tail -5
```
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/auth/password_policy.py backend/tests/unit/test_password_policy.py
git commit -m "feat(auth): add password_policy pure module + unit tests"
```

---

### Task 3: Wire password policy into UserManager + register/change-password

**Files:**
- Modify: `backend/cubeplex/auth/users.py` (add `validate_password` override on `UserManager`)
- Modify: `backend/cubeplex/api/routes/v1/auth.py` (register catch → `weak_password` 400 with errors; change-password `ChangePasswordRequest` drop `min_length`, route through policy → `weak_password` 400 with errors)
- Test: `backend/tests/e2e/test_password_policy_e2e.py` (deferred to Task 10; this task adds a focused unit-level check via the existing register e2e fixture shape — but the real e2e is in Task 10). For this task, verify via an inline manual curl against the worktree app or skip to Task 10. **This task's deliverable is the wiring + a mypy/lint pass + the unit test already in Task 2 still green.**

**Interfaces:**
- Consumes: `validate_password_from_config`, `get_password_policy` from Task 2.
- Produces: `UserManager.validate_password(self, password, user)` override that raises `InvalidPasswordException(reason=...)` with `reason` carrying the structured errors. The register/change-password endpoints translate that into a 400 `{code: "weak_password", errors: [...]}`.
- `InvalidPasswordException` is imported from `fastapi_users.exceptions`; construct with `InvalidPasswordException(reason=<list or str>)`. fastapi-users stores `reason` on the exception; we attach the errors list there.

- [ ] **Step 1: Add the `validate_password` override to `UserManager`**

In `backend/cubeplex/auth/users.py`, inside `class UserManager(BaseUserManager[User, str]):`, immediately after `parse_id` (before `on_after_register`), add:

```python
    async def validate_password(
        self,
        password: str,
        user: User | None = None,
    ) -> None:
        """Override the fastapi-users no-op. Backend is authoritative."""
        from cubeplex.auth.password_policy import validate_password_from_config

        result = validate_password_from_config(password)
        if not result.ok:
            # reason carries the structured per-rule error keys for the API layer.
            raise InvalidPasswordException(reason=result.errors)
```

Add `InvalidPasswordException` to the existing fastapi-users import at the top of the file:

```python
from fastapi_users import BaseUserManager, FastAPIUsers
from fastapi_users.exceptions import InvalidPasswordException, UserAlreadyExists
```

(Check whether `UserAlreadyExists` is already imported in `auth.py` rather than `users.py` — the override lives in `users.py`; only add `InvalidPasswordException` there. Confirm with `grep -n "InvalidPasswordException" backend/cubeplex/auth/users.py`.)

- [ ] **Step 2: Update the register endpoint to surface `weak_password`**

In `backend/cubeplex/api/routes/v1/auth.py`, the `register` handler currently catches `InvalidPasswordException` and returns a generic `register_invalid_password` message. Replace that branch to return the structured errors. The `except InvalidPasswordException` block (currently ~line 60) becomes:

```python
    except InvalidPasswordException as exc:
        errors = exc.reason if isinstance(exc.reason, list) else [str(exc.reason)]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "weak_password", "errors": errors},
        ) from None
```

(Confirm `InvalidPasswordException` is already imported in `auth.py` — it is, per the existing catch. The `_t("register_invalid_password")` translation call in that branch is removed.)

- [ ] **Step 3: Update change-password to route through the policy with structured errors**

In `backend/cubeplex/api/routes/v1/auth.py`:

(a) Change `ChangePasswordRequest` to drop the hardcoded `min_length`:

```python
class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
```

(b) The `change_password` handler already calls `await user_manager.validate_password(body.new_password, user)`. Replace its `except InvalidPasswordException` block (~line 296) with:

```python
    except InvalidPasswordException as exc:
        errors = exc.reason if isinstance(exc.reason, list) else [str(exc.reason)]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "weak_password", "errors": errors},
        ) from None
```

- [ ] **Step 4: mypy + import sanity**

```bash
cd backend && uv run mypy cubeplex/auth/users.py cubeplex/api/routes/v1/auth.py 2>&1 | tee tmp/mypy_auth.log | tail -5
```
Expected: no new errors. (`InvalidPasswordException.reason` is typed `Any` in fastapi-users, so the `isinstance` check is fine.)

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/auth/users.py backend/cubeplex/api/routes/v1/auth.py
git commit -m "feat(auth): enforce configurable password policy at register + change-password"
```

(Full e2e coverage of high/low pass-fail + config switch lives in Task 10.)

---

### Task 4: OTP service module (Redis-backed) + unit tests

**Files:**
- Create: `backend/cubeplex/auth/email_otp.py`
- Test: `backend/tests/unit/test_email_otp.py`

**Interfaces:**
- Produces:
  - `is_email_verification_enabled() -> bool` — resolves `auth.email_verification.enabled` (`auto`→`email.backend=="smtp"`, `true`→True, `false`→False).
  - `issue_otp(email: str) -> str` — generate code, write Redis hash `{code, attempts}` with TTL, enforce resend cooldown + per-hour rate limit, send via `get_email_service()`. Returns the code (used by tests/log backend).
  - `verify_otp(email: str, code: str) -> VerifyResult` — compare; success deletes key; tracks attempts.
  - `VerifyResult` dataclass: `{ok: bool, reason: str | None, remaining_attempts: int | None}`. reason ∈ `None` (success) / `"invalid_otp"` / `"expired_or_unknown"` / `"max_attempts_reached"`.
  - Redis keys (all prefixed with the app `redis_key_prefix`): `email_otp:{email}` (hash, TTL `code_ttl_seconds`), `email_otp_sent:{email}` (string, TTL `resend_cooldown_seconds`), `email_otp_rl:{email}` (counter, TTL 3600).
- Consumes: `get_redis()` from `cubeplex.cache`, `get_email_service()` from `cubeplex.services.email`, config keys from Task 1.

- [ ] **Step 1: Write the failing unit test (fake Redis at the internal boundary)**

`backend/tests/unit/test_email_otp.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest

from cubeplex.auth.email_otp import verify_otp, VerifyResult


class FakeRedis:
    """Minimal in-memory async fake of the Redis ops email_otp uses."""

    def __init__(self) -> None:
        self.store: dict[str, dict[str, str]] = {}
        self.ttl: dict[str, int] = {}

    async def hset(self, key, mapping=None, **kwargs):
        self.store.setdefault(key, {}).update(mapping or {})
        return True

    async def hgetall(self, key):
        return dict(self.store.get(key, {}))

    async def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                n += 1
        return n

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def expire(self, key, ttl):
        self.ttl[key] = int(ttl)
        return True

    async def incr(self, key):
        v = int(self.store.get(key, {}).get("v", "0")) + 1
        self.store.setdefault(key, {})["v"] = str(v)
        return v

    async def set(self, key, value, ex=None, **kwargs):
        self.store[key] = {"v": str(value)}
        if ex is not None:
            self.ttl[key] = int(ex)
        return True


@pytest.mark.asyncio
async def test_verify_success_deletes_key():
    fake = FakeRedis()
    await fake.hset("email_otp:a@b.com", mapping={"code": "123456", "attempts": "0"})
    with patch("cubeplex.auth.email_otp.get_redis", return_value=fake):
        res = await verify_otp("a@b.com", "123456")
    assert isinstance(res, VerifyResult)
    assert res.ok is True
    assert "email_otp:a@b.com" not in fake.store  # success deletes (no replay)


@pytest.mark.asyncio
async def test_verify_wrong_code_increments_attempts():
    fake = FakeRedis()
    await fake.hset("email_otp:a@b.com", mapping={"code": "123456", "attempts": "0"})
    with patch("cubeplex.auth.email_otp.get_redis", return_value=fake):
        res = await verify_otp("a@b.com", "000000")
    assert res.ok is False
    assert res.reason == "invalid_otp"
    assert res.remaining_attempts == 4  # 5 max - 1 used


@pytest.mark.asyncio
async def test_verify_missing_key_expired():
    fake = FakeRedis()
    with patch("cubeplex.auth.email_otp.get_redis", return_value=fake):
        res = await verify_otp("a@b.com", "123456")
    assert res.ok is False
    assert res.reason == "expired_or_unknown"


@pytest.mark.asyncio
async def test_verify_max_attempts_invalidates():
    fake = FakeRedis()
    await fake.hset("email_otp:a@b.com", mapping={"code": "123456", "attempts": "4"})
    with patch("cubeplex.auth.email_otp.get_redis", return_value=fake):
        res = await verify_otp("a@b.com", "000000")
    assert res.ok is False
    assert res.reason == "max_attempts_reached"
    assert "email_otp:a@b.com" not in fake.store  # key deleted
```

(Note: `conftest.py` marks `tests/unit` to forbid real Postgres/Redis/network — the fake is an in-process collaborator, which is allowed at the unit layer. Confirm the unit conftest doesn't forbid `unittest.mock`.)

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/unit/test_email_otp.py -v --no-cov 2>&1 | tee tmp/email_otp.log | tail -5
```
Expected: FAIL with `ModuleNotFoundError: No module named 'cubeplex.auth.email_otp'`.

- [ ] **Step 3: Write the implementation**

`backend/cubeplex/auth/email_otp.py`:

```python
"""Email OTP verification service — Redis-backed, no HTTP.

Replaces the magic-link (JWT-in-URL) verification entirely. The OTP is a
short numeric code stored in Redis with a TTL; success deletes the key so
it cannot be replayed. Brute-force is bounded by max_attempts and a
per-email send rate limit.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from redis.asyncio import Redis

from cubeplex.cache import get_redis
from cubeplex.config import config
from cubeplex.services.email import get_email_service

_OTP_KEY = "email_otp:{email}"
_SENT_KEY = "email_otp_sent:{email}"
_RL_KEY = "email_otp_rl:{email}"


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    reason: str | None
    remaining_attempts: int | None


def is_email_verification_enabled() -> bool:
    raw = str(config.get("auth.email_verification.enabled", "auto")).lower()
    if raw == "true":
        return True
    if raw == "false":
        return False
    # auto
    return str(config.get("email.backend", "log")).lower() == "smtp"


def _code_length() -> int:
    return int(config.get("auth.email_verification.code_length", 6))


def _ttl() -> int:
    return int(config.get("auth.email_verification.code_ttl_seconds", 600))


def _max_attempts() -> int:
    return int(config.get("auth.email_verification.max_attempts", 5))


def _cooldown() -> int:
    return int(config.get("auth.email_verification.resend_cooldown_seconds", 60))


def _rate_limit_per_hour() -> int:
    return int(config.get("auth.email_verification.rate_limit_per_hour", 10))


def _gen_code(length: int) -> str:
    digits = "0123456789"
    return "".join(secrets.choice(digits) for _ in range(length))


class _CooldownError(Exception):
    pass


class _RateLimitError(Exception):
    pass


async def issue_otp(email: str) -> str:
    """Generate + store an OTP for `email`, send it, return the code.

    Raises _CooldownError if a resend is requested too soon, _RateLimitError
    if the per-hour send cap is exceeded. Callers translate these into HTTP.
    """
    redis = get_redis()
    sent_key = _SENT_KEY.format(email=email)
    rl_key = _RL_KEY.format(email=email)

    if await redis.exists(sent_key):
        raise _CooldownError
    sends = await redis.incr(rl_key)
    if sends == 1:
        await redis.expire(rl_key, 3600)
    if sends > _rate_limit_per_hour():
        raise _RateLimitError

    code = _gen_code(_code_length())
    key = _OTP_KEY.format(email=email)
    await redis.hset(key, mapping={"code": code, "attempts": "0"})
    await redis.expire(key, _ttl())
    await redis.set(sent_key, "1", ex=_cooldown())

    await get_email_service().send(
        to=email,
        subject="Your cubeplex verification code",
        template="email_otp_verification",
        context={"code": code, "ttl_minutes": str(_ttl() // 60)},
    )
    return code


async def verify_otp(email: str, code: str) -> VerifyResult:
    redis: Redis = get_redis()
    key = _OTP_KEY.format(email=email)
    data = await redis.hgetall(key)
    if not data:
        return VerifyResult(ok=False, reason="expired_or_unknown", remaining_attempts=None)

    stored = data.get("code")
    attempts = int(data.get("attempts", "0"))
    if stored is not None and secrets.compare_digest(str(stored), code):
        await redis.delete(key)
        return VerifyResult(ok=True, reason=None, remaining_attempts=None)

    attempts += 1
    if attempts >= _max_attempts():
        await redis.delete(key)
        return VerifyResult(ok=False, reason="max_attempts_reached", remaining_attempts=0)
    await redis.hset(key, mapping={"attempts": str(attempts)})
    return VerifyResult(
        ok=False,
        reason="invalid_otp",
        remaining_attempts=_max_attempts() - attempts,
    )
```

- [ ] **Step 4: Run unit test to verify it passes**

```bash
cd backend && uv run pytest tests/unit/test_email_otp.py -v --no-cov 2>&1 | tee tmp/email_otp.log | tail -5
```
Expected: PASS.

- [ ] **Step 5: mypy**

```bash
cd backend && uv run mypy cubeplex/auth/email_otp.py 2>&1 | tee tmp/mypy_email_otp.log | tail -5
```
Expected: no errors. (If `get_redis` return type is `redis_asyncio.Redis`, the `Redis` annotation is fine.)

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/auth/email_otp.py backend/tests/unit/test_email_otp.py
git commit -m "feat(auth): add Redis-backed email OTP service + unit tests"
```

---

### Task 5: OTP endpoints + register response + login gate + remove magic-link + OTP template

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/auth.py` (remove `get_verify_router` include; register response `verification_required`; new `/verify-otp` + `/resend-otp`; login `email_not_verified` 403)
- Modify: `backend/cubeplex/auth/users.py` (remove the `request_verify` call in `on_after_register`; remove `on_after_request_verify` magic-link body)
- Create: `backend/cubeplex/templates/email/email_otp_verification.html`, `email_otp_verification.txt`
- Delete: `backend/cubeplex/templates/email/email_verification.html`, `email_verification.txt`
- Test: `backend/tests/e2e/test_register_otp_flow.py`, `test_register_smtp_disabled.py`, `test_login_unverified_blocked.py` (full e2e in Task 10; this task lands the routes + a smoke check)

**Interfaces:**
- Consumes: `issue_otp`, `verify_otp`, `is_email_verification_enabled`, `_CooldownError`, `_RateLimitError` from Task 4.
- Produces API:
  - `POST /api/v1/auth/register` → `{id, email, default_workspace_id, verification_required: bool}`.
  - `POST /api/v1/auth/verify-otp` `{email, code}` → 200 `{ok: true}` or 400 `{code: "invalid_otp"|"otp_expired"|"otp_max_attempts", remaining_attempts?: int}`.
  - `POST /api/v1/auth/resend-otp` `{email}` → 200 `{ok: true}` (always, even for unknown email) or 429 `{code: "otp_cooldown"|"otp_rate_limited"}`.
  - `POST /api/v1/auth/login` → 403 `{code: "email_not_verified", message}` when verification enabled and `user.is_verified` is false.

- [ ] **Step 1: Create the OTP email template**

`backend/cubeplex/templates/email/email_otp_verification.txt`:
```
Your cubeplex verification code is: {{ code }}

It expires in {{ ttl_minutes }} minutes. If you didn't request this, ignore this email.
```

`backend/cubeplex/templates/email/email_otp_verification.html`:
```html
<p>Your cubeplex verification code is:</p>
<p style="font-size:24px;font-weight:bold;letter-spacing:4px">{{ code }}</p>
<p>It expires in {{ ttl_minutes }} minutes. If you didn't request this, ignore this email.</p>
```

- [ ] **Step 2: Delete the magic-link email templates**

```bash
git rm backend/cubeplex/templates/email/email_verification.html backend/cubeplex/templates/email/email_verification.txt
```

- [ ] **Step 3: Remove magic-link verify router + handlers from `auth.py`**

In `backend/cubeplex/api/routes/v1/auth.py`, delete the line:

```python
router.include_router(fastapi_users.get_verify_router(UserRead), prefix="")
```

(Confirm with `grep -n "get_verify_router" backend/cubeplex/api/routes/v1/auth.py` — should now be empty.)

- [ ] **Step 4: Remove magic-link sending from `users.py`**

In `backend/cubeplex/auth/users.py`:

(a) In `on_after_register`, delete the trailing block that calls `self.request_verify(user, request)` (the `if not user.is_verified:` block at the end of `on_after_register`). Register no longer auto-sends any verification email; the OTP is issued by the `/register` endpoint via `issue_otp` when verification is enabled.

(b) Replace the `on_after_request_verify` method body so it no longer sends a magic-link email (the route is gone; keep the method as a no-op override to satisfy fastapi-users' abstract base, or delete it if the base allows — verify by `grep -n "on_after_request_verify" backend/cubeplex/`. If fastapi-users requires it, make it a no-op `pass`). Simplest: delete the method entirely if no remaining route triggers it. Confirm nothing else references it.

- [ ] **Step 5: Update the register endpoint to issue OTP + return `verification_required`**

In `backend/cubeplex/api/routes/v1/auth.py`, replace the `register` handler's tail (the `default_ws = ...; return {...}` part, ~lines 67-72) with:

```python
    from cubeplex.auth.email_otp import is_email_verification_enabled, issue_otp

    verification_required = False
    if is_email_verification_enabled():
        verification_required = True
        try:
            await issue_otp(user.email)
        except Exception:
            # OTP send failure must not leak; the user can resend from /verify-otp.
            logger.warning("Failed to issue OTP for {}", user.email)
    default_ws = getattr(user, "_default_workspace_id", None)
    return {
        "id": user.id,
        "email": user.email,
        "default_workspace_id": default_ws or "",
        "verification_required": verification_required,
    }
```

Add `from loguru import logger` to the imports if not already present (check `grep -n "^from loguru" backend/cubeplex/api/routes/v1/auth.py`).

Note: when verification is enabled, `on_after_register` (Task 8/9) must NOT run bootstrap — that's enforced in Task 8/9. For this task the register path still calls `user_manager.create` which triggers `on_after_register`; Task 8 makes `on_after_register` a no-op bootstrap when verification is enabled. To keep Task 5 independently testable, also guard here: the OTP gate simply defers the cookie — register never set a cookie anyway.

- [ ] **Step 6: Add `/verify-otp` and `/resend-otp` endpoints**

In `backend/cubeplex/api/routes/v1/auth.py`, add (after the `register` handler, before `login`):

```python
class VerifyOtpRequest(BaseModel):
    email: str
    code: str


class ResendOtpRequest(BaseModel):
    email: str


_OTP_VERIFY_LIMIT = f"{config.get('auth.rate_limit.login_per_minute', 5)}/minute"
_OTP_RESEND_LIMIT = f"{config.get('auth.rate_limit.login_per_minute', 5)}/minute"


@router.post("/verify-otp")
@limiter.limit(_OTP_VERIFY_LIMIT)
async def verify_otp_endpoint(
    request: Request,
    body: Annotated[VerifyOtpRequest, Body()],
) -> dict[str, object]:
    from cubeplex.auth.email_otp import verify_otp

    result = await verify_otp(body.email, body.code)
    if result.ok:
        return {"ok": True}
    code_map = {
        "invalid_otp": "invalid_otp",
        "expired_or_unknown": "otp_expired",
        "max_attempts_reached": "otp_max_attempts",
    }
    detail: dict[str, object] = {"code": code_map.get(result.reason, "otp_expired")}
    if result.remaining_attempts is not None:
        detail["remaining_attempts"] = result.remaining_attempts
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


@router.post("/resend-otp")
@limiter.limit(_OTP_RESEND_LIMIT)
async def resend_otp_endpoint(
    request: Request,
    body: Annotated[ResendOtpRequest, Body()],
) -> dict[str, bool]:
    from cubeplex.auth.email_otp import _CooldownError, _RateLimitError, issue_otp

    try:
        await issue_otp(body.email)
    except _CooldownError:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "otp_cooldown"},
        ) from None
    except _RateLimitError:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "otp_rate_limited"},
        ) from None
    except Exception:
        logger.warning("Failed to resend OTP for {}", body.email)
    # Always return ok — no email enumeration.
    return {"ok": True}
```

(Confirm `config` is imported in `auth.py` — it is used elsewhere; check `grep -n "from cubeplex.config import config" backend/cubeplex/api/routes/v1/auth.py`. `BaseModel` import: check `grep -n "from pydantic" backend/cubeplex/api/routes/v1/auth.py`.)

- [ ] **Step 7: Add the `email_not_verified` 403 gate to `login`**

In `backend/cubeplex/api/routes/v1/auth.py`, in the `login` handler, immediately after the `if user is None or not user.is_active:` block (which raises bad credentials) and **before** the SSO enforcement block, add:

```python
    from cubeplex.auth.email_otp import is_email_verification_enabled

    if is_email_verification_enabled() and not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "email_not_verified", "message": _t("login_email_not_verified")},
        )
```

- [ ] **Step 8: mypy + import sanity**

```bash
cd backend && uv run mypy cubeplex/api/routes/v1/auth.py cubeplex/auth/users.py 2>&1 | tee tmp/mypy_task5.log | tail -5
```
Expected: no new errors.

- [ ] **Step 9: Commit**

```bash
git add backend/cubeplex/api/routes/v1/auth.py backend/cubeplex/auth/users.py backend/cubeplex/templates/email/
git commit -m "feat(auth): OTP verify/resend endpoints + login email_not_verified gate; remove magic-link"
```

(Full e2e: register→OTP→verify→login, smtp-disabled, unverified-blocked — Task 10.)

---

### Task 6: OrgInviteToken model + prefix + migration + repository

**Files:**
- Modify: `backend/cubeplex/models/public_id.py` (add `PREFIX_ORG_INVITE`)
- Create: `backend/cubeplex/models/org_invite_token.py`
- Modify: `backend/cubeplex/models/__init__.py` (export)
- Create: `backend/cubeplex/repositories/org_invite_token.py`
- Create: alembic migration (autogen)

**Interfaces:**
- Produces: `OrgInviteToken` model (table `org_invite_tokens`): `id` (PK, public-id `oinv` prefix), `org_id` (FK orgs.id), `role` (str, OrgRole value), `created_by` (FK users.id), `expires_at` (tz-aware), `used_at` (tz-aware nullable). `OrgInviteTokenRepository` with `issue(*, org_id, role, created_by) -> OrgInviteToken` and `consume(token) -> OrgInviteToken | None` (mirrors `InviteTokenRepository`).
- Note: unlike `InviteToken` (whose PK is the uuid7 `token` itself), `OrgInviteToken` uses a public-id `id` PK **and** a separate `token` field (uuid7 string) that is the value exchanged in the accept URL. This keeps the public-facing token opaque while giving the table a stable public id. Mirror `InviteToken`'s `token` field for the exchange value.

- [ ] **Step 1: Add the public ID prefix**

In `backend/cubeplex/models/public_id.py`, add to the prefix constants:

```python
PREFIX_ORG_INVITE: str = "oinv"
```

- [ ] **Step 2: Create the model**

`backend/cubeplex/models/org_invite_token.py`:

```python
"""Org-scoped invite token — single-use, time-limited org invitation.

Coexists with the workspace-scoped InviteToken. Accepting an org invite
grants an OrganizationMembership (ADMIN or MEMBER only — never OWNER).
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import Column, DateTime, Index
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7

from cubeplex.models.public_id import PREFIX_ORG_INVITE, generate_public_id


def _default_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(hours=24)


class OrgInviteToken(SQLModel, table=True):
    __tablename__ = "org_invite_tokens"
    __table_args__ = (Index("ix_org_invite_tokens_expires", "expires_at"),)

    id: str = Field(
        primary_key=True,
        max_length=20,
        default_factory=lambda: generate_public_id(PREFIX_ORG_INVITE),
    )
    token: str = Field(
        default_factory=lambda: str(uuid7()),
        max_length=64,
        index=True,
        unique=True,
    )
    org_id: str = Field(foreign_key="organizations.id", max_length=20)
    role: str = Field(max_length=32)  # OrgRole value: "admin" | "member"
    created_by: str = Field(foreign_key="users.id", max_length=20)
    expires_at: datetime = Field(
        default_factory=_default_expiry,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    used_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
```

(Confirm `generate_public_id` is importable from `cubeplex.models.public_id` — yes per Task 2 grep. Confirm FK table name `organizations.id`: `grep -n "__tablename__" backend/cubeplex/models/organization.py`.)

- [ ] **Step 3: Export the model**

In `backend/cubeplex/models/__init__.py`, add the import (near the `InviteToken` import) and the name to `__all__`:

```python
from cubeplex.models.org_invite_token import OrgInviteToken
```
and `"OrgInviteToken",` in `__all__`.

- [ ] **Step 4: Create the repository**

`backend/cubeplex/repositories/org_invite_token.py` (mirror `invite_token.py`):

```python
"""Org invite token repository — single-use + time-limited."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import OrgInviteToken


class OrgInviteTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def issue(self, *, org_id: str, role: str, created_by: str) -> OrgInviteToken:
        tok = OrgInviteToken(org_id=org_id, role=role, created_by=created_by)
        self.session.add(tok)
        await self.session.commit()
        await self.session.refresh(tok)
        return tok

    async def consume(self, token: str) -> OrgInviteToken | None:
        """Atomically mark token used. None if expired/used/missing."""
        stmt = (
            select(OrgInviteToken)
            .where(OrgInviteToken.token == token)  # type: ignore[arg-type]
            .with_for_update()
        )
        tok = (await self.session.execute(stmt)).scalar_one_or_none()
        if tok is None:
            return None
        now = datetime.now(UTC)
        expires_at = tok.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if tok.used_at is not None or expires_at < now:
            return None
        tok.used_at = now
        await self.session.commit()
        await self.session.refresh(tok)
        return tok
```

Add `OrgInviteTokenRepository` to `backend/cubeplex/repositories/__init__.py` exports (check the existing `InviteTokenRepository` export line and mirror it: `grep -n "InviteTokenRepository" backend/cubeplex/repositories/__init__.py`).

- [ ] **Step 5: Generate the migration**

```bash
cd backend && uv run alembic revision --autogenerate -m "add org_invite_tokens table" 2>&1 | tee tmp/alembic_org_invite.log | tail -10
```
Open the generated migration and verify:
- It creates `org_invite_tokens` with `id`, `token` (unique index), `org_id` FK→`organizations.id`, `created_by` FK→`users.id`, `expires_at` (timestamptz), `used_at` (timestamptz nullable), and the `ix_org_invite_tokens_expires` index.
- Both `expires_at` and `used_at` are `DateTime(timezone=True)` (autogen should produce this from the `sa_column`). If autogen emitted plain `DateTime` for either, hand-fix per the CLAUDE.md rule is **not allowed** (no hand-editing migrations) — instead fix the model's `sa_column` and re-run autogen. Do not add `postgresql_using` on `create_table` columns (that rule applies to `alter_column` of existing columns, not new-table creation).

- [ ] **Step 6: Apply the migration to the worktree test DB**

```bash
cd backend && uv run alembic upgrade head 2>&1 | tee tmp/alembic_upgrade.log | tail -5
```
Expected: `Running upgrade <prev> -> <new>, add org_invite_tokens table`.

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/models/public_id.py backend/cubeplex/models/org_invite_token.py backend/cubeplex/models/__init__.py backend/cubeplex/repositories/org_invite_token.py backend/cubeplex/repositories/__init__.py backend/cubeplex/migrations/versions/
git commit -m "feat(model): add OrgInviteToken table + repository + migration"
```

---

### Task 7: Org-invite create (admin) + accept (auth) endpoints

**Files:**
- Create: `backend/cubeplex/api/routes/v1/org_invites.py`
- Modify: `backend/cubeplex/api/app.py` (register the router)

**Interfaces:**
- Consumes: `OrgInviteTokenRepository` (Task 6), `require_org_admin` + `resolve_current_org_id` (`cubeplex.auth.dependencies`), `OrganizationMembershipRepository.grant`, `OrgRole`.
- Produces API:
  - `POST /api/v1/admin/orgs/invites` `{role}` (org-admin; org resolved via `resolve_current_org_id`) → 201 `{token, expires_at, role}`. `role` ∈ `admin`/`member` only; `owner` → 400.
  - `POST /api/v1/orgs/invites/accept` `{token}` (auth required) → 200 `{org_id, role}`. Consumes token, grants `OrganizationMembership(role=invite role)` if the user isn't already a member; idempotent if already a member (does not re-grant, does not fail).

- [ ] **Step 1: Create the router**

`backend/cubeplex/api/routes/v1/org_invites.py`:

```python
"""Org-scoped invite routes.

Create is org-admin scoped (org resolved from the admin session). Accept is
auth-scoped — any logged-in user holding a valid token joins the org at the
invite's role. Invite role is limited to ADMIN/MEMBER (never OWNER).
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.dependencies import current_active_user, require_org_admin, resolve_current_org_id
from cubeplex.db import get_session
from cubeplex.models import OrgRole, User
from cubeplex.repositories import OrganizationMembershipRepository, OrgInviteTokenRepository
from cubeplex.utils.time import utc_isoformat

router = APIRouter(tags=["org-invites"])

_ADMIN_ROUTER = APIRouter(prefix="/admin/orgs/invites", tags=["org-invites"])
_ACCEPT_ROUTER = APIRouter(prefix="/orgs/invites", tags=["org-invites"])

_ASSIGNABLE_ORG_ROLES = {OrgRole.ADMIN, OrgRole.MEMBER}


class CreateOrgInviteRequest(BaseModel):
    role: str


class OrgInviteOut(BaseModel):
    token: str
    expires_at: str
    role: str


@_ADMIN_ROUTER.post("", response_model=OrgInviteOut, status_code=status.HTTP_201_CREATED)
async def create_org_invite(
    body: Annotated[CreateOrgInviteRequest, Body()],
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrgInviteOut:
    try:
        role = OrgRole(body.role)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_role") from None
    if role not in _ASSIGNABLE_ORG_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="role_not_assignable"
        )
    org_id = await resolve_current_org_id(user, session)
    tok = await OrgInviteTokenRepository(session).issue(
        org_id=org_id, role=role, created_by=user.id
    )
    return OrgInviteOut(token=tok.token, expires_at=utc_isoformat(tok.expires_at), role=tok.role)


class AcceptOrgInviteRequest(BaseModel):
    token: str


class AcceptOrgInviteResponse(BaseModel):
    org_id: str
    role: str


@_ACCEPT_ROUTER.post("/accept", response_model=AcceptOrgInviteResponse)
async def accept_org_invite(
    body: Annotated[AcceptOrgInviteRequest, Body()],
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AcceptOrgInviteResponse:
    tok = await OrgInviteTokenRepository(session).consume(body.token)
    if tok is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invite_invalid_or_expired",
        )
    om_repo = OrganizationMembershipRepository(session)
    existing = await om_repo.get_role(user_id=user.id, org_id=tok.org_id)
    if existing is None:
        await om_repo.grant(user_id=user.id, org_id=tok.org_id, role=OrgRole(tok.role))
    return AcceptOrgInviteResponse(org_id=tok.org_id, role=tok.role)


router.include_router(_ADMIN_ROUTER)
router.include_router(_ACCEPT_ROUTER)
```

(Confirm `OrganizationMembershipRepository.get_role(*, user_id, org_id)` and `.grant(*, user_id, org_id, role)` signatures — yes per the existing `accept_invite` in `workspaces.py`. Confirm `OrgInviteTokenRepository` is exported from `cubeplex.repositories` — Task 6 Step 4 adds it. Confirm `current_active_user` import path — yes.)

- [ ] **Step 2: Register the router in `app.py`**

In `backend/cubeplex/api/app.py`, near the other `include_router` calls (after `workspaces_router`), add:

```python
    from cubeplex.api.routes.v1 import org_invites as org_invites_routes
    app.include_router(org_invites_routes.router, prefix="/api/v1")
```

- [ ] **Step 3: mypy**

```bash
cd backend && uv run mypy cubeplex/api/routes/v1/org_invites.py 2>&1 | tee tmp/mypy_org_invites.log | tail -5
```
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/api/routes/v1/org_invites.py backend/cubeplex/api/app.py
git commit -m "feat(org-invites): admin create + auth accept endpoints"
```

(Full e2e: expired/reused token, role-limited-to-ADMIN/MEMBER, accept-then-onboarding — Task 10.)

---

### Task 8: Refactor bootstrap into shared helpers in `users.py`

**Files:**
- Modify: `backend/cubeplex/auth/users.py`

**Interfaces:**
- Produces module-level helpers (used by Task 9's onboarding router):
  - `async def _bootstrap_org_and_workspace(session, *, user_id, org_name, org_slug, workspace_name) -> tuple[Organization, Workspace]` — creates org, `OrganizationMembership(OWNER)`, workspace, `Membership(ADMIN)`, `AgentConfig`, MCP enrollment, preinstalled skills; returns (org, ws). Wraps in try/except + `_best_effort_cleanup_register`. (This is the body of the current `_on_register_multi_tenant` extracted, parameterized by names instead of derived from email.)
  - `async def _bootstrap_workspace_in_org(session, *, user_id, org_id, workspace_name) -> Workspace` — creates workspace in an existing org + `Membership(ADMIN)` + `AgentConfig` + MCP + skills. (Body of the single-tenant subsequent-user path extracted.)
  - Keeps `_best_effort_cleanup_register`, `_install_preinstalled_skills`, `_install_preinstalled_skills_safe`, `_slugify_org_name`, `_allocate_org_slug` as-is.
- Behavior change: `on_after_register` no longer auto-bootstraps in `multi_tenant` when verification is enabled — it leaves the user pending-onboarding. When verification is **disabled**, keep today's behavior? **No** — per spec §4, multi_tenant register creates the user only (no org/workspace) in all cases; onboarding is deferred to the wizard. So `_on_register_multi_tenant` becomes a no-op (just logs). `_on_register_single_tenant` keeps its current behavior (first user pending; subsequent users auto-attach) because the spec non-goal explicitly preserves single-tenant subsequent-user auto-attach. **But** when verification is enabled, single_tenant register also defers: first user stays pending (no org), subsequent users... still auto-attach (verification gates login, not bootstrap). Decision: verification-enabled does not change single_tenant bootstrap; it only changes multi_tenant (defer) — because multi_tenant's deferred bootstrap is the whole point of the wizard.

- [ ] **Step 1: Extract `_bootstrap_org_and_workspace`**

In `backend/cubeplex/auth/users.py`, add a module-level function (place it near `_install_preinstalled_skills`, before the `UserManager` class):

```python
async def _bootstrap_org_and_workspace(
    session: AsyncSession,
    *,
    user_id: str,
    org_name: str,
    org_slug: str,
    workspace_name: str,
) -> tuple[Organization, "Workspace"]:
    """Full first-owner bootstrap: org + workspace + memberships + AgentConfig + MCP + skills."""
    from cubeplex.mcp.workspace_bootstrap import enroll_workspace_in_org_wide_mcp
    from cubeplex.models import OrgRole, Role
    from cubeplex.models.agent_config import AgentConfig
    from cubeplex.repositories import (
        MembershipRepository,
        OrganizationMembershipRepository,
        OrganizationRepository,
        WorkspaceRepository,
    )

    org = await OrganizationRepository(session).create(name=org_name, slug=org_slug)
    await OrganizationMembershipRepository(session).grant(
        user_id=user_id, org_id=org.id, role=OrgRole.OWNER
    )
    ws = await WorkspaceRepository(session).create(org_id=org.id, name=workspace_name)
    await MembershipRepository(session).grant(user_id=user_id, workspace_id=ws.id, role=Role.ADMIN)
    session.add(AgentConfig(org_id=org.id, workspace_id=ws.id))
    await enroll_workspace_in_org_wide_mcp(
        session, org_id=org.id, workspace_id=ws.id, actor_user_id=user_id
    )
    await session.flush()
    await _install_preinstalled_skills_safe(session, org_id=org.id, user_id=user_id)
    return org, ws
```

Add the `Workspace` type import at module top (or use a string forward-ref + import inside — shown as forward-ref `"Workspace"`; add `from cubeplex.models import Workspace` to the top imports if not present: `grep -n "from cubeplex.models import" backend/cubeplex/auth/users.py`). Use a top-level import to satisfy mypy.

- [ ] **Step 2: Extract `_bootstrap_workspace_in_org`**

```python
async def _bootstrap_workspace_in_org(
    session: AsyncSession,
    *,
    user_id: str,
    org_id: str,
    workspace_name: str,
) -> "Workspace":
    """Create a workspace in an existing org for a user who already has an org membership."""
    from cubeplex.mcp.workspace_bootstrap import enroll_workspace_in_org_wide_mcp
    from cubeplex.models import Role
    from cubeplex.models.agent_config import AgentConfig
    from cubeplex.repositories import MembershipRepository, WorkspaceRepository

    ws = await WorkspaceRepository(session).create(org_id=org_id, name=workspace_name)
    await MembershipRepository(session).grant(user_id=user_id, workspace_id=ws.id, role=Role.ADMIN)
    session.add(AgentConfig(org_id=org_id, workspace_id=ws.id))
    await enroll_workspace_in_org_wide_mcp(
        session, org_id=org_id, workspace_id=ws.id, actor_user_id=user_id
    )
    await session.flush()
    await _install_preinstalled_skills_safe(session, org_id=org_id, user_id=user_id)
    return ws
```

- [ ] **Step 3: Simplify `_on_register_multi_tenant` to defer bootstrap**

Replace the body of `_on_register_multi_tenant` with a no-op (the wizard now does the bootstrap):

```python
    async def _on_register_multi_tenant(self, *, user: User, session: AsyncSession) -> None:
        """multi_tenant: register creates the user only — onboarding wizard bootstraps org/workspace."""
        user._default_workspace_id = None
```

- [ ] **Step 4: Refactor `_on_register_single_tenant` to call the helpers (subsequent-user path)**

In `_on_register_single_tenant`, the subsequent-user branch (after `singleton_org_id` is resolved, the `try:` block that grants MEMBER + creates Personal workspace) — replace the inlined logic with a call to `_bootstrap_workspace_in_org`, but note the existing code grants `OrgRole.MEMBER` (not ADMIN) at the org level for subsequent single-tenant users. Preserve that: keep the explicit `om_repo.grant(... OrgRole.MEMBER)` call, then call `_bootstrap_workspace_in_org` for the workspace portion. Concretely, replace the `try: ... except:` block with:

```python
        try:
            await OrganizationMembershipRepository(session).grant(
                user_id=user.id, org_id=singleton_org_id, role=OrgRole.MEMBER
            )
            ws = await _bootstrap_workspace_in_org(
                session, user_id=user.id, org_id=singleton_org_id, workspace_name="Personal"
            )
        except Exception as exc:
            await self._best_effort_cleanup_register(user=user, org=None, session=session)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="REGISTER_BOOTSTRAP_FAILED",
            ) from exc
        user._default_workspace_id = ws.id
```

(Remove the now-unused inline `MembershipRepository`/`WorkspaceRepository`/`AgentConfig`/`enroll_workspace_in_org_wide_mcp` imports from this method if they're no longer referenced — but `_bootstrap_workspace_in_org` imports them itself. Keep `OrgRole` import for the MEMBER grant. Run mypy to catch unused imports.)

- [ ] **Step 5: mypy + existing register e2e still green (single_tenant path)**

```bash
cd backend && uv run mypy cubeplex/auth/users.py 2>&1 | tee tmp/mypy_task8.log | tail -5
cd backend && uv run pytest tests/e2e/test_single_tenant_register.py --no-cov 2>&1 | tee tmp/task8_st_register.log | tail -5
```
Expected: mypy clean; single_tenant register e2e still passes (subsequent users still get a Personal workspace + `_default_workspace_id`). The multi_tenant register e2e (legacy `register → /w/{wsId}`) will now **break** because multi_tenant no longer auto-creates a workspace — that's expected and is updated in Task 10 (the legacy test is replaced by the onboarding flow). If other multi_tenant e2e rely on auto-bootstrap, note them for Task 10.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/auth/users.py
git commit -m "refactor(auth): extract bootstrap helpers; defer multi_tenant bootstrap to onboarding"
```

---

### Task 9: Onboarding router + retire /system/setup + needs_onboarding

**Files:**
- Create: `backend/cubeplex/api/routes/v1/onboarding.py`
- Modify: `backend/cubeplex/api/app.py` (register onboarding router)
- Modify: `backend/cubeplex/api/routes/v1/system.py` (remove `post_setup`; keep `/info`, drop `needs_org_setup` from response)
- Modify: `backend/cubeplex/api/schemas/system.py` (drop `needs_org_setup` from `SystemInfoResponse`; delete `SetupRequest`/`SetupResponse`)
- Modify: `backend/cubeplex/api/routes/v1/auth.py` (`_me_payload`: replace `needs_org_setup` with `needs_onboarding`)

**Interfaces:**
- Consumes: `_bootstrap_org_and_workspace`, `_bootstrap_workspace_in_org` (Task 8); `OrganizationMembershipRepository.get_role`; `OrganizationRepository.create` (raises `IntegrityError` on slug collision); `resolve_current_org_id` is **not** used (onboarding acts on the caller's own memberships).
- Produces API:
  - `POST /api/v1/onboarding` `{org_name?, org_slug?, workspace_name}` (auth) → 201 `{workspace_id}`. Mode inferred: caller has no org membership → Full (requires org_name + org_slug); caller has an org but no workspace → Workspace-only (requires workspace_name); caller already has a workspace → 409 `onboarding_not_required`. Slug collision → 409 `slug_taken`. single_tenant subsequent users (already in the singleton org with a workspace) → 409 `onboarding_not_required`.
  - `MeResult.needs_onboarding: bool` (replaces `needs_org_setup`).
  - `SystemInfoResponse` drops `needs_org_setup`.

- [ ] **Step 1: Create the onboarding router**

`backend/cubeplex/api/routes/v1/onboarding.py`:

```python
"""Post-registration onboarding: provision the caller's first org/workspace.

Mode is inferred from the caller's current memberships — no `mode` param.
Full = no org yet (needs org_name + org_slug + workspace_name).
Workspace-only = has an org, no workspace (needs workspace_name).
Already onboarded = 409 onboarding_not_required.
"""

import re
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.dependencies import current_active_user
from cubeplex.db import get_session
from cubeplex.models import Membership, OrganizationMembership, User

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


class OnboardingRequest(BaseModel):
    org_name: str | None = Field(default=None, min_length=2, max_length=64)
    org_slug: str | None = Field(default=None, max_length=32)
    workspace_name: str = Field(min_length=1, max_length=64)

    @field_validator("org_slug")
    @classmethod
    def _check_slug(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if len(v) < 3:
            raise ValueError("slug_too_short")
        if not _SLUG_RE.match(v):
            raise ValueError("slug_invalid_format")
        return v


class OnboardingResponse(BaseModel):
    workspace_id: str


@router.post("", response_model=OnboardingResponse, status_code=status.HTTP_201_CREATED)
async def complete_onboarding(
    request: Request,
    body: Annotated[OnboardingRequest, Body()],
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OnboardingResponse:
    from cubeplex.auth.users import _bootstrap_org_and_workspace, _bootstrap_workspace_in_org
    from cubeplex.repositories import OrganizationMembershipRepository

    om_repo = OrganizationMembershipRepository(session)
    # Any org membership?
    org_rows = (
        (
            await session.execute(
                select(OrganizationMembership).where(
                    OrganizationMembership.user_id == user.id  # type: ignore[arg-type]
                )
            )
        )
        .scalars()
        .all()
    )
    # Any workspace membership?
    ws_rows = (
        (
            await session.execute(
                select(Membership).where(Membership.user_id == user.id)  # type: ignore[arg-type]
            )
        )
        .scalars()
        .all()
    )

    if ws_rows:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="onboarding_not_required"
        )

    try:
        if not org_rows:
            # Full mode.
            if not body.org_name or not body.org_slug:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="org_name_and_slug_required",
                )
            _, ws = await _bootstrap_org_and_workspace(
                session,
                user_id=user.id,
                org_name=body.org_name,
                org_slug=body.org_slug,
                workspace_name=body.workspace_name,
            )
        else:
            # Workspace-only mode: caller already in an org.
            org_id = org_rows[0].org_id
            ws = await _bootstrap_workspace_in_org(
                session,
                user_id=user.id,
                org_id=org_id,
                workspace_name=body.workspace_name,
            )
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="slug_taken") from exc
    except HTTPException:
        raise
    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ONBOARDING_FAILED",
        ) from exc

    await session.commit()
    return OnboardingResponse(workspace_id=ws.id)
```

(Confirm `Membership` model is exported from `cubeplex.models` — yes. Confirm `_bootstrap_org_and_workspace`/`_bootstrap_workspace_in_org` flush but do not commit — the onboarding handler commits once after success. Check whether the helpers call `_install_preinstalled_skills_safe` which may flush; that's fine, the final commit covers it. If the helpers commit internally, remove that — they currently only `flush`. Verify with the Task 8 code: yes, only `flush`.)

- [ ] **Step 2: Register the onboarding router**

In `backend/cubeplex/api/app.py`, add near the other includes:

```python
    from cubeplex.api.routes.v1 import onboarding as onboarding_routes
    app.include_router(onboarding_routes.router, prefix="/api/v1")
```

- [ ] **Step 3: Retire `POST /system/setup`; simplify `/system/info`**

In `backend/cubeplex/api/routes/v1/system.py`:
- Delete the entire `post_setup` function.
- In `get_system_info`, remove the `needs_setup` computation and the `needs_org_setup=needs_setup` field. Keep `deployment_mode`, `version`, `sandbox_enabled`. Remove now-unused imports (`acquire_setup_lock`, `org_count`, the repo imports, `IntegrityError`, `OrgRole`, `Role`, `AgentConfig`, `SetupRequest`/`SetupResponse`).

In `backend/cubeplex/api/schemas/system.py`:
- Remove `needs_org_setup: bool` from `SystemInfoResponse`.
- Delete `SetupRequest` and `SetupResponse` classes (and the `_SLUG_RE`/`field_validator` if now unused — keep `_SLUG_RE` only if still referenced; it won't be, so remove it and the `re` import).

- [ ] **Step 4: Replace `needs_org_setup` with `needs_onboarding` in `_me_payload`**

In `backend/cubeplex/api/routes/v1/auth.py`, `_me_payload`:
- Replace the `needs_setup` logic (the `mode == "single_tenant"` branch computing `needs_setup` from org_count / membership) with a unified `needs_onboarding` computation:

```python
    # needs_onboarding = user has no workspace membership yet (pending wizard).
    from cubeplex.models import Membership

    ws_membership_count = (
        await session.execute(
            select(func.count())
            .select_from(Membership)
            .where(Membership.user_id == user.id)  # type: ignore[arg-type]
        )
    ).scalar_one()
    needs_onboarding = int(ws_membership_count) == 0
```

- Change the return dict key from `"needs_org_setup": needs_setup,` to `"needs_onboarding": needs_onboarding,`.
- Remove the now-unused `OrganizationMembership` import inside `_me_payload` if no longer referenced (the `org_memberships` query still uses it — keep it). Keep the `org_memberships` block as-is.

- [ ] **Step 5: mypy + import sanity**

```bash
cd backend && uv run mypy cubeplex/api/routes/v1/onboarding.py cubeplex/api/routes/v1/system.py cubeplex/api/routes/v1/auth.py cubeplex/api/schemas/system.py 2>&1 | tee tmp/mypy_task9.log | tail -5
```
Expected: no new errors.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/api/routes/v1/onboarding.py backend/cubeplex/api/app.py backend/cubeplex/api/routes/v1/system.py backend/cubeplex/api/schemas/system.py backend/cubeplex/api/routes/v1/auth.py
git commit -m "feat(onboarding): POST /onboarding router; retire /system/setup; needs_onboarding in /me"
```

---

### Task 10: Backend i18n keys + e2e tests

**Files:**
- Modify: `backend/cubeplex/i18n/messages/en/LC_MESSAGES/messages.po`, `backend/cubeplex/i18n/messages/zh/LC_MESSAGES/messages.po`
- Create: `backend/tests/e2e/test_register_otp_flow.py`, `test_register_smtp_disabled.py`, `test_login_unverified_blocked.py`, `test_password_policy_e2e.py`, `test_onboarding.py`, `test_invite_onboarding.py`
- Modify: existing multi_tenant register e2e that assumed auto-bootstrap (update to drive the onboarding wizard) — find via `grep -rln "register" backend/tests/e2e/ | xargs grep -l "multi_tenant\|/w/"`.

**Interfaces:**
- Consumes: Tasks 1–9. Test fixtures: `fresh_db_unauth_client_single_tenant`, `unauthenticated_memory_client` (multi_tenant), `session_factory`, helper `_login(client, email, password)` from `tests/e2e/conftest.py` (read `backend/tests/e2e/conftest.py` for the exact fixture names + the CSRF-cookie dance before authoring tests).

- [ ] **Step 1: Add i18n keys**

In both `en` and `zh` `messages.po`, add entries for:
- `login_email_not_verified` — en: "Please verify your email before signing in." zh: "请先验证邮箱再登录。"
- `register_invalid_password` — keep (still used? No — Task 3 replaced it with `weak_password`. Remove the unused key only if nothing references it; safer to leave it.)

Compile: `cd backend && uv run python -m msgfmt ...` is not needed if the i18n loader reads `.po` directly — check `grep -rn "msgfmt\|\.mo\|mofile\|gettext" backend/cubeplex/i18n/`. If `.mo` files are committed, regenerate them.

- [ ] **Step 2: Read conftest fixtures**

```bash
cd backend && grep -n "def fresh_db_unauth_client_single_tenant\|def unauthenticated_memory_client\|def _login\|def session_factory\|rate_limit\|flush" tests/e2e/conftest.py | head -30
```
Use the exact fixture names + the CSRF-get-before-login pattern the existing `test_single_tenant_register.py` uses.

- [ ] **Step 3: `test_register_otp_flow.py`** — verification ON (config.test.yaml forces `enabled: true`): register returns `verification_required: true` and sets **no** cookie; OTP code is recoverable from the log backend output (the `LogEmailBackend` prints the rendered email to stdout — capture via `caplog`/fixture that reads the log, or read the Redis `email_otp:{email}` hash via the test's redis handle to get the code); `/verify-otp` with the code → 200 `{ok: true}`; a second verify with the same code → 400 (key deleted); `/login` now sets a cookie; `GET /me` → `is_verified: true`, `needs_onboarding: true`. Also: wrong code → 400 `invalid_otp` with `remaining_attempts`; `max_attempts` wrong guesses → 400 `otp_max_attempts` + key deleted; resend within cooldown → 429 `otp_cooldown`. **No fire-and-forget sleeps** — poll Redis for the `email_otp_sent:{email}` key TTL or just assert on the immediate 429.

- [ ] **Step 4: `test_register_smtp_disabled.py`** — temporarily override `app.state`/config so `is_email_verification_enabled()` returns False (set `CUBEPLEX_AUTH__EMAIL_VERIFICATION__ENABLED=false` via the test app config override, or monkeypatch `cubeplex.auth.email_otp.is_email_verification_enabled`): register returns `verification_required: false`; `is_verified` becomes true; `/login` works without an OTP step.

- [ ] **Step 5: `test_login_unverified_blocked.py`** — register (verification ON) → do NOT verify → `/login` → 403 `email_not_verified`; then verify → `/login` → 200.

- [ ] **Step 6: `test_password_policy_e2e.py`** — high policy (default): register with `weak` → 400 `weak_password` with `errors` containing `password_too_short`; register with `NoSymbol1` → 400 with `password_no_symbol`; register with a strong password → 201. Override config to `low`: register with `12345678` → 201. Change-password: same matrix. (Config override via the test app's `config.set(...)` or env — match how other tests override config: `grep -rn "config.set\|deployment_mode" backend/tests/e2e/conftest.py`.)

- [ ] **Step 7: `test_onboarding.py`** —
  - Full mode (multi_tenant first registrant, verified): register → verify → login → `GET /me` `needs_onboarding: true` → `POST /onboarding {org_name, org_slug, workspace_name}` → 201 `{workspace_id}` → `GET /me` `needs_onboarding: false` + `org_memberships` non-empty. Assert AgentConfig + preinstalled skills created (query the tables) + MCP enrollment (or assert no error).
  - Full mode single_tenant first owner: same, with `fresh_db_unauth_client_single_tenant`.
  - Workspace-only mode: pre-grant the user an org membership (via `OrganizationMembershipRepository.grant` directly in the test session), then `POST /onboarding {workspace_name}` (no org fields) → 201; `GET /me` `needs_onboarding: false`.
  - Slug collision → 409 `slug_taken`.
  - Already-onboarded (user with a workspace) → 409 `onboarding_not_required`.
  - Rollback on injected failure: monkeypatch `_bootstrap_org_and_workspace` to raise → 500 `ONBOARDING_FAILED` + no partial org row left (assert `Organization` count unchanged).
  - Cleanup: delete created orgs/workspaces per test (per the cleanup discipline).

- [ ] **Step 8: `test_invite_onboarding.py`** —
  - Workspace invite: org-admin creates a workspace invite (existing `POST /workspaces/{ws}/invites`); a second user registers + verifies + logs in → `POST /workspaces/invites/accept {token}` → `GET /me` `needs_onboarding: false` (has a workspace).
  - Org invite: org-admin creates via `POST /admin/orgs/invites {role: "member"}`; second user registers + verifies + logs in → `POST /orgs/invites/accept {token}` → `GET /me` `needs_onboarding: true` (has org, no ws) → workspace-only wizard → `/onboarding {workspace_name}` → `needs_onboarding: false`.
  - Org-invite role `owner` → 400 `role_not_assignable` at create.
  - Expired/reused org-invite token → 400 `invite_invalid_or_expired` (plant an expired token or call accept twice).

- [ ] **Step 9: Update legacy multi_tenant register e2e**

Any existing e2e that registered in multi_tenant and expected to land in `/w/{wsId}` now breaks (no auto-bootstrap). Update those tests to drive the onboarding wizard (register → verify → login → `/onboarding` → workspace) so they end at a real workspace. Grep: `cd backend && grep -rln "register" tests/e2e/`. Do the same for Playwright (Task 16).

- [ ] **Step 10: Run the full backend e2e suite for this feature**

```bash
cd backend && uv run pytest tests/e2e/test_register_otp_flow.py tests/e2e/test_register_smtp_disabled.py tests/e2e/test_login_unverified_blocked.py tests/e2e/test_password_policy_e2e.py tests/e2e/test_onboarding.py tests/e2e/test_invite_onboarding.py --no-cov 2>&1 | tee tmp/task10_e2e.log | tail -15
```
Expected: all green. On failure, `grep -nE "FAILED|Error" tmp/task10_e2e.log`.

- [ ] **Step 11: Commit**

```bash
git add backend/cubeplex/i18n/ backend/tests/e2e/test_register_otp_flow.py backend/tests/e2e/test_register_smtp_disabled.py backend/tests/e2e/test_login_unverified_blocked.py backend/tests/e2e/test_password_policy_e2e.py backend/tests/e2e/test_onboarding.py backend/tests/e2e/test_invite_onboarding.py <updated legacy tests>
git commit -m "test(auth): e2e for OTP/password/onboarding/invite registration flows"
```

---

### Task 11: Frontend `@cubeplex/core` API/types + password policy mirror

**Files:**
- Modify: `frontend/packages/core/src/api/auth.ts` (`RegisterResult.verification_required`; `MeResult.needs_onboarding` replaces `needs_org_setup`; add `verifyOtp`/`resendOtp`; remove `verifyEmail`/`requestVerifyToken`)
- Modify: `frontend/packages/core/src/api/system.ts` (remove `postSetup`/`SetupRequest`/`SetupResponse`; drop `needs_org_setup`)
- Modify: `frontend/packages/core/src/hooks/useDeploymentMode.ts` (`needsOnboarding` replaces `needsOrgSetup`)
- Create: `frontend/packages/core/src/api/onboarding.ts`
- Create: `frontend/packages/core/src/api/orgInvites.ts`
- Create: `frontend/packages/core/src/auth/passwordPolicy.ts`

**Interfaces:**
- Produces (consumed by Tasks 12–15):
  - `RegisterResult { id, email, default_workspace_id, verification_required: boolean }`
  - `MeResult.needs_onboarding?: boolean` (drop `needs_org_setup`)
  - `verifyOtp(client, email, code): Promise<{ok: true} | never>` (throws `ApiError` on 400 with `code`)
  - `resendOtp(client, email): Promise<{ok: boolean}>`
  - `completeOnboarding(client, body: {org_name?, org_slug?, workspace_name}): Promise<{workspace_id: string}>`
  - `acceptOrgInvite(client, token): Promise<{org_id, role}>`
  - `createOrgInvite(client, role): Promise<{token, expires_at, role}>`
  - `validatePassword(password, policy: 'low'|'high'): {ok: boolean, errors: string[]}` (mirror of backend; `getPasswordPolicy()` not needed client-side — the policy is communicated via the `weak_password` 400 body).

- [ ] **Step 1: Update `auth.ts`**

In `frontend/packages/core/src/api/auth.ts`:
- Add `verification_required: boolean` to `RegisterResult`.
- In `MeResult`, replace `needs_org_setup?: boolean` with `needs_onboarding?: boolean`.
- Delete the `verifyEmail` function.
- Add:

```ts
export async function verifyOtp(
  client: ApiClient,
  email: string,
  code: string,
): Promise<{ ok: true }> {
  const res = await client.post('/api/v1/auth/verify-otp', { email, code })
  if (!res.ok) throw await toApiError(res)
  return { ok: true }
}

export async function resendOtp(client: ApiClient, email: string): Promise<{ ok: boolean }> {
  const res = await client.post('/api/v1/auth/resend-otp', { email })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { ok: boolean }
}
```

(Check whether `requestVerifyToken` exists in `auth.ts` — `grep -n "requestVerifyToken" frontend/packages/core/src/api/auth.ts`. The spec lists it for removal; if present, delete it.)

- [ ] **Step 2: Update `system.ts`**

In `frontend/packages/core/src/api/system.ts`: delete `SetupRequest`, `SetupResponse`, and `postSetup`. Remove `needs_org_setup` from `SystemInfoResponse`.

- [ ] **Step 3: Update `useDeploymentMode.ts`**

In `frontend/packages/core/src/hooks/useDeploymentMode.ts`: replace `needsOrgSetup: data?.needs_org_setup ?? false` with `needsOnboarding: data?.needs_onboarding ?? false`, and rename the returned field consistently (check call sites: `grep -rn "needsOrgSetup" frontend/` and update them — the `(app)/layout.tsx` is updated in Task 14; the `setup/page.tsx` is deleted in Task 14).

- [ ] **Step 4: Create `onboarding.ts`**

`frontend/packages/core/src/api/onboarding.ts`:

```ts
import { toApiError, type ApiClient } from './client'

export interface OnboardingRequest {
  org_name?: string
  org_slug?: string
  workspace_name: string
}

export interface OnboardingResponse {
  workspace_id: string
}

export async function completeOnboarding(
  client: ApiClient,
  body: OnboardingRequest,
): Promise<OnboardingResponse> {
  const res = await client.post('/api/v1/onboarding', body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as OnboardingResponse
}
```

- [ ] **Step 5: Create `orgInvites.ts`**

`frontend/packages/core/src/api/orgInvites.ts`:

```ts
import { toApiError, type ApiClient } from './client'

export interface OrgInviteOut {
  token: string
  expires_at: string
  role: string
}

export interface AcceptOrgInviteResult {
  org_id: string
  role: string
}

export async function createOrgInvite(
  client: ApiClient,
  role: 'admin' | 'member',
): Promise<OrgInviteOut> {
  const res = await client.post('/api/v1/admin/orgs/invites', { role })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as OrgInviteOut
}

export async function acceptOrgInvite(
  client: ApiClient,
  token: string,
): Promise<AcceptOrgInviteResult> {
  const res = await client.post('/api/v1/orgs/invites/accept', { token })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as AcceptOrgInviteResult
}
```

- [ ] **Step 6: Create `passwordPolicy.ts`**

`frontend/packages/core/src/auth/passwordPolicy.ts` (mirror of backend; UX only):

```ts
export type PasswordPolicy = 'low' | 'high'

export interface PasswordValidationResult {
  ok: boolean
  errors: string[]
}

function isSymbol(ch: string): boolean {
  const code = ch.charCodeAt(0)
  return code >= 33 && code <= 126 && !/[a-z0-9]/i.test(ch)
}

export function validatePassword(
  password: string,
  policy: PasswordPolicy,
): PasswordValidationResult {
  const errors: string[] = []
  const minLen = policy === 'high' ? 10 : 8
  if (password.length < minLen) errors.push('password_too_short')
  if (policy === 'high') {
    if (!/[A-Z]/.test(password)) errors.push('password_no_uppercase')
    if (!/[a-z]/.test(password)) errors.push('password_no_lowercase')
    if (!/[0-9]/.test(password)) errors.push('password_no_digit')
    if (![...password].some(isSymbol)) errors.push('password_no_symbol')
  }
  return { ok: errors.length === 0, errors }
}
```

- [ ] **Step 7: Build core + typecheck**

```bash
cd frontend && pnpm --filter @cubeplex/core build 2>&1 | tee tmp/core_build.log | tail -5
cd frontend && pnpm --filter @cubeplex/core typecheck 2>&1 | tee tmp/core_typecheck.log | tail -5
```
Expected: build + typecheck clean. (If `typecheck` script doesn't exist, run `pnpm --filter @cubeplex/core exec tsc --noEmit`.)

- [ ] **Step 8: Commit**

```bash
git add frontend/packages/core/src/
git commit -m "feat(core): OTP/onboarding/orgInvite APIs + needs_onboarding + passwordPolicy mirror"
```

---

### Task 12: `<OtpInput>` + `/verify-otp` page

**Files:**
- Create: `frontend/packages/web/components/auth/OtpInput.tsx`
- Create: `frontend/packages/web/app/(auth)/verify-otp/page.tsx`

**Interfaces:**
- Consumes: `verifyOtp`, `resendOtp` (Task 11); `useTranslations('auth')`; `useSearchParams` for `email` + `next`.
- Produces: a `/verify-otp?email=&next=` page that takes a 6-digit code, submits, and on success auto-logs-in (`loginUser`) then routes by `next`/`needs_onboarding`/`/w/{id}`. Resend button with a countdown disabled state (client state machine — allowed).

- [ ] **Step 1: Create `<OtpInput>`**

`frontend/packages/web/components/auth/OtpInput.tsx` — a 6-cell input with:
- Controlled `value: string` (6 digits) + `onChange`.
- Per-cell inputs; arrow/backspace navigation; paste support (split pasted digits across cells).
- Props: `length?: number` (default 6), `value: string`, `onChange: (v: string) => void`, `disabled?: boolean`.
- Use `'use client'`. Match the styling of existing auth inputs (`rounded-md border border-border bg-background px-3 py-2 text-sm`).

(Keep it focused: no countdown here — the page owns the resend countdown. The component is just the cell state machine.)

- [ ] **Step 2: Create the `/verify-otp` page**

`frontend/packages/web/app/(auth)/verify-otp/page.tsx`:

```tsx
'use client'

import { useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { createApiClient, loginUser, resendOtp, useAuthStore, verifyOtp } from '@cubeplex/core'
import { OtpInput } from '@/components/auth/OtpInput'

const RESEND_COOLDOWN = 60

export default function VerifyOtpPage() {
  const t = useTranslations('auth')
  const router = useRouter()
  const params = useSearchParams()
  const email = params.get('email') ?? ''
  const next = params.get('next') ?? '/'
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [cooldown, setCooldown] = useState(0)

  useEffect(() => {
    if (cooldown <= 0) return
    const id = setInterval(() => setCooldown((c) => Math.max(0, c - 1)), 1000)
    return () => clearInterval(id)
  }, [cooldown])

  const routeAfterLogin = async () => {
    const client = createApiClient('')
    await useAuthStore.getState().loadMe(client)
    const me = useAuthStore.getState().user
    const safeNext = next.startsWith('/') && !next.startsWith('//') ? next : '/'
    if (me?.needs_onboarding && !safeNext.startsWith('/orgs/invites/accept') && !safeNext.startsWith('/invite')) {
      router.push('/onboarding')
    } else {
      router.push(safeNext)
    }
  }

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (code.length !== 6 || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const client = createApiClient('')
      await verifyOtp(client, email, code)
      await loginUser(client, email, /* password unknown */ '')
      // NOTE: loginUser needs the password — but the verify-otp page does NOT have it.
      // See Step 3 note: the backend must set the cookie on /verify-otp success, OR
      // the page re-derives login differently. Resolve per Step 3.
      await routeAfterLogin()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  const onResend = async () => {
    if (cooldown > 0) return
    setError(null)
    try {
      await resendOtp(createApiClient(''), email)
      setCooldown(RESEND_COOLDOWN)
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <div className="text-center mb-6">
        <h1 className="text-xl font-semibold">{t('verifyOtpTitle')}</h1>
        <p className="text-sm text-foreground/60 mt-1">{t('verifyOtpSubtitle', { email })}</p>
      </div>
      <OtpInput value={code} onChange={setCode} disabled={submitting} />
      {error && <div className="text-sm text-destructive">{error}</div>}
      <button
        type="submit"
        disabled={code.length !== 6 || submitting}
        className="w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
      >
        {submitting ? t('verifying') : t('verify')}
      </button>
      <button
        type="button"
        onClick={onResend}
        disabled={cooldown > 0}
        className="w-full text-xs text-muted-foreground underline disabled:opacity-50"
      >
        {cooldown > 0 ? t('resendIn', { seconds: cooldown }) : t('resendCode')}
      </button>
    </form>
  )
}
```

- [ ] **Step 3: Resolve the cookie-on-verify problem**

The `/verify-otp` page does not have the user's password, so it cannot call `/login`. Two options — pick **A** (recommended):

**Option A (backend sets the cookie on verify):** Change `POST /auth/verify-otp` to, on success, also issue the auth cookie (call the fastapi-users login strategy with the user). Then the frontend `verifyOtp` call itself establishes the session, and the page skips `loginUser`. Update Task 5 Step 6's `verify_otp_endpoint` to, on `result.ok`, load the user by email and call `strategy.write_token(user)` + set the cookie (mirror what `/login` does). This is the cleanest: the verify step **is** the login. Update the `/verify-otp` handler in Task 5 accordingly (add `user_manager` + `strategy` deps; on success, set cookie via the same response mechanism `/login` uses — read the `/login` cookie-setting code in `auth.py` ~lines 130-180 and mirror it). Adjust this task's page to **not** call `loginUser` (remove the `loginUser(...)` line; `verifyOtp` already set the cookie).

**Option B:** Frontend stores the password in memory during register and threads it through to `/verify-otp`. Rejected — never hold the password across pages.

Apply Option A: edit Task 5's `verify_otp_endpoint` to set the cookie on success, and remove the `loginUser` call from this page's `onSubmit`.

- [ ] **Step 4: i18n keys**

Add to `frontend/packages/web/messages/en.json` and `zh.json` under `auth`: `verifyOtpTitle`, `verifyOtpSubtitle` (with `{email}`), `verifying`, `verify`, `resendCode`, `resendIn` (with `{seconds}`). (Consolidated in Task 16, but add now so the page compiles.)

- [ ] **Step 5: Lint + typecheck**

```bash
cd frontend && pnpm --filter @cubeplex/web lint 2>&1 | tee tmp/verify_otp_lint.log | tail -5
cd frontend && pnpm --filter @cubeplex/web typecheck 2>&1 | tee tmp/verify_otp_tc.log | tail -5
```
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/components/auth/OtpInput.tsx frontend/packages/web/app/\(auth\)/verify-otp/ frontend/packages/web/messages/
git commit -m "feat(web): OtpInput component + /verify-otp page"
```

---

### Task 13: `RegisterForm` next-threading + `LoginForm` `email_not_verified` + `VerificationBanner`

**Files:**
- Modify: `frontend/packages/web/components/auth/RegisterForm.tsx`
- Modify: `frontend/packages/web/components/auth/LoginForm.tsx`
- Modify: `frontend/packages/web/components/layout/VerificationBanner.tsx`

**Interfaces:**
- Consumes: `RegisterResult.verification_required`, `validatePassword` (Task 11), `MeResult.needs_onboarding`.
- Produces:
  - `RegisterForm`: pre-validates password via `validatePassword(password, 'high')`; after register, routes: `verification_required` → `/verify-otp?email=&next=`; else invite-accept `next` → there; else `needs_onboarding` → `/onboarding`; else `/w/{default_workspace_id}`. Threads `next` everywhere (fixes the dropped-`next` bug).
  - `LoginForm`: 403 `email_not_verified` → renders a notice + link to `/verify-otp?email=<email>`.
  - `VerificationBanner`: resend button calls `resendOtp` (was magic-link).

- [ ] **Step 1: Rewrite `RegisterForm` routing + password pre-validation**

In `frontend/packages/web/components/auth/RegisterForm.tsx`, replace the `onSubmit` body's post-register block with:

```tsx
  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const client = createApiClient('')
      const result = await registerUser(client, email, password, displayName || undefined)
      const safeNext = nextPath.startsWith('/') && !nextPath.startsWith('//') ? nextPath : '/'

      if (result.verification_required) {
        router.push(
          `/verify-otp?email=${encodeURIComponent(email)}&next=${encodeURIComponent(safeNext)}`,
        )
        return
      }

      // Verification off: register set is_verified=true. Establish session + route.
      await loginUser(client, email, password)
      await useAuthStore.getState().loadMe(client)
      const me = useAuthStore.getState().user
      if (isInviteAcceptPath(safeNext)) {
        router.push(safeNext)
      } else if (me?.needs_onboarding) {
        router.push('/onboarding')
      } else {
        router.push(result.default_workspace_id ? `/w/${result.default_workspace_id}` : '/onboarding')
      }
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }
```

Add a helper (module-level in `RegisterForm.tsx` or a shared `lib/invitePath.ts` — put it in `lib/invitePath.ts` and import in both forms):

```ts
// frontend/packages/web/lib/invitePath.ts
export function isInviteAcceptPath(path: string): boolean {
  return path.startsWith('/invite') || path.startsWith('/orgs/invites/accept')
}
```

Add password pre-validation UX: before calling `registerUser`, compute `validatePassword(password, 'high')` and if `!ok`, set a translated error and abort. (Backend is authoritative; this is just UX.) Import `validatePassword` from `@cubeplex/core/auth/passwordPolicy`.

- [ ] **Step 2: `LoginForm` `email_not_verified` handling**

In `frontend/packages/web/components/auth/LoginForm.tsx`, mirror the `extractSsoRequired` pattern: add `extractEmailNotVerified(err)` returning `{message}` when `err.status === 403 && err.code === 'email_not_verified'`. In `onSubmit`'s catch, before the generic `setError`, check it and render a notice with a link to `/verify-otp?email=<email>&next=<nextPath>`. Add `emailNotVerified` state alongside `ssoRequired`.

- [ ] **Step 3: `VerificationBanner` resend → `/resend-otp`**

`grep -rn "resend\|requestVerify\|verifyEmail" frontend/packages/web/components/layout/VerificationBanner.tsx`. Replace the resend action to call `resendOtp(client, user.email)` and show a countdown. Remove any magic-link `/verify-email` link; the banner's CTA becomes "Enter verification code" → `/verify-otp?email=<email>`.

- [ ] **Step 4: Lint + typecheck**

```bash
cd frontend && pnpm --filter @cubeplex/web lint 2>&1 | tee tmp/task13_lint.log | tail -5
cd frontend && pnpm --filter @cubeplex/web typecheck 2>&1 | tee tmp/task13_tc.log | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/components/auth/RegisterForm.tsx frontend/packages/web/components/auth/LoginForm.tsx frontend/packages/web/components/layout/VerificationBanner.tsx frontend/packages/web/lib/invitePath.ts
git commit -m "feat(web): thread next through register; login email_not_verified; banner resend via OTP"
```

---

### Task 14: `<OnboardingForm>` + `/onboarding` page + `(app)/layout` guard + delete `/setup`

**Files:**
- Create: `frontend/packages/web/components/onboarding/OnboardingForm.tsx`
- Create: `frontend/packages/web/app/(setup)/onboarding/page.tsx`
- Modify: `frontend/packages/web/app/(app)/layout.tsx` (`needs_onboarding` → `/onboarding` guard)
- Delete: `frontend/packages/web/app/(setup)/setup/`, `frontend/packages/web/components/setup/SetupForm.tsx`

**Interfaces:**
- Consumes: `completeOnboarding` (Task 11), `useAuthStore` (for `me` to pick full vs workspace-only mode), `suggestSlug`/`validateSlug`/`slugErrorMessage` from `@/lib/slugRules`.
- Produces: a `/onboarding` page that renders full fields (org name + slug + workspace name) when `me` has no org membership, else workspace-only (workspace name only). On success → `router.replace('/w/{workspace_id}')`.

- [ ] **Step 1: Create `<OnboardingForm>`**

`frontend/packages/web/components/onboarding/OnboardingForm.tsx` — base it on `SetupForm.tsx` (Task-read earlier). It:
- Reads `me` from `useAuthStore` to decide mode: `const fullMode = !(me?.org_memberships?.length)` → show org name + slug fields; else workspace-only.
- State: `orgName`, `slug` (auto-suggested from orgName in full mode, editable, real-time `validateSlug`), `workspaceName`.
- On submit: `completeOnboarding(client, { org_name, org_slug, workspace_name })` (omit org fields in workspace-only mode). On 409 `slug_taken` → show slug error; on `onboarding_not_required` → `router.replace('/')`.
- On success → `router.replace('/w/${result.workspace_id}')`.

- [ ] **Step 2: Create the `/onboarding` page**

`frontend/packages/web/app/(setup)/onboarding/page.tsx`:

```tsx
'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useAuthStore } from '@cubeplex/core'
import { OnboardingForm } from '@/components/onboarding/OnboardingForm'

export default function OnboardingPage() {
  const router = useRouter()
  const user = useAuthStore((s) => s.user)
  useEffect(() => {
    if (user && !user.needs_onboarding) router.replace('/')
  }, [user, router])
  if (!user?.needs_onboarding) return null
  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <OnboardingForm />
    </div>
  )
}
```

- [ ] **Step 3: Update `(app)/layout.tsx` guard**

In `frontend/packages/web/app/(app)/layout.tsx`, replace the `needsOrgSetup` logic:

```tsx
  const needsOnboarding = useAuthStore((s) => s.user?.needs_onboarding)
  useEffect(() => {
    if (needsOnboarding) router.replace('/onboarding')
  }, [needsOnboarding, router])
```

(Remove the `/setup` reference. Keep the rest of the layout unchanged.)

- [ ] **Step 4: Delete `/setup`**

```bash
git rm -r frontend/packages/web/app/\(setup\)/setup frontend/packages/web/components/setup/SetupForm.tsx
```
(Check for other references: `grep -rn "postSetup\|SetupForm\|/setup" frontend/packages/web/` and remove any imports/nav links.)

- [ ] **Step 5: Lint + typecheck + build**

```bash
cd frontend && pnpm --filter @cubeplex/web lint 2>&1 | tee tmp/task14_lint.log | tail -5
cd frontend && pnpm --filter @cubeplex/web typecheck 2>&1 | tee tmp/task14_tc.log | tail -5
cd frontend && pnpm --filter @cubeplex/web build 2>&1 | tee tmp/task14_build.log | tail -5
```

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/components/onboarding/ frontend/packages/web/app/\(setup\)/onboarding/ frontend/packages/web/app/\(app\)/layout.tsx
git rm -r frontend/packages/web/app/\(setup\)/setup frontend/packages/web/components/setup/SetupForm.tsx
git commit -m "feat(web): /onboarding wizard (full + workspace-only); replace /setup"
```

---

### Task 15: Org-invite accept page

**Files:**
- Create: `frontend/packages/web/app/(auth)/orgs/invites/accept/page.tsx`

**Interfaces:**
- Consumes: `acceptOrgInvite` (Task 11), `useAuthStore`.
- Produces: a `/orgs/invites/accept?token=` page (auth required — the `(auth)` layout redirects unauthenticated users to `/login?next=/orgs/invites/accept?token=...`). On load: call `acceptOrgInvite(client, token)`; on success → `loadMe` → if `needs_onboarding` → `/onboarding`, else `/`. On 400 `invite_invalid_or_expired` → show error.

- [ ] **Step 1: Create the page**

`frontend/packages/web/app/(auth)/orgs/invites/accept/page.tsx`:

```tsx
'use client'

import { useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { acceptOrgInvite, createApiClient, useAuthStore } from '@cubeplex/core'

export default function AcceptOrgInvitePage() {
  const t = useTranslations('auth')
  const router = useRouter()
  const params = useSearchParams()
  const token = params.get('token') ?? ''
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!token) {
      setError('invite_invalid_or_expired')
      return
    }
    (async () => {
      try {
        const client = createApiClient('')
        await acceptOrgInvite(client, token)
        await useAuthStore.getState().loadMe(client)
        const me = useAuthStore.getState().user
        router.replace(me?.needs_onboarding ? '/onboarding' : '/')
      } catch (err) {
        setError((err as Error).message)
      }
    })()
  }, [token, router])

  if (error) {
    return <div className="p-8 text-center text-sm text-destructive">{t('inviteInvalid')}</div>
  }
  return <div className="p-8 text-center text-sm text-foreground/60">{t('joiningOrg')}</div>
}
```

- [ ] **Step 2: Verify the `(auth)` layout's auth redirect carries `next`**

`grep -rn "next\|redirect\|/login" frontend/packages/web/app/\(auth\)/layout.tsx`. Confirm an unauthenticated visit to `/orgs/invites/accept?token=...` redirects to `/login?next=/orgs/invites/accept?token=...` so the user logs in (or registers) and is returned. If the layout strips the query string, fix it to preserve `?token=` in the `next` value (URL-encode the full path). This is what makes "register via org-invite link" work end-to-end.

- [ ] **Step 3: Lint + typecheck**

```bash
cd frontend && pnpm --filter @cubeplex/web lint 2>&1 | tee tmp/task15_lint.log | tail -5
cd frontend && pnpm --filter @cubeplex/web typecheck 2>&1 | tee tmp/task15_tc.log | tail -5
```

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/app/\(auth\)/orgs/
git commit -m "feat(web): /orgs/invites/accept page"
```

---

### Task 16: Frontend i18n + Playwright e2e + docs

**Files:**
- Modify: `frontend/packages/web/messages/en.json`, `frontend/packages/web/messages/zh.json`
- Create/Modify: `frontend/packages/web/__tests__/e2e/` Playwright specs (registration OTP flow, verification-off flow, workspace-invite flow, unverified-login notice, OtpInput paste/countdown)
- Modify: `docs/site/docs/` — the auth/registration page (find via the docs-overhaul plan mapping)
- Delete: any Playwright spec that tested the magic-link `/verify-email` flow or the `/setup` page

**Interfaces:**
- Consumes: Tasks 11–15.

- [ ] **Step 1: Consolidate frontend i18n keys**

Add to `en.json`/`zh.json` `auth`: `verifyOtpTitle`, `verifyOtpSubtitle` (`{email}`), `verifying`, `verify`, `resendCode`, `resendIn` (`{seconds}`), `emailNotVerified`, `emailNotVerifiedLink`, `inviteInvalid`, `joiningOrg`, and onboarding keys (`onboardingTitle`, `orgName`, `orgSlug`, `workspaceName`, `createWorkspace`, `createOrgAndWorkspace`). Translate both languages.

- [ ] **Step 2: Playwright e2e — full OTP + onboarding flow**

`frontend/packages/web/__tests__/e2e/registration-otp.spec.ts`:
- Register (strong password) → land on `/verify-otp` → scrape the OTP code from the backend log backend output (the test reads the OTP the way the existing suite reads emailed codes — check `grep -rn "otp\|verification\|email" frontend/packages/web/__tests__/e2e/` for an existing pattern; if none, read the code from the backend test log or expose it via a dev-only endpoint under test) → enter code → verify → land on `/onboarding` → fill org+slug+workspace → land on `/w/{id}` (assert the chat input is visible — the real invariant, not a heading count).
- Verification-off variant (override config to disable): register → land directly on `/onboarding`.

- [ ] **Step 3: Playwright e2e — workspace-invite flow**

`registration-invite.spec.ts`: admin creates a workspace invite; a new user registers via the invite `next` link → verifies OTP → invite accepted → lands directly in `/w/{id}` (no onboarding).

- [ ] **Step 4: Playwright e2e — unverified-login notice + OtpInput state machine**

`unverified-login.spec.ts`: register (don't verify) → go to `/login` → submit → assert the "email not verified" notice + link to `/verify-otp` is shown. `otp-input.spec.ts`: paste a 6-digit string into `<OtpInput>` → all cells fill; resend button disabled during countdown (client state machine — allowed).

- [ ] **Step 5: Delete obsolete Playwright specs**

`git rm` any spec that tested `/verify-email` (magic-link) or `/setup`. `grep -rln "verify-email\|/setup" frontend/packages/web/__tests__/e2e/`.

- [ ] **Step 6: Update docs**

Find the auth/registration doc page via the docs-overhaul mapping: `grep -rln "register\|verify-email\|/setup\|magic-link\|needs_org_setup" docs/site/docs/`. Update it to describe: OTP verification gate (when SMTP enabled), configurable password policy (high/low), the onboarding wizard (full + workspace-only + skip), org-invite links, and the removal of magic-link + `/setup`. Add screenshot placeholders per the CLAUDE.md convention for the OTP page + onboarding wizard (capture not required now — leave the `:::info 📸 Screenshot placeholder` block).

- [ ] **Step 7: Run the Playwright suite + lint/build**

```bash
cd frontend && pnpm --filter @cubeplex/web lint 2>&1 | tee tmp/task16_lint.log | tail -5
cd frontend && pnpm --filter @cubeplex/web build 2>&1 | tee tmp/task16_build.log | tail -5
cd frontend && pnpm --filter @cubeplex/web exec playwright test registration-otp registration-invite unverified-login otp-input 2>&1 | tee tmp/task16_pw.log | tail -15
```
Expected: green. (Backend must be running on :8001 with the test config; `.worktree.env` sets ports.)

- [ ] **Step 8: Pre-PR full sweep**

```bash
cd backend && uv run pytest --no-cov 2>&1 | tee tmp/full_backend.log | tail -10
cd frontend && pnpm --filter @cubeplex/web build 2>&1 | tee tmp/full_build.log | tail -5
```

- [ ] **Step 9: Commit**

```bash
git add frontend/packages/web/messages/ frontend/packages/web/__tests__/e2e/ docs/site/docs/
git rm <obsolete specs>
git commit -m "test(web)+docs: registration OTP/onboarding/invite Playwright specs + i18n + docs"
```

---

## Self-Review

### 1. Spec coverage

- **§1 Config** → Task 1. ✓
- **§2 Password policy** (pure module, register/change/reset integration, TS mirror) → Task 2 (module), Task 3 (register + change-password wiring), Task 11 (TS mirror). Reset-password routes through the overridden `validate_password` automatically (fastapi-users reset router calls `user_manager.validate_password`) — covered by Task 3's override; an explicit reset e2e is optional but the override is the mechanism. ✓ (note: reset-password e2e not explicitly listed — the override covers it; add a line in Task 10 if desired.)
- **§3 OTP** (Redis storage, `email_otp.py`, register/verify-otp/resend-otp endpoints, login `email_not_verified`, remove magic-link, template, `is_verified` reuse) → Task 4 (service), Task 5 (endpoints + login gate + magic-link removal + template). ✓
- **§4 Onboarding + invites** (three modes, `on_after_register` refactor, `needs_onboarding`, `POST /onboarding`, org-scoped invite model + endpoints, invite-routing priority, retire `/system/setup`, frontend onboarding page + RegisterForm routing + layout guard + org-invite accept page) → Task 6 (model), Task 7 (org-invite endpoints), Task 8 (bootstrap refactor), Task 9 (onboarding router + retire setup + needs_onboarding), Task 14 (onboarding page + layout + delete setup), Task 15 (org-invite accept page). ✓
- **§5 Frontend flow** (next threading, OTP page, login notice, password pre-validation, removed magic-link UI) → Tasks 11–15. ✓
- **§6 Error/security** (OTP security, password no-echo, onboarding atomicity, invite single-use + role limit) → encoded in Tasks 4, 5, 7, 9. ✓
- **§7 Testing** (backend e2e, backend unit, frontend e2e, config/migration) → Tasks 2, 4 (unit), 10 (backend e2e + migration), 16 (frontend e2e). ✓

**Gap:** The cookie-on-verify decision (Task 12 Step 3, Option A) requires editing Task 5's `verify_otp_endpoint` to set the cookie on success. That cross-task edit is called out in Task 12 Step 3 and must be applied when implementing Task 5/12. Implementer: apply Option A during Task 5 (add `user_manager` + `strategy` deps to `/verify-otp`, set cookie on `result.ok`), so Task 12's page omits `loginUser`.

### 2. Placeholder scan

No "TBD"/"TODO"/"implement later". Where the plan says "confirm with grep" or "check whether X exists", that is a verification step for the implementer against the live codebase, not a placeholder for unwritten plan content — the code blocks are complete. The Task 10 e2e steps reference fixture names to confirm via grep before authoring; this is intentional (fixture names can drift) and the test bodies are otherwise fully specified by the invariant each must protect.

### 3. Type consistency

- `verify_otp` → `VerifyResult{ok, reason, remaining_attempts}` (Task 4) ↔ consumed in Task 5's `verify_otp_endpoint` mapping `reason` → API codes. ✓
- `_bootstrap_org_and_workspace` / `_bootstrap_workspace_in_org` (Task 8) ↔ called in Task 9 onboarding handler. ✓
- `OrgInviteTokenRepository.issue/consume` (Task 6) ↔ used in Task 7. ✓
- Frontend `RegisterResult.verification_required`, `MeResult.needs_onboarding`, `verifyOtp`/`resendOtp`/`completeOnboarding`/`acceptOrgInvite` (Task 11) ↔ used in Tasks 12–15. ✓
- `isInviteAcceptPath` (Task 13) used in RegisterForm + (app) layout routing. ✓
- Config keys (`auth.password_policy`, `auth.email_verification.*`) defined in Task 1, read in Tasks 2, 4, 5. ✓
- `needs_org_setup` fully replaced by `needs_onboarding` across backend (`_me_payload`, `SystemInfoResponse`) and frontend (`MeResult`, `useDeploymentMode`, layout, deleted setup page). ✓

