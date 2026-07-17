# Egress Injection + Exchange (Backend) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** At sandbox creation, inject the resolved env vault as env vars — secrets as opaque `cbxref_` placeholders, plain values verbatim — set the egress `network_policy`, persist an `EgressRef` record, and expose an internal exchange endpoint that swaps a placeholder for its real secret only for a verified sidecar of that sandbox sending to the secret's declared host.

**Architecture:** Builds on Plan 1's `SandboxEnvResolver`. A `SandboxEnvInjector` turns the resolved set into `(env, network_policy, ref_bindings)`; the manager passes env+policy to `Sandbox.create`, then persists an `EgressRef` keyed by `hash(placeholder)` → `{sandbox_id, bindings}`. The exchange endpoint authenticates the sidecar (pluggable: mTLS in prod, shared secret in bare-local), enforces `sandbox_id` match + host match, and returns the decrypted secret.

**Tech Stack:** Python 3.13, FastAPI, SQLModel, Alembic, opensandbox SDK (`Sandbox.create(env=..., network_policy=...)`), pytest.

**Scope:** Plan 2 of 3. Depends on Plan 1 (`SandboxEnvVar`, `SandboxEnvResolver`, `host_rules`). Plan 3 = the K8s webhook + mitmproxy addon that actually call this exchange endpoint. This plan is bare-unit testable (dev authenticator); the full sidecar path is exercised in Plan 3's cluster E2E.

**Spec:** `docs/dev/specs/2026-05-25-egress-key-injection-design.md` (§4.1 authenticator, §5 flow, §6.5 EgressRef + injection, §6.6 exchange).

---

## File Structure

- Create `cubeplex/sandbox_env/placeholder.py` — `cbxref_` mint + detect helpers.
- Create `cubeplex/models/egress_ref.py` — `EgressRef` model.
- Modify `cubeplex/models/__init__.py` — export `EgressRef`.
- Create `alembic/versions/<autogen>_egress_refs.py` — migration (autogen).
- Create `cubeplex/repositories/egress_ref.py` — `EgressRefRepository`.
- Create `cubeplex/sandbox_env/injector.py` — `SandboxEnvInjector` (resolved set → env + policy + ref bindings).
- Create `cubeplex/sandbox_env/exchange_auth.py` — `SidecarAuthenticator` protocol + `MtlsAuthenticator`, `DevSharedSecretAuthenticator`, `build_sidecar_authenticator(config)` factory + prod guardrail.
- Create `cubeplex/services/egress_exchange.py` — `EgressExchangeService` (verify → match → decrypt).
- Create `cubeplex/api/routes/internal_egress.py` — internal control-plane exchange route.
- Modify `cubeplex/api/app.py` — mount the internal exchange router.
- Modify `cubeplex/sandbox/manager.py` — build env+policy before `Sandbox.create`, persist/revoke `EgressRef` around it.
- Tests: `tests/unit/test_egress_placeholder.py`, `tests/unit/test_egress_injector.py`, `tests/unit/test_egress_exchange_auth.py`, `tests/unit/test_egress_exchange_service.py`, `tests/e2e/test_internal_egress_route.py`.

---

## Task 1: `cbxref_` placeholder helpers

**Files:**
- Create: `cubeplex/sandbox_env/placeholder.py`
- Test: `tests/unit/test_egress_placeholder.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_egress_placeholder.py
import re

from cubeplex.sandbox_env.placeholder import (
    PLACEHOLDER_RE,
    hash_placeholder,
    mint_placeholder,
)


def test_mint_is_unique_and_well_formed():
    a, b = mint_placeholder(), mint_placeholder()
    assert a != b
    assert a.startswith("cbxref_")
    assert PLACEHOLDER_RE.fullmatch(a)


def test_scan_finds_placeholder_in_header_value():
    p = mint_placeholder()
    found = PLACEHOLDER_RE.findall(f"Bearer {p}")
    assert found == [p]


def test_hash_is_stable_and_hex():
    p = mint_placeholder()
    h1, h2 = hash_placeholder(p), hash_placeholder(p)
    assert h1 == h2
    assert re.fullmatch(r"[0-9a-f]{64}", h1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_egress_placeholder.py -v`
Expected: FAIL `ModuleNotFoundError: cubeplex.sandbox_env.placeholder`

- [ ] **Step 3: Implement**

```python
# cubeplex/sandbox_env/placeholder.py
"""Opaque placeholder tokens injected into the sandbox in place of real secrets.

A tool reads the env var, sends the placeholder in a header; the egress addon
scans headers for this pattern and exchanges it for the real secret. The token
is high-entropy so header scanning cannot accidentally match real data.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets

_PREFIX = "cbxref_"
# 160 bits of randomness, base32 (no padding, uppercase A-Z2-7).
_RAND_BYTES = 20

# Recognizable, self-delimiting: prefix + fixed-length base32 body.
PLACEHOLDER_RE = re.compile(r"cbxref_[A-Z2-7]{32}")


def mint_placeholder() -> str:
    body = base64.b32encode(secrets.token_bytes(_RAND_BYTES)).decode("ascii").rstrip("=")
    return f"{_PREFIX}{body}"


def hash_placeholder(placeholder: str) -> str:
    """Stable hex SHA-256; only the hash is persisted in the ref record."""
    return hashlib.sha256(placeholder.encode("ascii")).hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_egress_placeholder.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cubeplex/sandbox_env/placeholder.py tests/unit/test_egress_placeholder.py
git commit -m "feat(egress): cbxref placeholder mint/scan/hash helpers"
```

---

## Task 2: `EgressRef` model + migration

**Files:**
- Create: `cubeplex/models/egress_ref.py`
- Modify: `cubeplex/models/__init__.py`
- Create: `alembic/versions/<autogen>_egress_refs.py`
- Test: `tests/unit/test_egress_ref_model.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_egress_ref_model.py
from cubeplex.models import EgressRef


def test_prefix_and_fields():
    ref = EgressRef(
        ref_hash="a" * 64,
        sandbox_id="sbx-1",
        org_id="org-1",
        workspace_id="ws-1",
        user_id="u-1",
        run_id="run-1",
        bindings=[{"env_name": "GITHUB_TOKEN", "hosts": ["api.github.com"],
                   "header_names": None, "credential_id": "cred-1"}],
    )
    assert ref.id.startswith("eref-")
    assert ref.status == "valid"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_egress_ref_model.py -v`
Expected: FAIL `ImportError: cannot import name 'EgressRef'`

- [ ] **Step 3: Implement the model**

```python
# cubeplex/models/egress_ref.py
"""Per-run egress placeholder reference. Stores only hash(placeholder)."""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, Column, Index
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase


class EgressRef(CubeplexBase, table=True):
    _PREFIX: ClassVar[str] = "eref"
    __tablename__ = "egress_refs"
    __table_args__ = (
        Index("ix_egress_ref_hash", "ref_hash", unique=True),
        Index("ix_egress_ref_sandbox", "sandbox_id"),
    )

    ref_hash: str = Field(max_length=64)
    sandbox_id: str = Field(max_length=64, index=True)
    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    workspace_id: str = Field(foreign_key="workspaces.id", max_length=20)
    user_id: str = Field(foreign_key="users.id", max_length=20)
    run_id: str | None = Field(default=None, max_length=64, nullable=True)
    bindings: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    status: str = Field(default="valid", max_length=16)
    expires_at: datetime | None = Field(default=None, nullable=True)
```

- [ ] **Step 4: Export** in `cubeplex/models/__init__.py`: `from cubeplex.models.egress_ref import EgressRef  # noqa: F401` and add `"EgressRef"` to `__all__`.

- [ ] **Step 5: Run model test**

Run: `uv run pytest tests/unit/test_egress_ref_model.py -v`
Expected: PASS

- [ ] **Step 6: Autogenerate + apply migration**

Run (from `backend/`): `uv run alembic revision --autogenerate -m "egress refs"`
Then inspect the file (creates `egress_refs` with the two indexes), then:
Run: `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: clean round-trip.

- [ ] **Step 7: Commit**

```bash
git add cubeplex/models/egress_ref.py cubeplex/models/__init__.py alembic/versions/ tests/unit/test_egress_ref_model.py
git commit -m "feat(egress): EgressRef model + migration"
```

---

## Task 3: `EgressRefRepository`

**Files:**
- Create: `cubeplex/repositories/egress_ref.py`

- [ ] **Step 1: Implement**

```python
# cubeplex/repositories/egress_ref.py
"""Repository for EgressRef. Lookups by ref_hash are global (the exchange
caller is a sidecar, not an org-scoped user); writes/revokes are by sandbox."""

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import EgressRef


class EgressRefRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, ref: EgressRef) -> EgressRef:
        self.session.add(ref)
        await self.session.commit()
        await self.session.refresh(ref)
        return ref

    async def get_valid_by_hash(self, ref_hash: str) -> EgressRef | None:
        now = datetime.now(UTC)
        stmt = select(EgressRef).where(
            EgressRef.ref_hash == ref_hash,  # type: ignore[arg-type]
            EgressRef.status == "valid",  # type: ignore[arg-type]
        )
        ref = (await self.session.execute(stmt)).scalar_one_or_none()
        if ref is None:
            return None
        if ref.expires_at is not None and ref.expires_at < now:
            return None
        return ref

    async def revoke_for_sandbox(self, sandbox_id: str) -> None:
        await self.session.execute(
            update(EgressRef)
            .where(EgressRef.sandbox_id == sandbox_id)  # type: ignore[arg-type]
            .values(status="revoked")
        )
        await self.session.commit()
```

- [ ] **Step 2: Commit**

```bash
git add cubeplex/repositories/egress_ref.py
git commit -m "feat(egress): EgressRefRepository"
```

---

## Task 4: `SandboxEnvInjector`

Turns Plan 1's `list[ResolvedEnv]` into `(env_vars, network_policy, ref_bindings)`. Secrets → placeholder env + binding + allow-list host(s); plain → literal env. Always adds the exchange host to the allow-list. Pure function of inputs (no DB, no sandbox_id yet) so it can run before `Sandbox.create`.

**Files:**
- Create: `cubeplex/sandbox_env/injector.py`
- Test: `tests/unit/test_egress_injector.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_egress_injector.py
from cubeplex.sandbox_env.injector import SandboxEnvInjector
from cubeplex.sandbox_env.placeholder import PLACEHOLDER_RE
from cubeplex.services.sandbox_env import ResolvedEnv


def test_secret_becomes_placeholder_plain_passes_through():
    inj = SandboxEnvInjector(exchange_host="egress-exchange.internal")
    resolved = [
        ResolvedEnv("GITHUB_TOKEN", True, ["api.github.com"], None, "cred-1", None),
        ResolvedEnv("LOG_LEVEL", False, None, None, None, "info"),
    ]
    result = inj.build(resolved)
    assert PLACEHOLDER_RE.fullmatch(result.env["GITHUB_TOKEN"])
    assert result.env["LOG_LEVEL"] == "info"
    # one binding for the secret, keyed by hash of its placeholder
    assert len(result.bindings) == 1
    assert result.bindings[0]["env_name"] == "GITHUB_TOKEN"
    assert result.bindings[0]["hosts"] == ["api.github.com"]
    # allow-list contains the secret host plus the exchange host
    targets = {r.target for r in result.network_policy.egress}
    assert "api.github.com" in targets
    assert "egress-exchange.internal" in targets


def test_wildcard_host_maps_to_allowlist_rule():
    inj = SandboxEnvInjector(exchange_host="x.internal")
    resolved = [ResolvedEnv("T", True, ["*.example.com"], None, "c", None)]
    result = inj.build(resolved)
    assert "*.example.com" in {r.target for r in result.network_policy.egress}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_egress_injector.py -v`
Expected: FAIL `ModuleNotFoundError: cubeplex.sandbox_env.injector`

- [ ] **Step 3: Implement**

```python
# cubeplex/sandbox_env/injector.py
"""Build sandbox env + egress network policy + ref bindings from resolved env."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from opensandbox.models.sandboxes import (
    NetworkPolicy,
    NetworkPolicyDefaultAction,
    NetworkRule,
    NetworkRuleAction,
)

from cubeplex.sandbox_env.placeholder import hash_placeholder, mint_placeholder
from cubeplex.services.sandbox_env import ResolvedEnv

# Regex host patterns ("/.../" ) cannot be expressed as an egress allow-list
# rule (FQDN/wildcard only). Plan 1 guarantees a secret with a regex host also
# carries an FQDN/wildcard companion, so we simply skip regex items here.
def _allowlist_targets(hosts: list[str]) -> list[str]:
    return [h for h in hosts if not (h.startswith("/") and h.endswith("/"))]


@dataclass
class InjectionResult:
    env: dict[str, str] = field(default_factory=dict)
    network_policy: NetworkPolicy = field(default_factory=NetworkPolicy)
    bindings: list[dict[str, Any]] = field(default_factory=list)


class SandboxEnvInjector:
    def __init__(self, *, exchange_host: str) -> None:
        self._exchange_host = exchange_host

    def build(self, resolved: list[ResolvedEnv]) -> InjectionResult:
        env: dict[str, str] = {}
        bindings: list[dict[str, Any]] = []
        targets: set[str] = {self._exchange_host}

        for r in resolved:
            if r.is_secret:
                assert r.hosts and r.credential_id  # Plan 1 value-shape guarantees
                placeholder = mint_placeholder()
                env[r.env_name] = placeholder
                bindings.append(
                    {
                        "ref_hash": hash_placeholder(placeholder),
                        "env_name": r.env_name,
                        "hosts": r.hosts,
                        "header_names": r.header_names,
                        "credential_id": r.credential_id,
                    }
                )
                targets.update(_allowlist_targets(r.hosts))
            else:
                assert r.plain_value is not None
                env[r.env_name] = r.plain_value

        policy = NetworkPolicy(
            default_action=NetworkPolicyDefaultAction.DENY,
            egress=[NetworkRule(action=NetworkRuleAction.ALLOW, target=t) for t in sorted(targets)],
        )
        return InjectionResult(env=env, network_policy=policy, bindings=bindings)
```

> NOTE for implementer: confirm the exact enum import names in `opensandbox.models.sandboxes` (`NetworkPolicyDefaultAction.DENY`, `NetworkRuleAction.ALLOW`). Run `uv run python -c "from opensandbox.models.sandboxes import NetworkPolicyDefaultAction, NetworkRuleAction; print(list(NetworkPolicyDefaultAction), list(NetworkRuleAction))"` and adjust to the real member names if they differ (e.g. `DENY` vs `deny`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_egress_injector.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cubeplex/sandbox_env/injector.py tests/unit/test_egress_injector.py
git commit -m "feat(egress): SandboxEnvInjector (env + policy + bindings)"
```

---

## Task 5: `SidecarAuthenticator` (pluggable, config-selected)

**Files:**
- Create: `cubeplex/sandbox_env/exchange_auth.py`
- Test: `tests/unit/test_egress_exchange_auth.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_egress_exchange_auth.py
import pytest

from cubeplex.sandbox_env.exchange_auth import (
    DevSharedSecretAuthenticator,
    SidecarIdentity,
    build_sidecar_authenticator,
)


class _Req:
    def __init__(self, headers, client_cert=None):
        self.headers = headers
        self.client_cert = client_cert


async def test_dev_authenticator_accepts_token_and_returns_sandbox_id():
    auth = DevSharedSecretAuthenticator(token="devtok")
    ident = await auth.verify(_Req({"x-egress-dev-token": "devtok", "x-egress-sandbox-id": "sbx-9"}))
    assert ident == SidecarIdentity(sandbox_id="sbx-9")


async def test_dev_authenticator_rejects_bad_token():
    auth = DevSharedSecretAuthenticator(token="devtok")
    with pytest.raises(PermissionError):
        await auth.verify(_Req({"x-egress-dev-token": "nope", "x-egress-sandbox-id": "sbx-9"}))


def test_factory_refuses_dev_in_production():
    with pytest.raises(RuntimeError):
        build_sidecar_authenticator(
            {"mode": "dev", "dev_token": "t"}, deployment_mode="production"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_egress_exchange_auth.py -v`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# cubeplex/sandbox_env/exchange_auth.py
"""Sidecar identity verification for the egress exchange endpoint.

Pluggable so the same endpoint works in production (mTLS, per-sandbox client
cert carrying sandbox_id) and bare-local dev (shared secret + explicit
sandbox_id header). The dev backend must never run in production.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class SidecarIdentity:
    sandbox_id: str


class SidecarAuthenticator(Protocol):
    async def verify(self, request: Any) -> SidecarIdentity:
        """Return the verified sidecar identity, or raise PermissionError."""
        ...


class DevSharedSecretAuthenticator:
    """Bare-local only. Trusts a shared token; takes sandbox_id from a header."""

    def __init__(self, *, token: str) -> None:
        self._token = token

    async def verify(self, request: Any) -> SidecarIdentity:
        if request.headers.get("x-egress-dev-token") != self._token:
            raise PermissionError("bad dev token")
        sandbox_id = request.headers.get("x-egress-sandbox-id")
        if not sandbox_id:
            raise PermissionError("missing x-egress-sandbox-id")
        return SidecarIdentity(sandbox_id=sandbox_id)


class MtlsAuthenticator:
    """Production. Reads the verified client cert; sandbox_id is its CN/SAN.

    The TLS layer (uvicorn ssl_cert_reqs=CERT_REQUIRED + ssl_ca_certs=<our CA>)
    has already verified the chain; here we just extract sandbox_id from the
    peer certificate surfaced on the request.
    """

    def __init__(self, *, sandbox_id_field: str = "CN") -> None:
        self._field = sandbox_id_field

    async def verify(self, request: Any) -> SidecarIdentity:
        cert = getattr(request, "client_cert", None)
        if not cert:
            raise PermissionError("no client certificate")
        sandbox_id = cert.get(self._field) if isinstance(cert, dict) else None
        if not sandbox_id:
            raise PermissionError(f"client cert missing {self._field}")
        return SidecarIdentity(sandbox_id=sandbox_id)


def build_sidecar_authenticator(
    config: dict[str, Any], *, deployment_mode: str
) -> SidecarAuthenticator:
    mode = config.get("mode", "mtls")
    if mode == "dev":
        if deployment_mode == "production":
            raise RuntimeError(
                "egress exchange dev authenticator selected in production deployment mode"
            )
        token = config.get("dev_token")
        if not token:
            raise RuntimeError("dev authenticator requires dev_token")
        return DevSharedSecretAuthenticator(token=token)
    if mode == "mtls":
        return MtlsAuthenticator(sandbox_id_field=config.get("sandbox_id_field", "CN"))
    raise RuntimeError(f"unknown egress exchange auth mode: {mode!r}")
```

> NOTE for implementer: how the verified client cert is surfaced on the FastAPI/Starlette `Request` depends on the ASGI server config (uvicorn exposes peercert via the transport). Wire `MtlsAuthenticator.verify` to read it from `request.scope["transport"]`/`request.scope.get("client_cert")` per the actual uvicorn TLS setup chosen in Plan 3; the `client_cert` attribute here is the test seam.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_egress_exchange_auth.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cubeplex/sandbox_env/exchange_auth.py tests/unit/test_egress_exchange_auth.py
git commit -m "feat(egress): pluggable sidecar authenticator (mtls + dev)"
```

---

## Task 6: `EgressExchangeService`

**Files:**
- Create: `cubeplex/services/egress_exchange.py`
- Test: `tests/unit/test_egress_exchange_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_egress_exchange_service.py
from collections.abc import AsyncIterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubeplex.credentials.encryption import FernetBackend
from cubeplex.models import EgressRef
from cubeplex.repositories.credential import CredentialRepository
from cubeplex.repositories.egress_ref import EgressRefRepository
from cubeplex.sandbox_env.exchange_auth import SidecarIdentity
from cubeplex.sandbox_env.placeholder import hash_placeholder, mint_placeholder
from cubeplex.services.credential import CredentialService
from cubeplex.services.egress_exchange import (
    EgressExchangeError,
    EgressExchangeService,
)
from cubeplex.services.sandbox_env import SANDBOX_ENV_KIND


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _seed(session):
    backend = FernetBackend([Fernet.generate_key()])
    cred = CredentialService(
        CredentialRepository(session, org_id="org-1"), backend,
        org_id="org-1", actor_user_id="u1",
    )
    cred_id = await cred.create(kind=SANDBOX_ENV_KIND, name="t", plaintext="ghp_real")
    placeholder = mint_placeholder()
    await EgressRefRepository(session).add(
        EgressRef(
            ref_hash=hash_placeholder(placeholder), sandbox_id="sbx-1", org_id="org-1",
            workspace_id="ws-1", user_id="u1", run_id="run-1",
            bindings=[{"ref_hash": hash_placeholder(placeholder), "env_name": "GITHUB_TOKEN",
                       "hosts": ["api.github.com"], "header_names": None, "credential_id": cred_id}],
        )
    )
    svc = EgressExchangeService(
        ref_repo=EgressRefRepository(session),
        credentials_factory=lambda org_id: CredentialService(
            CredentialRepository(session, org_id=org_id), backend,
            org_id=org_id, actor_user_id=None,
        ),
    )
    return svc, placeholder


async def test_exchange_returns_secret_for_matching_sandbox_and_host(session):
    svc, placeholder = await _seed(session)
    secret = await svc.exchange(
        identity=SidecarIdentity(sandbox_id="sbx-1"), placeholder=placeholder,
        host="api.github.com",
    )
    assert secret == "ghp_real"


async def test_rejects_sandbox_id_mismatch(session):
    svc, placeholder = await _seed(session)
    with pytest.raises(EgressExchangeError):
        await svc.exchange(
            identity=SidecarIdentity(sandbox_id="sbx-OTHER"), placeholder=placeholder,
            host="api.github.com",
        )


async def test_rejects_non_declared_host(session):
    svc, placeholder = await _seed(session)
    with pytest.raises(EgressExchangeError):
        await svc.exchange(
            identity=SidecarIdentity(sandbox_id="sbx-1"), placeholder=placeholder,
            host="api.attacker.net",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_egress_exchange_service.py -v`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# cubeplex/services/egress_exchange.py
"""Exchange a placeholder for its real secret, for a verified sidecar only."""

from __future__ import annotations

from collections.abc import Callable

from cubeplex.repositories.egress_ref import EgressRefRepository
from cubeplex.sandbox_env.exchange_auth import SidecarIdentity
from cubeplex.sandbox_env.host_rules import host_matches
from cubeplex.sandbox_env.placeholder import hash_placeholder
from cubeplex.services.credential import CredentialService
from cubeplex.services.sandbox_env import SANDBOX_ENV_KIND


class EgressExchangeError(Exception):
    """Any failure to resolve a placeholder; callers must fail closed."""


class EgressExchangeService:
    def __init__(
        self,
        *,
        ref_repo: EgressRefRepository,
        credentials_factory: Callable[[str], CredentialService],
    ) -> None:
        self._refs = ref_repo
        self._credentials_factory = credentials_factory

    async def exchange(
        self, *, identity: SidecarIdentity, placeholder: str, host: str
    ) -> str:
        ref = await self._refs.get_valid_by_hash(hash_placeholder(placeholder))
        if ref is None:
            raise EgressExchangeError("unknown/revoked/expired placeholder")
        if ref.sandbox_id != identity.sandbox_id:
            raise EgressExchangeError("sandbox_id mismatch")
        host_norm = host.lower().split(":", 1)[0]
        binding = next(
            (b for b in ref.bindings if host_matches(host_norm, b["hosts"])), None
        )
        if binding is None:
            raise EgressExchangeError(f"host {host_norm!r} not allowed for this placeholder")
        creds = self._credentials_factory(ref.org_id)
        return await creds.get_decrypted(
            credential_id=binding["credential_id"], requesting_kind=SANDBOX_ENV_KIND
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_egress_exchange_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cubeplex/services/egress_exchange.py tests/unit/test_egress_exchange_service.py
git commit -m "feat(egress): EgressExchangeService (verify + host match + decrypt)"
```

---

## Task 7: Internal exchange route

A standalone control-plane router (NOT under `/api/v1/ws` or `/admin`). Authenticated by the `SidecarAuthenticator`, not a user session.

**Files:**
- Create: `cubeplex/api/routes/internal_egress.py`
- Modify: `cubeplex/api/app.py`
- Test: `tests/e2e/test_internal_egress_route.py`

- [ ] **Step 1: Implement the router**

```python
# cubeplex/api/routes/internal_egress.py
"""Internal egress secret-exchange endpoint (sidecar-authenticated)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.credentials.dependencies import get_encryption_backend
from cubeplex.credentials.encryption import EncryptionBackend
from cubeplex.db import get_session
from cubeplex.repositories.credential import CredentialRepository
from cubeplex.repositories.egress_ref import EgressRefRepository
from cubeplex.sandbox_env.exchange_auth import SidecarAuthenticator
from cubeplex.services.credential import CredentialService
from cubeplex.services.egress_exchange import EgressExchangeError, EgressExchangeService

router = APIRouter(prefix="/internal/egress", tags=["internal-egress"])


class ExchangeIn(BaseModel):
    placeholder: str
    host: str


class ExchangeOut(BaseModel):
    secret: str


def get_sidecar_authenticator(request: Request) -> SidecarAuthenticator:
    # Built once at startup and stored on app.state (see app.py wiring).
    return request.app.state.sidecar_authenticator


@router.post("/exchange", response_model=ExchangeOut)
async def exchange(
    body: ExchangeIn,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
    authenticator: Annotated[SidecarAuthenticator, Depends(get_sidecar_authenticator)],
) -> ExchangeOut:
    try:
        identity = await authenticator.verify(request)
    except PermissionError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sidecar auth failed") from exc

    svc = EgressExchangeService(
        ref_repo=EgressRefRepository(session),
        credentials_factory=lambda org_id: CredentialService(
            CredentialRepository(session, org_id=org_id), backend,
            org_id=org_id, actor_user_id=None,
        ),
    )
    try:
        secret = await svc.exchange(
            identity=identity, placeholder=body.placeholder, host=body.host
        )
    except EgressExchangeError as exc:
        # Fail closed; do not leak which check failed.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "exchange denied") from exc
    return ExchangeOut(secret=secret)
```

- [ ] **Step 2: Wire authenticator + router in `app.py`**

In `cubeplex/api/app.py`, during app construction build the authenticator from config and stash it, then include the router:

```python
from cubeplex.sandbox_env.exchange_auth import build_sidecar_authenticator
from cubeplex.api.routes import internal_egress

app.state.sidecar_authenticator = build_sidecar_authenticator(
    config.get("egress_exchange.auth", {}),
    deployment_mode=config.get("deployment.mode", "production"),
)
app.include_router(internal_egress.router, prefix="/api/v1")
```

> NOTE for implementer: match the real config accessor (`config.get(...)` style used elsewhere in app.py) and the real deployment-mode key (grep `single_tenant`/`multi_tenant`/`deployment` in `cubeplex/config*`). The guardrail in `build_sidecar_authenticator` will raise at startup if `dev` is configured under a production mode — that is intended.

- [ ] **Step 3: e2e test (dev authenticator path)**

```python
# tests/e2e/test_internal_egress_route.py
# Relies on the app being built with the dev authenticator in the test config
# (egress_exchange.auth.mode=dev, dev_token=...). Seed an EgressRef + credential
# via the DB session fixture, then call the endpoint with the dev headers.
# Grep tests/e2e/conftest.py for the app/client fixture (e.g. `client`) and the
# db_session fixture; assert:
#   - correct dev token + matching sandbox-id + declared host -> 200, secret returned
#   - wrong dev token -> 401
#   - mismatched sandbox-id -> 403
#   - non-declared host -> 403
```

> NOTE for implementer: this e2e needs the test app configured with the dev authenticator. Add `egress_exchange.auth: {mode: dev, dev_token: "test-egress"}` to the test config the e2e harness loads (grep how `tests/e2e/conftest.py` builds the app/config), and `deployment.mode` must be non-production in tests so the guardrail allows it.

- [ ] **Step 4: Commit**

```bash
git add cubeplex/api/routes/internal_egress.py cubeplex/api/app.py tests/e2e/test_internal_egress_route.py
git commit -m "feat(egress): internal sidecar-authenticated exchange endpoint"
```

---

## Task 8: Wire injection into the sandbox manager

Build env + policy + bindings before `Sandbox.create`; persist `EgressRef` (with the now-known `sandbox_id`) after; revoke prior refs when recreating.

**Files:**
- Modify: `cubeplex/sandbox/manager.py` (the create path, ~lines 145-181)
- Test: covered by Plan 3 cluster E2E + a targeted unit test of the assembly helper (Task 4 already covers `build`). Add one manager-level test that asserts `Sandbox.create` is called with env+policy and an `EgressRef` is persisted (mock the SDK).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_manager_egress_injection.py
# Construct SandboxManager with a fake opensandbox.Sandbox.create (monkeypatched)
# that records kwargs and returns an object with .id = "sbx-new".
# Provide a stub resolver returning one secret + one plain ResolvedEnv.
# Assert: create called with env containing a cbxref_ placeholder + the plain
# value, network_policy non-empty; and an EgressRef row persisted with
# sandbox_id="sbx-new" and status "valid".
#
# NOTE for implementer: SandboxManager construction needs its existing deps
# (session_factory, config). Reuse the manager unit-test setup if one exists
# (grep tests/ for "SandboxManager("); otherwise build it with the same args
# main.py / DI uses. Keep the SDK mocked — this is a wiring test, not a real
# sandbox.
```

- [ ] **Step 2: Run it (fails — injection not wired)**

Run: `uv run pytest tests/unit/test_manager_egress_injection.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the wiring**

In `cubeplex/sandbox/manager.py`, inside `get_or_create` just before the `opensandbox.Sandbox.create(...)` call (the create-new path), resolve + build:

```python
from datetime import UTC, datetime, timedelta  # timedelta likely already imported at top

from cubeplex.models import EgressRef
from cubeplex.repositories.egress_ref import EgressRefRepository
from cubeplex.repositories.sandbox_env import SandboxEnvRepository
from cubeplex.sandbox_env.injector import SandboxEnvInjector
from cubeplex.services.sandbox_env import SandboxEnvResolver

# ... within the create-new branch, with `session`, `repo` already in scope:
resolver = SandboxEnvResolver(SandboxEnvRepository(session, org_id=org_id))
resolved = await resolver.resolve(workspace_id=workspace_id, user_id=user_id)
injection = SandboxEnvInjector(exchange_host=self._exchange_host).build(resolved)

raw_sandbox = await opensandbox.Sandbox.create(
    self._image,
    connection_config=conn_config,
    timeout=None,
    ready_timeout=timedelta(seconds=self._ready_timeout),
    volumes=volumes,
    resource={"cpu": self._resource_cpu, "memory": self._resource_memory},
    env=injection.env,
    network_policy=injection.network_policy,
)

# Persist refs now that sandbox_id is known. expires_at is bounded by the
# sandbox TTL so a leaked placeholder cannot be redeemed indefinitely even if
# explicit revocation is missed (Codex P2).
ref_repo = EgressRefRepository(session)
expires_at = datetime.now(UTC) + timedelta(seconds=self._ttl)
for b in injection.bindings:
    await ref_repo.add(
        EgressRef(
            ref_hash=b["ref_hash"], sandbox_id=raw_sandbox.id, org_id=org_id,
            workspace_id=workspace_id, user_id=user_id, run_id=None,
            bindings=[b], expires_at=expires_at,
        )
    )
```

**Revoke the OLD sandbox's refs when abandoning it (Codex P1).** In the
reuse/unhealthy branch, at the point the manager already calls
`await repo.mark_terminated(record.id)` for an unhealthy/unreachable sandbox,
also revoke that sandbox's refs so its placeholders stop being redeemable even
if the old pod/sidecar is still alive:

```python
# right next to the existing `await repo.mark_terminated(record.id)`:
await EgressRefRepository(session).revoke_for_sandbox(record.sandbox_id)
```

Add `self._exchange_host = config.get("sandbox.egress_exchange_host", "")` in `__init__` (read from the same config object the manager already uses). `self._ttl` is the existing sandbox TTL field (config `sandbox.ttl`). When `_exchange_host` is empty (egress disabled), skip injection and call `Sandbox.create` without `env`/`network_policy` (preserve current behavior).

> NOTE for implementer: gate the whole block on `if self._exchange_host:` so deployments without the egress feature behave exactly as today. Confirm `user_id` is in scope in the create branch (the method signature has it). The per-run refresh-on-reuse refinement (spec §6.5) is deferred: v1 mints refs at sandbox creation; wiring a run-start refresh hook is a follow-up once the run/turn boundary calls into the manager (grep where `get_or_create` is invoked at turn start).

- [ ] **Step 4: Run the test (passes)**

Run: `uv run pytest tests/unit/test_manager_egress_injection.py -v`
Expected: PASS.

- [ ] **Step 5: Full check + commit**

Run: `uv run pytest tests/unit/test_egress_*.py tests/e2e/test_internal_egress_route.py -v && uv run mypy cubeplex/ && uv run ruff check cubeplex/`
Expected: all PASS, no type/lint issues.

```bash
git add cubeplex/sandbox/manager.py tests/unit/test_manager_egress_injection.py
git commit -m "feat(egress): inject env vault + policy + refs at sandbox creation"
```

---

## Self-Review Checklist (completed by plan author)

- **Spec coverage:** §4.1 authenticator (Task 5, mtls + dev + prod guardrail); §6.5 EgressRef + vault-driven injection + network_policy assembly + per-run revoke (Tasks 2/4/8); §6.6 exchange with sandbox_id + host checks (Tasks 6/7); placeholder format (Task 1, matches spec §6.2).
- **Type consistency:** `ResolvedEnv` consumed exactly as Plan 1 defines it (Task 4); `SidecarIdentity` shared Task 5↔6↔7; `hash_placeholder`/`PLACEHOLDER_RE` consistent across Tasks 1/4/6; `EgressRef.bindings` shape `{ref_hash,env_name,hosts,header_names,credential_id}` consistent Task 4↔6↔8.
- **Flagged implementer confirmations (lookups, not gaps):** exact `opensandbox.models.sandboxes` enum member names; how uvicorn surfaces the verified client cert on the request (Plan 3 wires the real TLS); the manager's config accessor + deployment-mode key + turn-start call site for the deferred per-run refresh.
- **Deferred to Plan 3:** the webhook/mTLS/CA/addon that call this exchange endpoint; the real per-sandbox client cert that `MtlsAuthenticator` reads.

## Next

Plan 3 (`docs/dev/plans/2026-05-25-egress-k8s-bundle.md`): the cubeplex-owned K8s bundle — mutating webhook (per-sandbox mTLS mint + initContainer CA + addon mount), fixed-CA Secret, the `inject.py` mitmproxy addon (token scan + header_names + host match calling this exchange endpoint), and the real-cluster E2E.
