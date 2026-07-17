# Sandbox Env Vault (Backend Foundation) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the backend foundation for managing per-(org/workspace/user) sandbox environment variables — secret and plain — that later plans inject into sandboxes and swap at the egress boundary.

**Architecture:** A new `SandboxEnvVar` table (its own nullable scope FKs, mirroring `MCPCredentialGrant`) holds one entry per env var per scope. Secret values live in the existing credential vault (`CredentialService`, new kind `sandbox_env`); plain values live inline. A resolver applies user > workspace > org precedence to produce the effective env set for a sandbox. Management is exposed through scope-isolated admin (org-scope) and workspace (workspace/user-scope) routes.

**Tech Stack:** Python 3.13, FastAPI, SQLModel, Alembic, Postgres, pytest. Host-pattern validation uses `tldextract` (Public Suffix List).

**Scope:** This is Plan 1 of 3. Plan 2 = run-start injection + exchange endpoint; Plan 3 = K8s egress bundle (webhook + addon). This plan ships and is fully testable on its own (bare `python main.py`, no Kubernetes).

**Spec:** `docs/dev/specs/2026-05-25-egress-key-injection-design.md` (§6.4, §6.5 resolution, §7 host rules).

---

## File Structure

- Create `cubeplex/models/sandbox_env.py` — `SandboxEnvVar` model + `__table_args__` (CHECK + partial unique indexes).
- Modify `cubeplex/models/__init__.py` — export `SandboxEnvVar`.
- Create `cubeplex/sandbox_env/__init__.py` — package marker.
- Create `cubeplex/sandbox_env/host_rules.py` — host-pattern validation (eTLD+1 boundary, anchored regex) + runtime host matching.
- Create `cubeplex/repositories/sandbox_env.py` — `SandboxEnvRepository` (org-scoped, nullable workspace/user).
- Create `cubeplex/services/sandbox_env.py` — `SandboxEnvService` (CRUD + validation, uses `CredentialService`) and `SandboxEnvResolver` (precedence).
- Create `cubeplex/api/schemas/sandbox_env.py` — request/response models.
- Create `cubeplex/api/routes/v1/admin_sandbox_env.py` — org-scope routes (`/admin/sandbox-env`).
- Create `cubeplex/api/routes/v1/ws_sandbox_env.py` — workspace/user-scope routes (`/ws/{workspace_id}/sandbox-env`).
- Modify `cubeplex/api/app.py` — register both routers.
- Tests: `tests/unit/test_sandbox_env_host_rules.py`, `tests/unit/test_sandbox_env_model.py`, `tests/unit/test_sandbox_env_service.py`, `tests/unit/test_sandbox_env_resolver.py`, `tests/e2e/test_sandbox_env_routes.py`.

---

## Task 1: Add `tldextract` dependency

**Files:**
- Modify: `pyproject.toml` (via `uv add`)

- [ ] **Step 1: Add the dependency**

Run: `uv add tldextract`
Expected: `pyproject.toml` gains `tldextract>=5` under dependencies; `uv.lock` updated.

- [ ] **Step 2: Verify import works**

Run: `uv run python -c "import tldextract; print(tldextract.extract('api.github.com').registered_domain)"`
Expected: prints `github.com`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add tldextract for sandbox env host validation"
```

---

## Task 2: Host-pattern validation rules

The `hosts` field accepts exact FQDNs, wildcards (`*.example.com`), and anchored regexes (`/^.../$/`). Validation: a pattern must resolve within a single registrable domain (eTLD+1); regexes must be anchored. Also provide a runtime matcher used later by the exchange (Plan 2) and addon (Plan 3).

**Files:**
- Create: `cubeplex/sandbox_env/__init__.py`
- Create: `cubeplex/sandbox_env/host_rules.py`
- Test: `tests/unit/test_sandbox_env_host_rules.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_sandbox_env_host_rules.py
import pytest

from cubeplex.sandbox_env.host_rules import (
    HostPatternError,
    host_matches,
    validate_host_pattern,
)


@pytest.mark.parametrize(
    "pattern",
    ["api.github.com", "*.example.com", r"/^api[0-9]+\.foo\.com$/"],
)
def test_valid_patterns_accepted(pattern):
    validate_host_pattern(pattern)  # no raise


@pytest.mark.parametrize(
    "pattern",
    [
        "*.com",
        "*.co.uk",
        "*",
        r"/github\.com/",
        r"/^.*$/",
        r"/^(api\.github\.com|api\.attacker\.net)$/",  # alternation spans two domains
        "not a host",
        "",
    ],
)
def test_invalid_patterns_rejected(pattern):
    with pytest.raises(HostPatternError):
        validate_host_pattern(pattern)


def test_regex_only_list_rejected():
    from cubeplex.sandbox_env.host_rules import validate_hosts

    with pytest.raises(HostPatternError):
        validate_hosts([r"/^api[0-9]+\.foo\.com$/"])  # no FQDN/wildcard companion
    validate_hosts([r"/^api[0-9]+\.foo\.com$/", "api.foo.com"])  # ok with companion


def test_exact_match():
    assert host_matches("api.github.com", ["api.github.com"])
    assert not host_matches("evil.com", ["api.github.com"])


def test_wildcard_matches_subdomain_only():
    assert host_matches("api.example.com", ["*.example.com"])
    assert not host_matches("example.com", ["*.example.com"])
    assert not host_matches("api.evil.com", ["*.example.com"])


def test_anchored_regex_does_not_overmatch():
    pats = [r"/^api[0-9]+\.foo\.com$/"]
    assert host_matches("api1.foo.com", pats)
    assert not host_matches("api1.foo.com.attacker.net", pats)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_sandbox_env_host_rules.py -v`
Expected: FAIL with `ModuleNotFoundError: cubeplex.sandbox_env.host_rules`

- [ ] **Step 3: Implement**

```python
# cubeplex/sandbox_env/__init__.py
"""Sandbox env vault: host rules, validation, resolution helpers."""
```

```python
# cubeplex/sandbox_env/host_rules.py
"""Validation + matching for env-vault host patterns.

A pattern is one of:
  - exact FQDN:   "api.github.com"
  - wildcard:     "*.example.com"  (matches subdomains of, not, example.com)
  - regex:        "/^...$/"        (must be fully anchored)

Every pattern must resolve within a single registrable domain (eTLD+1), so a
secret can never be widened to more than one registrable domain. See spec §7.
"""

from __future__ import annotations

import re

import tldextract

_extract = tldextract.TLDExtract(suffix_list_urls=())  # offline; bundled snapshot


class HostPatternError(ValueError):
    """Raised when a host pattern is malformed or too broad."""


def _registrable(host: str) -> str | None:
    ext = _extract(host)
    if not ext.domain or not ext.suffix:
        return None
    return f"{ext.domain}.{ext.suffix}"


def _is_regex(pattern: str) -> bool:
    return len(pattern) >= 2 and pattern.startswith("/") and pattern.endswith("/")


def validate_host_pattern(pattern: str) -> None:
    """Raise HostPatternError unless the pattern is well-formed and within one eTLD+1."""
    if not pattern or pattern == "*":
        raise HostPatternError(f"empty or too-broad pattern: {pattern!r}")

    if _is_regex(pattern):
        body = pattern[1:-1]
        if not (body.startswith("^") and body.endswith("$")):
            raise HostPatternError(f"regex must be anchored with ^...$: {pattern!r}")
        try:
            re.compile(body)
        except re.error as exc:
            raise HostPatternError(f"invalid regex {pattern!r}: {exc}") from exc
        core = body[1:-1]  # strip ^ and $
        # No alternation: a top-level (or any) `|` lets the regex match more than
        # one registrable domain (e.g. /^(api\.github\.com|api\.attacker\.net)$/),
        # which would defeat the substitution boundary. Reject it outright. Host
        # variation that stays within one registrable domain can be expressed with
        # char classes / quantifiers instead. (spec §7)
        if "|" in core:
            raise HostPatternError(
                f"alternation '|' not allowed in host regex (can span domains): {pattern!r}"
            )
        # eTLD+1 boundary: the regex must END with a literal \.domain.tld so it
        # cannot match more than one registrable domain.
        m = re.search(r"(?:\\\.[A-Za-z0-9-]+)+$", core)
        if not m:
            raise HostPatternError(
                f"regex must end with a literal \\.domain.tld suffix: {pattern!r}"
            )
        literal = m.group(0).replace("\\.", ".").lstrip(".")
        if _registrable(literal) is None:
            raise HostPatternError(
                f"regex literal suffix is not a single registrable domain: {pattern!r}"
            )
        return

    probe = pattern[2:] if pattern.startswith("*.") else pattern
    if "*" in probe or "/" in probe or " " in probe or "." not in probe:
        raise HostPatternError(f"malformed host pattern: {pattern!r}")
    reg = _registrable(probe)
    if reg is None:
        # e.g. "*.com" -> probe "com" -> not a registrable domain -> rejected here.
        raise HostPatternError(f"not a valid host / too broad: {pattern!r}")


def validate_hosts(hosts: list[str]) -> None:
    """Validate a host list for a secret entry.

    Each pattern must be valid; and a regex-only list is rejected because a
    regex cannot be expressed as an egress allow-list rule (FQDN/wildcard only),
    so at least one FQDN/wildcard companion is required (spec §6.4).
    """
    if not hosts:
        raise HostPatternError("secret entry requires at least one host")
    for pattern in hosts:
        validate_host_pattern(pattern)
    if all(_is_regex(p) for p in hosts):
        raise HostPatternError(
            "regex-only host list needs an FQDN/wildcard companion for the allow-list"
        )


def host_matches(host: str, patterns: list[str]) -> bool:
    """True if ``host`` (already lowercased/port-stripped) matches any pattern."""
    host = host.lower()
    for pattern in patterns:
        if _is_regex(pattern):
            if re.fullmatch(pattern[1:-1], host):
                return True
        elif pattern.startswith("*."):
            suffix = pattern[1:]  # ".example.com"
            if host.endswith(suffix) and host != suffix[1:]:
                return True
        elif host == pattern.lower():
            return True
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_sandbox_env_host_rules.py -v`
Expected: PASS (all cases)

- [ ] **Step 5: Commit**

```bash
git add cubeplex/sandbox_env/__init__.py cubeplex/sandbox_env/host_rules.py tests/unit/test_sandbox_env_host_rules.py
git commit -m "feat(sandbox-env): host pattern validation and matching"
```

---

## Task 3: `SandboxEnvVar` model

Mirrors `MCPCredentialGrant`: own nullable scope FKs, CHECK constraints for scope shape + value shape, partial unique indexes per scope so NULL columns collide correctly.

**Files:**
- Create: `cubeplex/models/sandbox_env.py`
- Modify: `cubeplex/models/__init__.py`
- Test: `tests/unit/test_sandbox_env_model.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_sandbox_env_model.py
from cubeplex.models import SandboxEnvVar


def test_public_id_prefix():
    row = SandboxEnvVar(
        org_id="org-1",
        env_name="GITHUB_TOKEN",
        is_secret=True,
        scope="org",
        hosts=["api.github.com"],
        credential_id="cred-1",
    )
    assert row.id.startswith("senv-")


def test_plain_entry_shape():
    row = SandboxEnvVar(
        org_id="org-1",
        env_name="LOG_LEVEL",
        is_secret=False,
        scope="org",
        plain_value="debug",
    )
    assert row.is_secret is False
    assert row.plain_value == "debug"
    assert row.hosts is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_sandbox_env_model.py -v`
Expected: FAIL with `ImportError: cannot import name 'SandboxEnvVar'`

- [ ] **Step 3: Implement the model**

```python
# cubeplex/models/sandbox_env.py
"""Sandbox Env Vault entry.

One entry per (env_name, scope). Secret entries carry hosts + a credential_id
(value in the vault, kind 'sandbox_env'); plain entries carry plain_value.
Scope shape and value shape are enforced by CHECK constraints; per-scope
uniqueness by partial unique indexes (NULL scope columns must collide, which
plain UNIQUE does not do in Postgres).
"""

from typing import Any, ClassVar

from sqlalchemy import JSON, CheckConstraint, Column, Index
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase


class SandboxEnvVar(CubeplexBase, table=True):
    _PREFIX: ClassVar[str] = "senv"
    __tablename__ = "sandbox_env_vars"
    __table_args__ = (
        CheckConstraint(
            "scope IN ('org','workspace','user')",
            name="ck_sandbox_env_scope",
        ),
        CheckConstraint(
            "(scope='org' AND workspace_id IS NULL AND user_id IS NULL)"
            " OR (scope='workspace' AND workspace_id IS NOT NULL AND user_id IS NULL)"
            " OR (scope='user' AND workspace_id IS NOT NULL AND user_id IS NOT NULL)",
            name="ck_sandbox_env_scope_columns",
        ),
        CheckConstraint(
            "(is_secret AND credential_id IS NOT NULL AND plain_value IS NULL"
            " AND hosts IS NOT NULL)"
            " OR (NOT is_secret AND plain_value IS NOT NULL AND credential_id IS NULL"
            " AND hosts IS NULL)",
            name="ck_sandbox_env_value_shape",
        ),
        Index(
            "uq_sandbox_env_org",
            "org_id",
            "env_name",
            unique=True,
            postgresql_where="scope = 'org'",
        ),
        Index(
            "uq_sandbox_env_workspace",
            "workspace_id",
            "env_name",
            unique=True,
            postgresql_where="scope = 'workspace'",
        ),
        Index(
            "uq_sandbox_env_user",
            "workspace_id",
            "user_id",
            "env_name",
            unique=True,
            postgresql_where="scope = 'user'",
        ),
    )

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    env_name: str = Field(max_length=128)
    is_secret: bool = Field(default=True)
    scope: str = Field(max_length=16)
    workspace_id: str | None = Field(
        default=None, foreign_key="workspaces.id", max_length=20, index=True, nullable=True
    )
    user_id: str | None = Field(
        default=None, foreign_key="users.id", max_length=20, index=True, nullable=True
    )
    hosts: list[str] | None = Field(default=None, sa_column=Column(JSON))
    header_names: list[str] | None = Field(default=None, sa_column=Column(JSON))
    credential_id: str | None = Field(
        default=None, foreign_key="credentials.id", max_length=20, nullable=True
    )
    plain_value: str | None = Field(default=None, max_length=4096, nullable=True)
    status: str = Field(default="valid", max_length=16)
    created_by_user_id: str | None = Field(
        default=None, foreign_key="users.id", max_length=20, nullable=True
    )
```

- [ ] **Step 4: Export the model**

In `cubeplex/models/__init__.py`, add `SandboxEnvVar` to the imports and `__all__` (follow the existing alphabetical/grouped style used for `Credential`, `MCPCredentialGrant`):

```python
from cubeplex.models.sandbox_env import SandboxEnvVar  # noqa: F401
```
and add `"SandboxEnvVar",` to `__all__`.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_sandbox_env_model.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add cubeplex/models/sandbox_env.py cubeplex/models/__init__.py tests/unit/test_sandbox_env_model.py
git commit -m "feat(sandbox-env): SandboxEnvVar model with scope/value constraints"
```

---

## Task 4: Alembic migration

**Files:**
- Create: `alembic/versions/<autogen>_sandbox_env_vars.py` (autogenerated — do NOT hand-write)

- [ ] **Step 1: Autogenerate the migration**

Run (from `backend/`): `uv run alembic revision --autogenerate -m "sandbox env vars"`
Expected: a new file under `alembic/versions/` creating `sandbox_env_vars` with the three CHECK constraints and three partial unique indexes.

- [ ] **Step 2: Inspect the generated migration**

Open the new file. Confirm it contains `create_table("sandbox_env_vars", ...)`, the `ck_sandbox_env_*` CHECK constraints, and the `uq_sandbox_env_*` partial unique indexes (`postgresql_where=...`). If any CHECK/partial index is missing, the model `__table_args__` is wrong — fix the model (Task 3) and regenerate, do not hand-edit.

- [ ] **Step 3: Apply and round-trip**

Run: `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: upgrade creates the table, downgrade drops it, re-upgrade succeeds — all without errors.

- [ ] **Step 4: Verify no pending diff**

Run: `uv run alembic revision --autogenerate -m "noop check"` then inspect the new file.
Expected: the upgrade/downgrade bodies are empty (only `pass`). Delete that specific noop file by its exact path (do not use `git clean`).

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/
git commit -m "feat(sandbox-env): migration for sandbox_env_vars table"
```

---

## Task 5: `SandboxEnvRepository`

Org-scoped with nullable workspace/user (cannot use `ScopedRepository`, which requires non-null workspace). Modeled on `CredentialRepository`.

**Files:**
- Create: `cubeplex/repositories/sandbox_env.py`
- Test: covered via service tests (Task 6) — no separate test file.

- [ ] **Step 1: Implement**

```python
# cubeplex/repositories/sandbox_env.py
"""Repository for SandboxEnvVar — org-scoped, nullable workspace/user."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import SandboxEnvVar


class SandboxEnvRepository:
    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(self, entry_id: str) -> SandboxEnvVar | None:
        stmt = select(SandboxEnvVar).where(
            SandboxEnvVar.id == entry_id,  # type: ignore[arg-type]
            SandboxEnvVar.org_id == self.org_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_resolution(
        self, *, workspace_id: str, user_id: str
    ) -> list[SandboxEnvVar]:
        """All entries in this org that could apply to (workspace_id, user_id):
        org-scope (any), workspace-scope for this workspace, user-scope for this
        (workspace, user). Precedence is applied by the resolver, not here."""
        stmt = select(SandboxEnvVar).where(
            SandboxEnvVar.org_id == self.org_id,  # type: ignore[arg-type]
            SandboxEnvVar.status == "valid",  # type: ignore[arg-type]
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return [
            r
            for r in rows
            if (r.scope == "org")
            or (r.scope == "workspace" and r.workspace_id == workspace_id)
            or (r.scope == "user" and r.workspace_id == workspace_id and r.user_id == user_id)
        ]

    async def add(self, row: SandboxEnvVar) -> SandboxEnvVar:
        row.org_id = self.org_id
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def update(self, row: SandboxEnvVar) -> SandboxEnvVar:
        if row.org_id != self.org_id:
            raise ValueError("cannot update SandboxEnvVar outside the repo's org scope")
        row.updated_at = datetime.now(UTC)
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def delete(self, entry_id: str) -> None:
        row = await self.get(entry_id)
        if row is None:
            return
        await self.session.delete(row)
        await self.session.commit()
```

- [ ] **Step 2: Commit**

```bash
git add cubeplex/repositories/sandbox_env.py
git commit -m "feat(sandbox-env): SandboxEnvRepository"
```

---

## Task 6: `SandboxEnvService` (CRUD + validation)

Validates scope shape, value shape, and host patterns; stores secret values via `CredentialService` (kind `sandbox_env`); creates/updates/deletes entries.

**Files:**
- Create: `cubeplex/services/sandbox_env.py`
- Test: `tests/unit/test_sandbox_env_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_sandbox_env_service.py
from collections.abc import AsyncIterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubeplex.credentials.encryption import FernetBackend
from cubeplex.repositories.credential import CredentialRepository
from cubeplex.repositories.sandbox_env import SandboxEnvRepository
from cubeplex.sandbox_env.host_rules import HostPatternError
from cubeplex.services.credential import CredentialService
from cubeplex.services.sandbox_env import SandboxEnvService, SandboxEnvShapeError


# Self-contained in-memory session — the unit-test convention used by
# tests/unit/test_credential_service.py. (`db_session` is defined only in
# tests/e2e/conftest.py and is NOT visible to tests/unit.)
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
async def service(session):
    org_id = "org-test"
    cred_svc = CredentialService(
        CredentialRepository(session, org_id=org_id),
        FernetBackend([Fernet.generate_key()]),
        org_id=org_id,
        actor_user_id="user-1",
    )
    return SandboxEnvService(
        repo=SandboxEnvRepository(session, org_id=org_id),
        credentials=cred_svc,
        org_id=org_id,
        actor_user_id="user-1",
    )


async def test_create_secret_entry(service):
    entry_id = await service.create_entry(
        env_name="GITHUB_TOKEN",
        is_secret=True,
        scope="workspace",
        workspace_id="ws-1",
        user_id=None,
        hosts=["api.github.com"],
        header_names=None,
        secret_value="ghp_xxx",
        plain_value=None,
    )
    assert entry_id.startswith("senv-")


async def test_create_plain_entry(service):
    entry_id = await service.create_entry(
        env_name="LOG_LEVEL",
        is_secret=False,
        scope="org",
        workspace_id=None,
        user_id=None,
        hosts=None,
        header_names=None,
        secret_value=None,
        plain_value="debug",
    )
    assert entry_id.startswith("senv-")


async def test_secret_requires_hosts(service):
    with pytest.raises(SandboxEnvShapeError):
        await service.create_entry(
            env_name="X", is_secret=True, scope="org", workspace_id=None,
            user_id=None, hosts=None, header_names=None, secret_value="v", plain_value=None,
        )


async def test_bad_scope_shape(service):
    with pytest.raises(SandboxEnvShapeError):
        await service.create_entry(
            env_name="X", is_secret=False, scope="workspace", workspace_id=None,
            user_id=None, hosts=None, header_names=None, secret_value=None, plain_value="v",
        )


async def test_bad_host_rejected(service):
    with pytest.raises(HostPatternError):
        await service.create_entry(
            env_name="X", is_secret=True, scope="org", workspace_id=None,
            user_id=None, hosts=["*.com"], header_names=None, secret_value="v", plain_value=None,
        )
```

> NOTE for implementer: these unit tests define their **own** in-file `session`
> fixture (in-memory SQLite) — copied verbatim from `tests/unit/test_credential_service.py`,
> which is the established unit-test convention. Do **not** use `db_session`: it
> is defined only in `tests/e2e/conftest.py` and is not visible to `tests/unit/`.
> SQLite is sufficient here (the partial unique indexes are Postgres-only and not
> exercised by these logic tests).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_sandbox_env_service.py -v`
Expected: FAIL with `ModuleNotFoundError: cubeplex.services.sandbox_env`

- [ ] **Step 3: Implement**

```python
# cubeplex/services/sandbox_env.py
"""Sandbox Env Vault service: CRUD + validation + scope-precedence resolution."""

from __future__ import annotations

from dataclasses import dataclass

from cubeplex.models import SandboxEnvVar
from cubeplex.repositories.sandbox_env import SandboxEnvRepository
from cubeplex.sandbox_env.host_rules import validate_hosts
from cubeplex.services.credential import CredentialService

SANDBOX_ENV_KIND = "sandbox_env"
_SCOPE_RANK = {"user": 3, "workspace": 2, "org": 1}


class SandboxEnvShapeError(ValueError):
    """Raised on invalid scope shape or value shape."""


def _validate_scope_shape(scope: str, workspace_id: str | None, user_id: str | None) -> None:
    if scope == "org":
        if workspace_id is not None or user_id is not None:
            raise SandboxEnvShapeError("scope='org' requires workspace_id=None, user_id=None")
    elif scope == "workspace":
        if workspace_id is None or user_id is not None:
            raise SandboxEnvShapeError("scope='workspace' requires workspace_id, forbids user_id")
    elif scope == "user":
        if workspace_id is None or user_id is None:
            raise SandboxEnvShapeError("scope='user' requires workspace_id and user_id")
    else:
        raise SandboxEnvShapeError(f"unknown scope: {scope!r}")


def _validate_value_shape(
    is_secret: bool, hosts: list[str] | None, secret_value: str | None, plain_value: str | None
) -> None:
    if is_secret:
        if not hosts:
            raise SandboxEnvShapeError("secret entry requires non-empty hosts")
        if secret_value is None:
            raise SandboxEnvShapeError("secret entry requires secret_value")
        if plain_value is not None:
            raise SandboxEnvShapeError("secret entry forbids plain_value")
        validate_hosts(hosts)  # raises HostPatternError (incl. regex-only rejection)
    else:
        if plain_value is None:
            raise SandboxEnvShapeError("plain entry requires plain_value")
        # Use ``is not None`` (not truthiness): hosts=[] is falsy but the model
        # CHECK requires hosts IS NULL for plain rows, so [] must be a 400 here,
        # not a DB integrity 500.
        if secret_value is not None or hosts is not None:
            raise SandboxEnvShapeError("plain entry forbids secret_value/hosts")


@dataclass
class ResolvedEnv:
    env_name: str
    is_secret: bool
    hosts: list[str] | None
    header_names: list[str] | None
    credential_id: str | None
    plain_value: str | None


class SandboxEnvService:
    def __init__(
        self,
        *,
        repo: SandboxEnvRepository,
        credentials: CredentialService,
        org_id: str,
        actor_user_id: str | None,
    ) -> None:
        self._repo = repo
        self._credentials = credentials
        self._org_id = org_id
        self._actor_user_id = actor_user_id

    async def create_entry(
        self,
        *,
        env_name: str,
        is_secret: bool,
        scope: str,
        workspace_id: str | None,
        user_id: str | None,
        hosts: list[str] | None,
        header_names: list[str] | None,
        secret_value: str | None,
        plain_value: str | None,
    ) -> str:
        _validate_scope_shape(scope, workspace_id, user_id)
        _validate_value_shape(is_secret, hosts, secret_value, plain_value)

        credential_id: str | None = None
        if is_secret:
            assert secret_value is not None  # guaranteed by value-shape validation
            credential_id = await self._credentials.create(
                kind=SANDBOX_ENV_KIND,
                name=f"{scope}:{workspace_id or '-'}:{user_id or '-'}:{env_name}",
                plaintext=secret_value,
            )

        row = SandboxEnvVar(
            org_id=self._org_id,
            env_name=env_name,
            is_secret=is_secret,
            scope=scope,
            workspace_id=workspace_id,
            user_id=user_id,
            hosts=hosts,
            header_names=header_names,
            credential_id=credential_id,
            plain_value=plain_value,
            created_by_user_id=self._actor_user_id,
        )
        try:
            saved = await self._repo.add(row)
        except Exception:
            # The credential is committed before the row insert; if the insert
            # fails (duplicate partial-unique index, FK, CHECK), delete the
            # now-orphaned credential so we don't leave a dangling secret.
            if credential_id is not None:
                await self._credentials.delete(credential_id=credential_id)
            raise
        return saved.id

    async def update_secret_value(self, *, entry_id: str, secret_value: str) -> None:
        row = await self._repo.get(entry_id)
        if row is None or not row.is_secret or row.credential_id is None:
            raise SandboxEnvShapeError(f"no secret entry {entry_id}")
        await self._credentials.update(
            credential_id=row.credential_id, plaintext=secret_value
        )

    async def delete_entry(self, *, entry_id: str) -> None:
        row = await self._repo.get(entry_id)
        if row is None:
            return
        await self._repo.delete(entry_id)
        if row.is_secret and row.credential_id is not None:
            await self._credentials.delete(credential_id=row.credential_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_sandbox_env_service.py -v`
Expected: PASS

- [ ] **Step 5: Guard credential deletion against env-var references**

In `cubeplex/services/credential.py`, extend `CredentialService._guard_references` to also reject deleting a credential still referenced by a `SandboxEnvVar` (same pattern as the existing `Provider` check):

```python
from cubeplex.models import SandboxEnvVar
# ... inside _guard_references, after the Provider check:
env_refs = (
    (
        await session.execute(
            select(SandboxEnvVar).where(
                SandboxEnvVar.credential_id == credential_id  # type: ignore[arg-type]
            )
        )
    )
    .scalars()
    .all()
)
if env_refs:
    raise CredentialInUseError(
        f"credential {credential_id} referenced by SandboxEnvVar: {[e.id for e in env_refs]}"
    )
```

Note: `SandboxEnvService.delete_entry` deletes the env row *before* the credential, so the normal delete path is unaffected; this guard only protects against deleting a still-referenced credential through other paths.

- [ ] **Step 6: Commit**

```bash
git add cubeplex/services/sandbox_env.py cubeplex/services/credential.py tests/unit/test_sandbox_env_service.py
git commit -m "feat(sandbox-env): SandboxEnvService CRUD with validation"
```

---

## Task 7: Scope-precedence resolver

Produces the effective env set for a sandbox (user > workspace > org), used by Plan 2's run-start injection.

**Files:**
- Modify: `cubeplex/services/sandbox_env.py` (add `SandboxEnvResolver`)
- Test: `tests/unit/test_sandbox_env_resolver.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_sandbox_env_resolver.py
from collections.abc import AsyncIterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubeplex.credentials.encryption import FernetBackend
from cubeplex.repositories.credential import CredentialRepository
from cubeplex.repositories.sandbox_env import SandboxEnvRepository
from cubeplex.services.credential import CredentialService
from cubeplex.services.sandbox_env import SandboxEnvResolver, SandboxEnvService


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
async def seeded(session):
    org_id = "org-r"
    cred = CredentialService(
        CredentialRepository(session, org_id=org_id),
        FernetBackend([Fernet.generate_key()]),
        org_id=org_id,
        actor_user_id="u1",
    )
    svc = SandboxEnvService(
        repo=SandboxEnvRepository(session, org_id=org_id),
        credentials=cred,
        org_id=org_id,
        actor_user_id="u1",
    )
    # org-level GH token, then a user-level override of the same env name
    await svc.create_entry(
        env_name="GITHUB_TOKEN", is_secret=True, scope="org", workspace_id=None,
        user_id=None, hosts=["api.github.com"], header_names=None,
        secret_value="org-token", plain_value=None,
    )
    await svc.create_entry(
        env_name="GITHUB_TOKEN", is_secret=True, scope="user", workspace_id="ws-1",
        user_id="u1", hosts=["api.github.com"], header_names=None,
        secret_value="user-token", plain_value=None,
    )
    await svc.create_entry(
        env_name="LOG_LEVEL", is_secret=False, scope="org", workspace_id=None,
        user_id=None, hosts=None, header_names=None, secret_value=None, plain_value="info",
    )
    return SandboxEnvResolver(SandboxEnvRepository(session, org_id=org_id))


async def test_user_overrides_org(seeded):
    resolved = await seeded.resolve(workspace_id="ws-1", user_id="u1")
    by_name = {r.env_name: r for r in resolved}
    # GITHUB_TOKEN should resolve to the user-scope entry, LOG_LEVEL to org plain.
    assert by_name["GITHUB_TOKEN"].is_secret
    assert by_name["LOG_LEVEL"].plain_value == "info"
    assert len(resolved) == 2  # one effective entry per env_name


async def test_other_user_gets_org(seeded):
    resolved = await seeded.resolve(workspace_id="ws-1", user_id="u2")
    by_name = {r.env_name: r for r in resolved}
    assert "GITHUB_TOKEN" in by_name  # falls back to org-scope
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_sandbox_env_resolver.py -v`
Expected: FAIL with `ImportError: cannot import name 'SandboxEnvResolver'`

- [ ] **Step 3: Implement (append to `cubeplex/services/sandbox_env.py`)**

```python
class SandboxEnvResolver:
    """Resolve the effective env set for (workspace, user) by scope precedence."""

    def __init__(self, repo: SandboxEnvRepository) -> None:
        self._repo = repo

    async def resolve(self, *, workspace_id: str, user_id: str) -> list[ResolvedEnv]:
        rows = await self._repo.list_for_resolution(
            workspace_id=workspace_id, user_id=user_id
        )
        best: dict[str, SandboxEnvVar] = {}
        for row in rows:
            cur = best.get(row.env_name)
            if cur is None or _SCOPE_RANK[row.scope] > _SCOPE_RANK[cur.scope]:
                best[row.env_name] = row
        return [
            ResolvedEnv(
                env_name=r.env_name,
                is_secret=r.is_secret,
                hosts=r.hosts,
                header_names=r.header_names,
                credential_id=r.credential_id,
                plain_value=r.plain_value,
            )
            for r in best.values()
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_sandbox_env_resolver.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cubeplex/services/sandbox_env.py tests/unit/test_sandbox_env_resolver.py
git commit -m "feat(sandbox-env): scope-precedence resolver"
```

---

## Task 8: Request/response schemas

**Files:**
- Create: `cubeplex/api/schemas/sandbox_env.py`

- [ ] **Step 1: Implement**

```python
# cubeplex/api/schemas/sandbox_env.py
"""Schemas for sandbox env vault routes."""

from pydantic import BaseModel, Field


class CreateOrgEnvIn(BaseModel):
    env_name: str = Field(max_length=128)
    is_secret: bool = True
    hosts: list[str] | None = None
    header_names: list[str] | None = None
    secret_value: str | None = None
    plain_value: str | None = None


class CreateWorkspaceEnvIn(CreateOrgEnvIn):
    """workspace-scope: workspace_id from path; user_id stays None."""


class CreateUserEnvIn(CreateOrgEnvIn):
    """user-scope: workspace_id from path; user_id from the authed user."""


class EnvEntryOut(BaseModel):
    id: str
    env_name: str
    is_secret: bool
    scope: str
    workspace_id: str | None
    user_id: str | None
    hosts: list[str] | None
    header_names: list[str] | None
    status: str
    # NOTE: never serialize secret value or credential_id plaintext.


class EnvEntryListOut(BaseModel):
    entries: list[EnvEntryOut]
```

- [ ] **Step 2: Commit**

```bash
git add cubeplex/api/schemas/sandbox_env.py
git commit -m "feat(sandbox-env): API schemas"
```

---

## Task 9: Org-scope admin routes

Scope-isolated: org-scope entries managed only via `/admin/...`, gated by `get_admin_request_context`.

**Files:**
- Create: `cubeplex/api/routes/v1/admin_sandbox_env.py`
- Test: `tests/e2e/test_sandbox_env_routes.py` (org cases)

- [ ] **Step 1: Implement the router**

```python
# cubeplex/api/routes/v1/admin_sandbox_env.py
"""Org-scope sandbox env vault routes (org admins only)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.sandbox_env import CreateOrgEnvIn, EnvEntryListOut, EnvEntryOut
from cubeplex.auth.context import RequestContext
from cubeplex.credentials.dependencies import get_encryption_backend
from cubeplex.credentials.encryption import EncryptionBackend
from cubeplex.mcp.dependencies import get_admin_request_context
from cubeplex.db import get_session
from cubeplex.repositories.credential import CredentialRepository
from cubeplex.repositories.sandbox_env import SandboxEnvRepository
from cubeplex.sandbox_env.host_rules import HostPatternError
from cubeplex.services.credential import CredentialService
from cubeplex.services.sandbox_env import SandboxEnvService, SandboxEnvShapeError

router = APIRouter(prefix="/admin/sandbox-env", tags=["admin-sandbox-env"])


def _service(session: AsyncSession, backend: EncryptionBackend, ctx: RequestContext) -> SandboxEnvService:
    cred = CredentialService(
        CredentialRepository(session, org_id=ctx.org_id),
        backend,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
    )
    return SandboxEnvService(
        repo=SandboxEnvRepository(session, org_id=ctx.org_id),
        credentials=cred,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
    )


@router.post("", response_model=EnvEntryOut, status_code=status.HTTP_201_CREATED)
async def create_org_env(
    body: CreateOrgEnvIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> EnvEntryOut:
    svc = _service(session, backend, ctx)
    try:
        entry_id = await svc.create_entry(
            env_name=body.env_name,
            is_secret=body.is_secret,
            scope="org",
            workspace_id=None,
            user_id=None,
            hosts=body.hosts,
            header_names=body.header_names,
            secret_value=body.secret_value,
            plain_value=body.plain_value,
        )
    except (SandboxEnvShapeError, HostPatternError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    row = await SandboxEnvRepository(session, org_id=ctx.org_id).get(entry_id)
    assert row is not None
    return EnvEntryOut(**row.model_dump(include=set(EnvEntryOut.model_fields)))


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_org_env(
    entry_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> None:
    row = await SandboxEnvRepository(session, org_id=ctx.org_id).get(entry_id)
    if row is None or row.scope != "org":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    await _service(session, backend, ctx).delete_entry(entry_id=entry_id)
```

> NOTE for implementer: verify the exact import paths for `get_session` (likely `cubeplex.db` or `cubeplex.api.deps`), `get_encryption_backend`, and `get_admin_request_context` by grepping `admin_mcp.py`'s imports — copy them verbatim. The handler shape above matches the MCP admin routes.

- [ ] **Step 2: Write the e2e test (org cases)**

```python
# tests/e2e/test_sandbox_env_routes.py
# The `admin_client` / `member_client` fixtures from tests/e2e/conftest.py yield
# a tuple `(client, workspace_id)` — unpack before use. There is no standalone
# `workspace_id` fixture.

async def test_admin_create_and_delete_org_secret(admin_client):
    client, _ws = admin_client
    resp = await client.post(
        "/api/v1/admin/sandbox-env",
        json={
            "env_name": "GITHUB_TOKEN",
            "is_secret": True,
            "hosts": ["api.github.com"],
            "secret_value": "ghp_x",
        },
    )
    assert resp.status_code == 201
    entry = resp.json()
    assert entry["scope"] == "org"
    assert "secret_value" not in entry  # never leaked

    del_resp = await client.delete(f"/api/v1/admin/sandbox-env/{entry['id']}")
    assert del_resp.status_code == 204


async def test_admin_rejects_bad_host(admin_client):
    client, _ws = admin_client
    resp = await client.post(
        "/api/v1/admin/sandbox-env",
        json={"env_name": "X", "is_secret": True, "hosts": ["*.com"], "secret_value": "v"},
    )
    assert resp.status_code == 400
```

- [ ] **Step 3: Commit (router wired in Task 11 before running e2e)**

```bash
git add cubeplex/api/routes/v1/admin_sandbox_env.py tests/e2e/test_sandbox_env_routes.py
git commit -m "feat(sandbox-env): org-scope admin routes"
```

---

## Task 10: Workspace/user-scope routes

Scope-isolated: workspace- and user-scope entries via `/ws/{workspace_id}/...`. Workspace-scope requires `require_admin`; user-scope (`/me`) open to `require_member` and pins `user_id = ctx.user.id`.

**Files:**
- Create: `cubeplex/api/routes/v1/ws_sandbox_env.py`
- Test: extend `tests/e2e/test_sandbox_env_routes.py` (ws/user cases)

- [ ] **Step 1: Implement the router**

```python
# cubeplex/api/routes/v1/ws_sandbox_env.py
"""Workspace- and user-scope sandbox env vault routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.sandbox_env import CreateUserEnvIn, CreateWorkspaceEnvIn, EnvEntryOut
from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import require_admin, require_member
from cubeplex.credentials.dependencies import get_encryption_backend
from cubeplex.credentials.encryption import EncryptionBackend
from cubeplex.db import get_session
from cubeplex.repositories.credential import CredentialRepository
from cubeplex.repositories.sandbox_env import SandboxEnvRepository
from cubeplex.sandbox_env.host_rules import HostPatternError
from cubeplex.services.credential import CredentialService
from cubeplex.services.sandbox_env import SandboxEnvService, SandboxEnvShapeError

router = APIRouter(prefix="/ws/{workspace_id}/sandbox-env", tags=["ws-sandbox-env"])


def _service(session, backend, ctx: RequestContext) -> SandboxEnvService:
    cred = CredentialService(
        CredentialRepository(session, org_id=ctx.org_id),
        backend,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
    )
    return SandboxEnvService(
        repo=SandboxEnvRepository(session, org_id=ctx.org_id),
        credentials=cred,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
    )


@router.post("/workspace", response_model=EnvEntryOut, status_code=201)
async def create_workspace_env(
    workspace_id: str,
    body: CreateWorkspaceEnvIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
) -> EnvEntryOut:
    try:
        entry_id = await _service(session, backend, ctx).create_entry(
            env_name=body.env_name, is_secret=body.is_secret, scope="workspace",
            workspace_id=workspace_id, user_id=None, hosts=body.hosts,
            header_names=body.header_names, secret_value=body.secret_value,
            plain_value=body.plain_value,
        )
    except (SandboxEnvShapeError, HostPatternError) as exc:
        raise HTTPException(400, str(exc)) from exc
    row = await SandboxEnvRepository(session, org_id=ctx.org_id).get(entry_id)
    assert row is not None
    return EnvEntryOut(**row.model_dump(include=set(EnvEntryOut.model_fields)))


@router.post("/me", response_model=EnvEntryOut, status_code=201)
async def create_user_env(
    workspace_id: str,
    body: CreateUserEnvIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> EnvEntryOut:
    try:
        entry_id = await _service(session, backend, ctx).create_entry(
            env_name=body.env_name, is_secret=body.is_secret, scope="user",
            workspace_id=workspace_id, user_id=ctx.user.id, hosts=body.hosts,
            header_names=body.header_names, secret_value=body.secret_value,
            plain_value=body.plain_value,
        )
    except (SandboxEnvShapeError, HostPatternError) as exc:
        raise HTTPException(400, str(exc)) from exc
    row = await SandboxEnvRepository(session, org_id=ctx.org_id).get(entry_id)
    assert row is not None
    return EnvEntryOut(**row.model_dump(include=set(EnvEntryOut.model_fields)))
```

- [ ] **Step 2: Add e2e cases**

```python
# append to tests/e2e/test_sandbox_env_routes.py
async def test_member_sets_own_user_env(member_client):
    client, workspace_id = member_client
    resp = await client.post(
        f"/api/v1/ws/{workspace_id}/sandbox-env/me",
        json={"env_name": "GITHUB_TOKEN", "is_secret": True,
              "hosts": ["api.github.com"], "secret_value": "ghp_u"},
    )
    assert resp.status_code == 201
    assert resp.json()["scope"] == "user"


async def test_member_cannot_set_workspace_env(member_client):
    client, workspace_id = member_client
    resp = await client.post(
        f"/api/v1/ws/{workspace_id}/sandbox-env/workspace",
        json={"env_name": "X", "is_secret": False, "plain_value": "v"},
    )
    assert resp.status_code == 403  # require_admin
```

> NOTE for implementer: `admin_client` and `member_client` (in `tests/e2e/conftest.py`) each yield a `(client, workspace_id)` tuple — always unpack first (`client, workspace_id = member_client`). There is no separate `workspace_id` fixture.

- [ ] **Step 3: Commit**

```bash
git add cubeplex/api/routes/v1/ws_sandbox_env.py tests/e2e/test_sandbox_env_routes.py
git commit -m "feat(sandbox-env): workspace/user-scope routes"
```

---

## Task 11: Register routers

**Files:**
- Modify: `cubeplex/api/routes/v1/__init__.py` (add to the module import block + `__all__`)
- Modify: `cubeplex/api/app.py` (near the existing `admin_mcp` / `ws_mcp` includes, ~lines 410-430)

- [ ] **Step 1: Export the new modules**

In `cubeplex/api/routes/v1/__init__.py`, add `admin_sandbox_env` and `ws_sandbox_env` to the `from cubeplex.api.routes.v1 import (...)` block and to `__all__` (alongside `admin_mcp`, `ws_mcp`).

- [ ] **Step 2: Import and include in app.py**

Add to the imports block alongside `admin_mcp, ws_mcp`:

```python
from cubeplex.api.routes.v1 import admin_sandbox_env, ws_sandbox_env
```

Add alongside the existing `app.include_router(... admin_mcp ...)` calls:

```python
    app.include_router(admin_sandbox_env.router, prefix="/api/v1")
    app.include_router(ws_sandbox_env.router, prefix="/api/v1")
```

- [ ] **Step 3: Run the full sandbox-env test set**

Run: `uv run pytest tests/unit/test_sandbox_env_host_rules.py tests/unit/test_sandbox_env_model.py tests/unit/test_sandbox_env_service.py tests/unit/test_sandbox_env_resolver.py tests/e2e/test_sandbox_env_routes.py -v`
Expected: all PASS.

- [ ] **Step 4: Type-check and lint**

Run: `uv run mypy cubeplex/ && uv run ruff check cubeplex/`
Expected: no issues.

- [ ] **Step 5: Commit**

```bash
git add cubeplex/api/app.py
git commit -m "feat(sandbox-env): register admin + workspace routers"
```

---

## Self-Review Checklist (completed by plan author)

- **Spec coverage:** §6.4 `SandboxEnvVar` (Task 3 + migration Task 4) incl. value-shape CHECK + partial unique (Task 3); `sandbox_env` credential kind (Task 6); scope-isolated management routes (Tasks 9–10); host rules incl. eTLD+1 + anchored regex (Task 2, §7); scope precedence (Task 7, §6.5). **Out of this plan (Plan 2/3):** `EgressRef`, run-start injection, exchange endpoint, webhook, addon — intentionally deferred.
- **Type consistency:** `SandboxEnvService.create_entry(...)` signature is identical across Tasks 6/9/10; `ResolvedEnv` fields match between Task 6 (definition) and Task 7 (construction); `host_matches`/`validate_host_pattern` names consistent Task 2 ↔ usage.
- **Resolved against the real repo (Codex review rev 2):** unit tests use a
  self-contained in-file `session` fixture (not `db_session`); e2e `admin_client`/
  `member_client` yield `(client, workspace_id)` tuples (unpacked); `session`
  import paths (`get_admin_request_context` → `cubeplex.mcp.dependencies`,
  `get_session` → `cubeplex.db`, `get_encryption_backend` →
  `cubeplex.credentials.dependencies`) verified; encryption backend is
  `FernetBackend([Fernet.generate_key()])`.

---

## Next

Plan 2 (`docs/dev/plans/2026-05-25-egress-injection-exchange.md`) builds on this: `EgressRef`, `SidecarAuthenticator`, the internal exchange endpoint, and run-start vault-driven injection in the sandbox manager (consuming `SandboxEnvResolver.resolve`).
