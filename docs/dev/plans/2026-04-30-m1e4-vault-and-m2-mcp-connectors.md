# M1-E4 Credential Vault + M2 MCP Connectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build internal Credential Vault (M1-E4 minimal) + DB-backed MCP Connectors (M2 admin + workspace member double entry, 4 credential scopes × static/oauth/none, workspace-private + org-wide visibility).

**Architecture:** Vault is `EncryptionBackend` Protocol + Fernet/MultiFernet CE impl + `CredentialService` internal-only API. MCP has 4 SQL tables (servers + workspace creds + user creds + bindings), per-(workspace, user) tool assembly inside `RunManager`, runtime-time JWT signer for passthrough mode, and tool list cached on admin-save.

**Tech Stack:** SQLModel + Alembic + FastAPI + cryptography (Fernet) + PyJWT + langchain-mcp-adapters + Next.js + shadcn/ui + Zustand + Playwright.

**Spec:** `docs/superpowers/specs/2026-04-30-m1e4-vault-and-m2-mcp-connectors-design.md`

**Working directory:** This plan executes in a dedicated worktree (user creates separately). Verify before starting:

```bash
git rev-parse --abbrev-ref HEAD          # → feat/m2-mcp-connectors (or similar)
cat .worktree.env                         # confirm allocated DB/Redis/ports
./scripts/worktree-env doctor             # verify everything reachable
```

---

## Review Corrections Before Implementation

These corrections override earlier draft assumptions and must be applied while executing tasks:

- Runtime wiring happens in `backend/cubeplex/streams/run_manager.py`, not in
  `backend/cubeplex/api/routes/v1/conversations.py`. The conversations route only starts a
  background run and passes `RunContext`.
- The FastAPI app file is `backend/cubeplex/api/app.py`, not `backend/cubeplex/app.py`.
- `RequestContext` is a dataclass with `user: User`, `org_id`, `workspace_id`, and `role`.
  Use `ctx.user.id`, not `ctx.user_id`. For admin routes without a workspace path, resolve
  `org_id` with `resolve_current_org_id(user, session)`.
- `CredentialService` should not require a FastAPI `RequestContext` object. It is also used
  from `RunManager`, where only ids are available. Construct it with `org_id` and
  `actor_user_id`; route dependencies can pass `ctx.org_id` and `ctx.user.id`.
- There is no `get_request_context` dependency. Workspace routes should depend on
  `require_member`; admin routes should depend on `require_org_admin` plus explicit org
  resolution where service construction needs `org_id`.
- Keep `create_cubeplex_agent()` synchronous and close to its current signature. Append DB MCP
  tools to the `tools` list before calling it from `RunManager`; do not pass DB sessions,
  credential services, or signers into the agent factory.
- Legacy config MCP tools remain global startup tools through `cubeplex.tools.init_mcp_tools()`.
  DB-backed MCP tools are run-scoped and must not be registered globally.
- Frontend API helpers must follow existing core patterns: `ApiClient` methods return `Response`,
  so helpers must check `res.ok`, call `toApiError(res)`, and parse `res.json()`.
- `ApiClient` currently has no `put()` method. Add it before using PUT endpoints.
- There is no `useApiClient()` hook in `frontend/packages/web`. Pages should use
  `useMemo(() => createApiClient(''), [])`, and workspace pages must call
  `client.setWorkspaceId(wsId)`.
- Playwright E2E files live under `frontend/packages/web/__tests__/e2e`, because
  `frontend/playwright.config.ts` sets `testDir` there.
- Do not leave pseudo-code identifiers such as `createdServerId` or `serverId` in E2E tests.
  Create data through the browser flow or explicit `page.request` calls in the test.

## Phase A · Vault Foundation (Stage 1)

### Task 1: Verify dependencies

**Files:**
- Modify: `backend/pyproject.toml` (only if a dep is missing)

- [ ] **Step 1: Check existing deps**

```bash
cd backend
grep -E "cryptography|pyjwt|uuid-utils" pyproject.toml
```

Expected: `cryptography` and `uuid-utils` already present (used by fastapi-users / existing models). PyJWT may or may not be direct (fastapi-users pulls it transitively).

- [ ] **Step 2: Add PyJWT as direct dep if not listed**

```bash
uv add pyjwt
```

(If already direct: skip; this command is idempotent for already-pinned deps but keeps the explicit declaration.)

- [ ] **Step 3: Verify import**

```bash
uv run python -c "import jwt; from cryptography.fernet import Fernet, MultiFernet; from uuid_utils import uuid7; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Commit (only if pyproject changed)**

```bash
git add backend/pyproject.toml backend/uv.lock
git commit -m "chore(deps): add pyjwt direct dep for vault user-token signer"
```

---

### Task 2: EncryptionBackend Protocol + FernetBackend

**Files:**
- Create: `backend/cubeplex/credentials/__init__.py`
- Create: `backend/cubeplex/credentials/encryption.py`
- Create: `backend/tests/unit/test_fernet_rotation.py`

- [ ] **Step 1: Write failing unit test**

```python
# backend/tests/unit/test_fernet_rotation.py
"""Unit tests for Fernet encryption + MultiFernet rotation."""

import pytest
from cryptography.fernet import Fernet, InvalidToken

from cubeplex.credentials.encryption import FernetBackend


@pytest.fixture
def k1() -> bytes:
    return Fernet.generate_key()


@pytest.fixture
def k2() -> bytes:
    return Fernet.generate_key()


async def test_roundtrip_single_key(k1: bytes) -> None:
    backend = FernetBackend([k1])
    plaintext = b"super-secret-token"
    ciphertext = await backend.encrypt(plaintext)
    assert ciphertext != plaintext
    assert await backend.decrypt(ciphertext) == plaintext


async def test_rotation_decrypts_old_ciphertext_with_new_key_first(
    k1: bytes, k2: bytes
) -> None:
    """Encrypt with k1, then add k2 as new primary; old ciphertext must still decrypt."""
    old = FernetBackend([k1])
    cipher_old = await old.encrypt(b"hello")

    rotated = FernetBackend([k2, k1])
    assert await rotated.decrypt(cipher_old) == b"hello"


async def test_unknown_key_fails(k1: bytes, k2: bytes) -> None:
    old = FernetBackend([k1])
    cipher = await old.encrypt(b"hello")
    other = FernetBackend([k2])
    with pytest.raises(InvalidToken):
        await other.decrypt(cipher)


def test_empty_keys_rejected() -> None:
    with pytest.raises(ValueError, match="at least one"):
        FernetBackend([])
```

- [ ] **Step 2: Run, expect fail (module missing)**

```bash
uv run pytest tests/unit/test_fernet_rotation.py -v
```

Expected: FAIL with `ModuleNotFoundError: cubeplex.credentials.encryption`

- [ ] **Step 3: Create `__init__.py`**

```python
# backend/cubeplex/credentials/__init__.py
"""Credential vault — internal-only key/secret storage for MCP and future consumers."""
```

- [ ] **Step 4: Implement `encryption.py`**

```python
# backend/cubeplex/credentials/encryption.py
"""Symmetric authenticated encryption with pluggable backend.

CE default: Fernet (AES-128-CBC + HMAC-SHA256) via the cryptography library,
with MultiFernet for zero-downtime key rotation. EE may register a KMS-backed
EncryptionBackend without changing CredentialService callers.
"""

from typing import Protocol

from cryptography.fernet import Fernet, MultiFernet


class EncryptionBackend(Protocol):
    async def encrypt(self, plaintext: bytes) -> bytes: ...
    async def decrypt(self, ciphertext: bytes) -> bytes: ...


class FernetBackend:
    """CE default: Fernet + MultiFernet rotation. keys[0] encrypts; all decrypt."""

    def __init__(self, keys: list[bytes]) -> None:
        if not keys:
            raise ValueError("FernetBackend requires at least one key")
        self._fernet = MultiFernet([Fernet(k) for k in keys])

    async def encrypt(self, plaintext: bytes) -> bytes:
        return self._fernet.encrypt(plaintext)

    async def decrypt(self, ciphertext: bytes) -> bytes:
        return self._fernet.decrypt(ciphertext)
```

- [ ] **Step 5: Run, expect pass**

```bash
uv run pytest tests/unit/test_fernet_rotation.py -v
```

Expected: 4 tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/credentials/__init__.py \
        backend/cubeplex/credentials/encryption.py \
        backend/tests/unit/test_fernet_rotation.py
git commit -m "feat(vault): add EncryptionBackend Protocol + Fernet impl with rotation"
```

---

### Task 3: Master key loading + fail-fast

**Files:**
- Modify: `backend/cubeplex/config.py` (add VAULT_KEY parsing)
- Create: `backend/cubeplex/credentials/keys.py` (key parsing helper)
- Create: `backend/tests/unit/test_vault_key_loading.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/unit/test_vault_key_loading.py
"""Vault master key parsing + validation."""

import pytest
from cryptography.fernet import Fernet

from cubeplex.credentials.keys import parse_vault_keys


def test_parses_single_key() -> None:
    k = Fernet.generate_key().decode()
    parsed = parse_vault_keys(k)
    assert len(parsed) == 1
    assert parsed[0] == k.encode()


def test_parses_comma_separated() -> None:
    k1 = Fernet.generate_key().decode()
    k2 = Fernet.generate_key().decode()
    parsed = parse_vault_keys(f"{k1},{k2}")
    assert parsed == [k1.encode(), k2.encode()]


def test_strips_whitespace() -> None:
    k = Fernet.generate_key().decode()
    parsed = parse_vault_keys(f"  {k}  ")
    assert parsed == [k.encode()]


def test_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        parse_vault_keys("")


def test_invalid_key_raises() -> None:
    with pytest.raises(ValueError, match="invalid"):
        parse_vault_keys("not-a-fernet-key")


def test_one_invalid_in_list_raises() -> None:
    k = Fernet.generate_key().decode()
    with pytest.raises(ValueError):
        parse_vault_keys(f"{k},garbage")
```

- [ ] **Step 2: Run, expect fail**

```bash
uv run pytest tests/unit/test_vault_key_loading.py -v
```

Expected: FAIL `ModuleNotFoundError: cubeplex.credentials.keys`

- [ ] **Step 3: Implement `keys.py`**

```python
# backend/cubeplex/credentials/keys.py
"""Parse and validate CUBEPLEX_AUTH__VAULT_KEY env value."""

from cryptography.fernet import Fernet


def parse_vault_keys(raw: str) -> list[bytes]:
    """Parse comma-separated url-safe base64 Fernet keys. First is encryption-primary.

    Raises ValueError on empty input or any malformed key.
    """
    if not raw or not raw.strip():
        raise ValueError("CUBEPLEX_AUTH__VAULT_KEY is empty")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise ValueError("CUBEPLEX_AUTH__VAULT_KEY is empty after split")
    keys: list[bytes] = []
    for idx, p in enumerate(parts):
        try:
            Fernet(p.encode())  # constructor validates length + base64
        except Exception as e:
            raise ValueError(f"CUBEPLEX_AUTH__VAULT_KEY entry #{idx} invalid: {e}") from e
        keys.append(p.encode())
    return keys
```

- [ ] **Step 4: Run, expect pass**

```bash
uv run pytest tests/unit/test_vault_key_loading.py -v
```

Expected: 6 pass.

- [ ] **Step 5: Wire fail-fast into app startup**

Read current `backend/cubeplex/api/app.py` to find startup hook (lifespan or `on_startup`). Append vault key load:

```python
# backend/cubeplex/api/app.py — inside lifespan / startup function

from cubeplex.credentials.encryption import FernetBackend
from cubeplex.credentials.keys import parse_vault_keys


def _build_encryption_backend() -> FernetBackend:
    raw = os.getenv("CUBEPLEX_AUTH__VAULT_KEY") or settings.get("auth.vault_key")
    if not raw:
        raise RuntimeError(
            "CUBEPLEX_AUTH__VAULT_KEY is required. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
    keys = parse_vault_keys(raw)  # raises ValueError on bad keys
    return FernetBackend(keys)
```

Invoke `_build_encryption_backend()` at startup; store on `app.state.encryption_backend` for DI later.

- [ ] **Step 6: Add vault key entry to `.env.example`**

```bash
# backend/.env.example
# Vault master key — generate with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Production: rotate by setting CUBEPLEX_AUTH__VAULT_KEY=<new>,<old> then run
#   python -m cubeplex.credentials.rotate_keys
CUBEPLEX_AUTH__VAULT_KEY=
```

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/credentials/keys.py \
        backend/cubeplex/api/app.py \
        backend/.env.example \
        backend/tests/unit/test_vault_key_loading.py
git commit -m "feat(vault): load Fernet keys from CUBEPLEX_AUTH__VAULT_KEY at startup"
```

---

### Task 4: Credential SQLModel + Alembic migration

**Files:**
- Create: `backend/cubeplex/models/credential.py`
- Modify: `backend/cubeplex/models/__init__.py`
- Create: `backend/alembic/versions/<hash>_add_credentials_table.py` (autogen)

- [ ] **Step 1: Define model**

```python
# backend/cubeplex/models/credential.py
"""Credential — vault row, org-scoped, kind-tagged."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class Credential(SQLModel, table=True):
    """Vault entry. v1 only kind='mcp_server'; future kinds extend without schema change."""

    __tablename__ = "credentials"
    __table_args__ = (UniqueConstraint("org_id", "kind", "name", name="uq_credential_org_kind_name"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)
    kind: str = Field(max_length=32)
    name: str = Field(max_length=128)
    value_encrypted: bytes
    cred_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_by_user_id: str = Field(max_length=36)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

- [ ] **Step 2: Register in models package**

```python
# backend/cubeplex/models/__init__.py — add import
from cubeplex.models.credential import Credential
```

(Append to alphabetical block; ensure exported.)

- [ ] **Step 3: Generate Alembic migration**

```bash
cd backend
uv run alembic revision --autogenerate -m "add credentials table"
```

- [ ] **Step 4: Review the autogenerated file**

Open `backend/alembic/versions/<latest>_add_credentials_table.py`. Verify:
- `op.create_table('credentials', ...)` with all columns
- `value_encrypted` as `LargeBinary`
- `cred_metadata` as `JSON`
- Unique constraint on `(org_id, kind, name)`
- Index on `org_id`

If any column was generated as wrong type (e.g., `value_encrypted` as `String`), edit manually.

- [ ] **Step 5: Apply + verify**

```bash
uv run alembic upgrade head
```

Verify with psql:

```bash
psql ... -c "\d credentials"
```

Expected: table exists with the listed columns.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/models/credential.py \
        backend/cubeplex/models/__init__.py \
        backend/alembic/versions/*_add_credentials_table.py
git commit -m "feat(vault): add credentials table"
```

---

### Task 5: CredentialRepository

**Files:**
- Create: `backend/cubeplex/repositories/credential.py`

- [ ] **Step 1: Implement (no tests yet — covered by service E2E in Task 7)**

```python
# backend/cubeplex/repositories/credential.py
"""Credential repository — org-scoped (no workspace dim)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import Credential


class CredentialRepository:
    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(self, credential_id: str) -> Credential | None:
        stmt = select(Credential).where(
            Credential.id == credential_id,  # type: ignore[arg-type]
            Credential.org_id == self.org_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add(self, cred: Credential) -> Credential:
        cred.org_id = self.org_id
        self.session.add(cred)
        await self.session.commit()
        await self.session.refresh(cred)
        return cred

    async def update(self, cred: Credential) -> Credential:
        from datetime import UTC, datetime
        cred.updated_at = datetime.now(UTC)
        self.session.add(cred)
        await self.session.commit()
        await self.session.refresh(cred)
        return cred

    async def delete(self, credential_id: str) -> None:
        cred = await self.get(credential_id)
        if cred is None:
            return
        await self.session.delete(cred)
        await self.session.commit()
```

- [ ] **Step 2: Commit**

```bash
git add backend/cubeplex/repositories/credential.py
git commit -m "feat(vault): add CredentialRepository (org-scoped, no workspace dim)"
```

---

### Task 6: CredentialService + exceptions

**Files:**
- Create: `backend/cubeplex/credentials/exceptions.py`
- Create: `backend/cubeplex/services/credential.py`

- [ ] **Step 1: Define exceptions**

```python
# backend/cubeplex/credentials/exceptions.py
"""Vault domain exceptions."""


class CredentialNotFound(Exception):
    """Credential id does not exist or is in a different org."""


class CredentialKindMismatch(Exception):
    """Caller's `requesting_kind` does not match credential's stored kind."""


class CredentialInUseError(Exception):
    """Credential cannot be deleted because some other row references it."""
```

- [ ] **Step 2: Implement service (without delete-in-use check — Task 24 wires it)**

```python
# backend/cubeplex/services/credential.py
"""Vault service — internal API for backend consumers (MCP, future skill env, etc.).

Never expose plaintext outside backend; never expose CRUD HTTP routes.
"""

from typing import Any

from cubeplex.credentials.encryption import EncryptionBackend
from cubeplex.credentials.exceptions import (
    CredentialKindMismatch,
    CredentialNotFound,
)
from cubeplex.models import Credential
from cubeplex.repositories.credential import CredentialRepository


class CredentialService:
    def __init__(
        self,
        repo: CredentialRepository,
        backend: EncryptionBackend,
        *,
        org_id: str,
        actor_user_id: str,
    ) -> None:
        self._repo = repo
        self._backend = backend
        self._org_id = org_id
        self._actor_user_id = actor_user_id

    async def create(
        self, *, kind: str, name: str, plaintext: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        ciphertext = await self._backend.encrypt(plaintext.encode("utf-8"))
        cred = Credential(
            org_id=self._org_id,
            kind=kind,
            name=name,
            value_encrypted=ciphertext,
            cred_metadata=metadata or {},
            created_by_user_id=self._actor_user_id,
        )
        cred = await self._repo.add(cred)
        return cred.id

    async def get_decrypted(
        self, *, credential_id: str, requesting_kind: str,
    ) -> str:
        cred = await self._repo.get(credential_id)
        if cred is None:
            raise CredentialNotFound(credential_id)
        if cred.kind != requesting_kind:
            raise CredentialKindMismatch(
                f"credential kind={cred.kind} but caller requested kind={requesting_kind}"
            )
        plaintext = await self._backend.decrypt(cred.value_encrypted)
        return plaintext.decode("utf-8")

    async def update(
        self, *, credential_id: str,
        plaintext: str | None = None,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        cred = await self._repo.get(credential_id)
        if cred is None:
            raise CredentialNotFound(credential_id)
        if plaintext is not None:
            cred.value_encrypted = await self._backend.encrypt(plaintext.encode("utf-8"))
        if name is not None:
            cred.name = name
        if metadata is not None:
            cred.cred_metadata = metadata
        await self._repo.update(cred)

    async def delete(self, *, credential_id: str) -> None:
        # Reverse-reference check is wired in Task 24 once MCP repos exist.
        cred = await self._repo.get(credential_id)
        if cred is None:
            raise CredentialNotFound(credential_id)
        await self._repo.delete(credential_id)
```

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/credentials/exceptions.py \
        backend/cubeplex/services/credential.py
git commit -m "feat(vault): add CredentialService with kind/org invariants"
```

---

### Task 7: Vault E2E (cross-org isolation, kind mismatch)

**Files:**
- Create: `backend/tests/e2e/test_credentials_vault.py`

- [ ] **Step 1: Write E2E**

```python
# backend/tests/e2e/test_credentials_vault.py
"""Vault E2E — exercises CredentialService through real DB + Fernet."""

import pytest
from cryptography.fernet import Fernet

from cubeplex.credentials.encryption import FernetBackend
from cubeplex.credentials.exceptions import (
    CredentialKindMismatch,
    CredentialNotFound,
)
from cubeplex.repositories.credential import CredentialRepository
from cubeplex.services.credential import CredentialService


@pytest.fixture
def backend() -> FernetBackend:
    return FernetBackend([Fernet.generate_key()])


async def test_roundtrip_decrypts_correct_kind(db_session, backend) -> None:
    repo = CredentialRepository(db_session, org_id="org-A")
    svc = CredentialService(repo, backend, org_id="org-A", actor_user_id="user-1")

    cred_id = await svc.create(
        kind="mcp_server", name="GitHub PAT", plaintext="ghp_abcXYZ"
    )
    plain = await svc.get_decrypted(credential_id=cred_id, requesting_kind="mcp_server")
    assert plain == "ghp_abcXYZ"


async def test_kind_mismatch_raises(db_session, backend) -> None:
    repo = CredentialRepository(db_session, org_id="org-A")
    svc = CredentialService(repo, backend, org_id="org-A", actor_user_id="user-1")
    cred_id = await svc.create(kind="mcp_server", name="x", plaintext="secret")

    with pytest.raises(CredentialKindMismatch):
        await svc.get_decrypted(credential_id=cred_id, requesting_kind="skill_env")


async def test_cross_org_returns_not_found(db_session, backend) -> None:
    repo_A = CredentialRepository(db_session, org_id="org-A")
    svc_A = CredentialService(repo_A, backend, org_id="org-A", actor_user_id="user-1")
    cred_id = await svc_A.create(kind="mcp_server", name="x", plaintext="secret")

    repo_B = CredentialRepository(db_session, org_id="org-B")
    svc_B = CredentialService(repo_B, backend, org_id="org-B", actor_user_id="user-1")
    with pytest.raises(CredentialNotFound):
        await svc_B.get_decrypted(credential_id=cred_id, requesting_kind="mcp_server")


async def test_update_replaces_ciphertext(db_session, backend) -> None:
    repo = CredentialRepository(db_session, org_id="org-A")
    svc = CredentialService(repo, backend, org_id="org-A", actor_user_id="user-1")
    cred_id = await svc.create(kind="mcp_server", name="x", plaintext="old")
    await svc.update(credential_id=cred_id, plaintext="new")
    assert (await svc.get_decrypted(credential_id=cred_id, requesting_kind="mcp_server")) == "new"


async def test_delete_removes_row(db_session, backend) -> None:
    repo = CredentialRepository(db_session, org_id="org-A")
    svc = CredentialService(repo, backend, org_id="org-A", actor_user_id="user-1")
    cred_id = await svc.create(kind="mcp_server", name="x", plaintext="secret")
    await svc.delete(credential_id=cred_id)
    with pytest.raises(CredentialNotFound):
        await svc.get_decrypted(credential_id=cred_id, requesting_kind="mcp_server")
```

(`db_session` fixture: use existing one in `backend/tests/conftest.py`. If not present, see how E2E tests in `tests/e2e/test_skills_*.py` open a session — replicate.)

- [ ] **Step 2: Run, expect pass**

```bash
uv run pytest tests/e2e/test_credentials_vault.py -v
```

Expected: 5 pass.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_credentials_vault.py
git commit -m "test(vault): E2E for create/get/update/delete + kind mismatch + cross-org"
```

---

## Phase B · MCP DB Layer (Stage 2)

### Task 8: MCP SQLModels (4 tables)

**Files:**
- Create: `backend/cubeplex/models/mcp.py`
- Modify: `backend/cubeplex/models/__init__.py`

- [ ] **Step 1: Define all 4 models**

```python
# backend/cubeplex/models/mcp.py
"""MCP connector tables — server registry + per-workspace creds + per-user creds + bindings."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class MCPServer(SQLModel, table=True):
    """MCP server registration. owner_workspace_id NULL = org-wide; else workspace-private."""

    __tablename__ = "mcp_servers"
    __table_args__ = (
        UniqueConstraint("org_id", "owner_workspace_id", "server_url_hash",
                         name="uq_mcp_server_url"),
        UniqueConstraint("org_id", "owner_workspace_id", "name",
                         name="uq_mcp_server_name"),
    )

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)
    owner_workspace_id: str | None = Field(default=None, max_length=36, index=True)
    name: str = Field(max_length=64)
    server_url: str = Field(max_length=2048)
    server_url_hash: str = Field(max_length=64)  # sha256(server_url) lowercase hex
    transport: str = Field(max_length=16)        # "streamable_http" | "sse" | "stdio"
    auth_method: str = Field(max_length=16)      # "static" | "oauth" | "none"
    credential_scope: str = Field(max_length=16) # "org" | "workspace" | "user" | "none"
    credential_id: str | None = Field(default=None, max_length=36)
    oauth_client_config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    headers: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    tools_cache: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    authed: bool = Field(default=False)
    last_error: str | None = Field(default=None, max_length=2048)
    last_discovered_at: datetime | None = None
    timeout: float = Field(default=30.0)
    sse_read_timeout: float = Field(default=300.0)
    created_by_user_id: str = Field(max_length=36)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WorkspaceMCPCredential(SQLModel, table=True):
    """credential_scope=workspace: one row per (workspace using server)."""

    __tablename__ = "workspace_mcp_credentials"
    __table_args__ = (
        UniqueConstraint("workspace_id", "mcp_server_id",
                         name="uq_ws_mcp_cred"),
    )

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)
    workspace_id: str = Field(max_length=36, index=True)
    mcp_server_id: str = Field(max_length=36, index=True)
    credential_id: str = Field(max_length=36)
    created_by_user_id: str = Field(max_length=36)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class UserMCPCredential(SQLModel, table=True):
    """credential_scope=user: one row per (user, server)."""

    __tablename__ = "user_mcp_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "mcp_server_id", name="uq_user_mcp_cred"),
    )

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)
    user_id: str = Field(max_length=36, index=True)
    mcp_server_id: str = Field(max_length=36, index=True)
    credential_id: str = Field(max_length=36)
    oauth_refresh_token_credential_id: str | None = Field(default=None, max_length=36)
    oauth_expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WorkspaceMCPBinding(SQLModel, table=True):
    """org-wide server × workspace visibility. workspace-private servers do NOT use this."""

    __tablename__ = "workspace_mcp_bindings"
    __table_args__ = (
        UniqueConstraint("workspace_id", "mcp_server_id", name="uq_ws_mcp_binding"),
    )

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)
    workspace_id: str = Field(max_length=36, index=True)
    mcp_server_id: str = Field(max_length=36, index=True)
    enabled: bool = Field(default=True)
    created_by_user_id: str = Field(max_length=36)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

- [ ] **Step 2: Register in models package**

```python
# backend/cubeplex/models/__init__.py — add imports
from cubeplex.models.mcp import (
    MCPServer,
    UserMCPCredential,
    WorkspaceMCPBinding,
    WorkspaceMCPCredential,
)
```

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/models/mcp.py backend/cubeplex/models/__init__.py
git commit -m "feat(mcp): add 4 SQLModels for server/binding/cred tables"
```

---

### Task 9: Alembic migration for MCP tables

**Files:**
- Create: `backend/alembic/versions/<hash>_add_mcp_connector_tables.py` (autogen)

- [ ] **Step 1: Generate**

```bash
cd backend
uv run alembic revision --autogenerate -m "add mcp connector tables"
```

- [ ] **Step 2: Review autogenerated file**

Open `backend/alembic/versions/<latest>_add_mcp_connector_tables.py`. Verify:
- 4 `op.create_table(...)` calls (mcp_servers, workspace_mcp_credentials, user_mcp_credentials, workspace_mcp_bindings)
- Unique constraints match `__table_args__`
- JSON columns generated as `sa.JSON()` (not VARCHAR)
- `tools_cache` / `oauth_client_config` / `headers` are JSON
- Indexes on org_id / workspace_id / user_id / mcp_server_id present

If autogen used `String` for columns that should be `LargeBinary` (none in MCP, but double-check), or missed indexes, edit manually.

- [ ] **Step 3: Apply**

```bash
uv run alembic upgrade head
```

- [ ] **Step 4: Verify schema**

```bash
psql ... -c "\d mcp_servers"
psql ... -c "\d workspace_mcp_credentials"
psql ... -c "\d user_mcp_credentials"
psql ... -c "\d workspace_mcp_bindings"
```

Expected: all 4 tables with declared columns + indexes.

- [ ] **Step 5: Test downgrade**

```bash
uv run alembic downgrade -1
psql ... -c "\d mcp_servers" 2>&1 | grep -q "did not find" && echo OK
uv run alembic upgrade head
```

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/*_add_mcp_connector_tables.py
git commit -m "feat(mcp): alembic migration for 4 connector tables"
```

---

### Task 10: MCP repositories

**Files:**
- Create: `backend/cubeplex/repositories/mcp.py`

- [ ] **Step 1: Implement all 4 repos in one file**

```python
# backend/cubeplex/repositories/mcp.py
"""MCP connector repositories.

All 4 repos are org-scoped. We do NOT use ScopedRepository because mcp_servers
has no workspace_id (it has owner_workspace_id which is nullable and semantically
distinct). All filters are explicit.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import (
    MCPServer,
    UserMCPCredential,
    WorkspaceMCPBinding,
    WorkspaceMCPCredential,
)


class MCPServerRepository:
    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(self, server_id: str) -> MCPServer | None:
        stmt = select(MCPServer).where(
            MCPServer.id == server_id,  # type: ignore[arg-type]
            MCPServer.org_id == self.org_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_org(
        self, *, owner_workspace_id: str | None | type[Ellipsis] = ...,
    ) -> list[MCPServer]:
        """List all org servers; optional filter on owner_workspace_id (None = org-wide only)."""
        stmt = select(MCPServer).where(MCPServer.org_id == self.org_id)  # type: ignore[arg-type]
        if owner_workspace_id is not Ellipsis:
            stmt = stmt.where(MCPServer.owner_workspace_id == owner_workspace_id)  # type: ignore[arg-type]
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_for_workspace(self, workspace_id: str) -> list[MCPServer]:
        """Owned by workspace + org-wide bound to workspace (enabled). authed=true filter."""
        # owned
        owned_stmt = select(MCPServer).where(
            MCPServer.org_id == self.org_id,  # type: ignore[arg-type]
            MCPServer.owner_workspace_id == workspace_id,  # type: ignore[arg-type]
            MCPServer.authed == True,  # noqa: E712
        )
        owned = list((await self.session.execute(owned_stmt)).scalars().all())
        # bound
        bound_stmt = (
            select(MCPServer)
            .join(WorkspaceMCPBinding, MCPServer.id == WorkspaceMCPBinding.mcp_server_id)  # type: ignore[arg-type]
            .where(
                MCPServer.org_id == self.org_id,  # type: ignore[arg-type]
                MCPServer.owner_workspace_id.is_(None),  # type: ignore[union-attr]
                MCPServer.authed == True,  # noqa: E712
                WorkspaceMCPBinding.workspace_id == workspace_id,  # type: ignore[arg-type]
                WorkspaceMCPBinding.enabled == True,  # noqa: E712
            )
        )
        bound = list((await self.session.execute(bound_stmt)).scalars().all())
        return owned + bound

    async def add(self, server: MCPServer) -> MCPServer:
        server.org_id = self.org_id
        self.session.add(server)
        await self.session.commit()
        await self.session.refresh(server)
        return server

    async def update(self, server: MCPServer) -> MCPServer:
        from datetime import UTC, datetime
        server.updated_at = datetime.now(UTC)
        self.session.add(server)
        await self.session.commit()
        await self.session.refresh(server)
        return server

    async def delete(self, server_id: str) -> None:
        server = await self.get(server_id)
        if server is None:
            return
        await self.session.delete(server)
        await self.session.commit()

    async def find_by_url_hash(
        self, *, owner_workspace_id: str | None, server_url_hash: str,
    ) -> MCPServer | None:
        stmt = select(MCPServer).where(
            MCPServer.org_id == self.org_id,  # type: ignore[arg-type]
            MCPServer.owner_workspace_id == owner_workspace_id,  # type: ignore[arg-type]
            MCPServer.server_url_hash == server_url_hash,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def find_by_credential_id(self, credential_id: str) -> list[MCPServer]:
        """Reverse-ref check for vault delete."""
        stmt = select(MCPServer).where(
            MCPServer.org_id == self.org_id,  # type: ignore[arg-type]
            MCPServer.credential_id == credential_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())


class WorkspaceMCPCredentialRepository:
    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(
        self, *, workspace_id: str, mcp_server_id: str,
    ) -> WorkspaceMCPCredential | None:
        stmt = select(WorkspaceMCPCredential).where(
            WorkspaceMCPCredential.org_id == self.org_id,  # type: ignore[arg-type]
            WorkspaceMCPCredential.workspace_id == workspace_id,  # type: ignore[arg-type]
            WorkspaceMCPCredential.mcp_server_id == mcp_server_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add(self, row: WorkspaceMCPCredential) -> WorkspaceMCPCredential:
        row.org_id = self.org_id
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def delete(self, *, workspace_id: str, mcp_server_id: str) -> None:
        row = await self.get(workspace_id=workspace_id, mcp_server_id=mcp_server_id)
        if row is None:
            return
        await self.session.delete(row)
        await self.session.commit()

    async def list_for_server(self, mcp_server_id: str) -> list[WorkspaceMCPCredential]:
        stmt = select(WorkspaceMCPCredential).where(
            WorkspaceMCPCredential.org_id == self.org_id,  # type: ignore[arg-type]
            WorkspaceMCPCredential.mcp_server_id == mcp_server_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def find_by_credential_id(self, credential_id: str) -> list[WorkspaceMCPCredential]:
        stmt = select(WorkspaceMCPCredential).where(
            WorkspaceMCPCredential.org_id == self.org_id,  # type: ignore[arg-type]
            WorkspaceMCPCredential.credential_id == credential_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())


class UserMCPCredentialRepository:
    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(
        self, *, user_id: str, mcp_server_id: str,
    ) -> UserMCPCredential | None:
        stmt = select(UserMCPCredential).where(
            UserMCPCredential.org_id == self.org_id,  # type: ignore[arg-type]
            UserMCPCredential.user_id == user_id,  # type: ignore[arg-type]
            UserMCPCredential.mcp_server_id == mcp_server_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add(self, row: UserMCPCredential) -> UserMCPCredential:
        row.org_id = self.org_id
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def delete(self, *, user_id: str, mcp_server_id: str) -> None:
        row = await self.get(user_id=user_id, mcp_server_id=mcp_server_id)
        if row is None:
            return
        await self.session.delete(row)
        await self.session.commit()

    async def list_for_server(self, mcp_server_id: str) -> list[UserMCPCredential]:
        stmt = select(UserMCPCredential).where(
            UserMCPCredential.org_id == self.org_id,  # type: ignore[arg-type]
            UserMCPCredential.mcp_server_id == mcp_server_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def find_by_credential_id(self, credential_id: str) -> list[UserMCPCredential]:
        stmt = select(UserMCPCredential).where(
            UserMCPCredential.org_id == self.org_id,  # type: ignore[arg-type]
            UserMCPCredential.credential_id == credential_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())


class WorkspaceMCPBindingRepository:
    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(
        self, *, workspace_id: str, mcp_server_id: str,
    ) -> WorkspaceMCPBinding | None:
        stmt = select(WorkspaceMCPBinding).where(
            WorkspaceMCPBinding.org_id == self.org_id,  # type: ignore[arg-type]
            WorkspaceMCPBinding.workspace_id == workspace_id,  # type: ignore[arg-type]
            WorkspaceMCPBinding.mcp_server_id == mcp_server_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_server(self, mcp_server_id: str) -> list[WorkspaceMCPBinding]:
        stmt = select(WorkspaceMCPBinding).where(
            WorkspaceMCPBinding.org_id == self.org_id,  # type: ignore[arg-type]
            WorkspaceMCPBinding.mcp_server_id == mcp_server_id,  # type: ignore[arg-type]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def add(self, row: WorkspaceMCPBinding) -> WorkspaceMCPBinding:
        row.org_id = self.org_id
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def delete(self, *, workspace_id: str, mcp_server_id: str) -> None:
        row = await self.get(workspace_id=workspace_id, mcp_server_id=mcp_server_id)
        if row is None:
            return
        await self.session.delete(row)
        await self.session.commit()

    async def upsert_bulk(
        self, *, mcp_server_id: str,
        bindings: list[tuple[str, bool]],  # [(workspace_id, enabled), ...]
        created_by_user_id: str,
    ) -> None:
        """Replace bindings list for a server. Existing rows updated; missing ones deleted."""
        existing = {r.workspace_id: r for r in await self.list_for_server(mcp_server_id)}
        incoming = {ws_id: enabled for ws_id, enabled in bindings}
        # update / insert
        from datetime import UTC, datetime
        for ws_id, enabled in incoming.items():
            row = existing.get(ws_id)
            if row is None:
                row = WorkspaceMCPBinding(
                    org_id=self.org_id, workspace_id=ws_id,
                    mcp_server_id=mcp_server_id, enabled=enabled,
                    created_by_user_id=created_by_user_id,
                )
                self.session.add(row)
            else:
                row.enabled = enabled
                row.updated_at = datetime.now(UTC)
                self.session.add(row)
        # delete bindings no longer in incoming
        for ws_id, row in existing.items():
            if ws_id not in incoming:
                await self.session.delete(row)
        await self.session.commit()
```

- [ ] **Step 2: Commit**

```bash
git add backend/cubeplex/repositories/mcp.py
git commit -m "feat(mcp): add 4 repositories (server / ws-cred / user-cred / binding)"
```

---

## Phase C · MCP Service & Runtime Helpers (Stage 3)

### Task 11: MCP exceptions

**Files:**
- Create: `backend/cubeplex/mcp/exceptions.py`

- [ ] **Step 1: Define domain exceptions**

```python
# backend/cubeplex/mcp/exceptions.py
"""MCP domain exceptions. Each maps to a specific HTTP error code in routes."""


class MCPServerNotFound(Exception): ...


class MCPServerURLConflict(Exception): ...


class MCPServerNameConflict(Exception): ...


class MCPCredentialRequired(Exception):
    """credential_scope=org/workspace requires plaintext credential."""


class MCPUserScopeCredentialForbidden(Exception):
    """credential_scope=user/none must NOT carry plaintext credential."""


class MCPOAuthNotImplemented(Exception):
    """auth_method=oauth is reserved enum but not implemented in v1."""


class MCPServerNotOwnedByWorkspace(Exception):
    """ws path attempted to mutate a server with different owner_workspace_id."""


class MCPWorkspaceOwnedNoBinding(Exception):
    """Bindings table only accepts org-wide servers (owner_workspace_id IS NULL)."""


class MCPServerAlreadyOrgWide(Exception):
    """promote called on a server already org-wide."""


class MCPShareCredentialOnlyForWorkspaceScope(Exception):
    """share_credential is only meaningful for credential_scope=workspace."""


class MCPCredentialPathMismatch(Exception):
    """user-credential / workspace-credential route used on wrong-scope server."""
```

- [ ] **Step 2: Commit**

```bash
git add backend/cubeplex/mcp/exceptions.py
git commit -m "feat(mcp): add domain exceptions"
```

---

### Task 12: MCPUserTokenSigner + HS256Signer + unit test

**Files:**
- Create: `backend/cubeplex/mcp/user_token.py`
- Create: `backend/tests/unit/test_user_token_signer.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/unit/test_user_token_signer.py
"""Unit tests for MCP passthrough JWT signer."""

from datetime import timedelta

import jwt
import pytest

from cubeplex.mcp.user_token import HS256Signer


@pytest.fixture
def signer() -> HS256Signer:
    return HS256Signer(secret="test-secret-please-rotate")


async def test_sign_returns_decodable_jwt(signer: HS256Signer) -> None:
    token = await signer.sign(
        user_id="u1", org_id="o1", workspace_id="w1",
        mcp_server_id="m1", ttl=timedelta(minutes=5),
    )
    decoded = jwt.decode(token, "test-secret-please-rotate", algorithms=["HS256"])
    assert decoded["sub"] == "u1"
    assert decoded["org"] == "o1"
    assert decoded["ws"] == "w1"
    assert decoded["mcp"] == "m1"
    assert decoded["iss"] == "cubeplex"
    assert "exp" in decoded


async def test_sign_with_wrong_secret_fails_verification(signer: HS256Signer) -> None:
    token = await signer.sign(
        user_id="u1", org_id="o1", workspace_id="w1",
        mcp_server_id="m1", ttl=timedelta(minutes=5),
    )
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, "wrong-secret", algorithms=["HS256"])


async def test_sign_zero_ttl_already_expired(signer: HS256Signer) -> None:
    token = await signer.sign(
        user_id="u1", org_id="o1", workspace_id="w1",
        mcp_server_id="m1", ttl=timedelta(seconds=-1),
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(token, "test-secret-please-rotate", algorithms=["HS256"])
```

- [ ] **Step 2: Run, expect fail**

```bash
uv run pytest tests/unit/test_user_token_signer.py -v
```

- [ ] **Step 3: Implement signer**

```python
# backend/cubeplex/mcp/user_token.py
"""MCP passthrough mode: sign cubeplex identity into a short-TTL JWT."""

from datetime import UTC, datetime, timedelta
from typing import Protocol

import jwt


class MCPUserTokenSigner(Protocol):
    async def sign(
        self, *, user_id: str, org_id: str, workspace_id: str,
        mcp_server_id: str, ttl: timedelta,
    ) -> str: ...


class HS256Signer:
    """v1 CE — HS256 with shared CUBEPLEX_AUTH__JWT_SECRET.

    EE / hardened deployments may register an RS256Signer that exposes
    /.well-known/cubeplex-jwks.json.
    """

    def __init__(self, secret: str) -> None:
        self._secret = secret

    async def sign(
        self, *, user_id: str, org_id: str, workspace_id: str,
        mcp_server_id: str, ttl: timedelta,
    ) -> str:
        now = datetime.now(UTC)
        claims = {
            "sub": user_id,
            "org": org_id,
            "ws": workspace_id,
            "mcp": mcp_server_id,
            "exp": int((now + ttl).timestamp()),
            "iat": int(now.timestamp()),
            "iss": "cubeplex",
        }
        return jwt.encode(claims, self._secret, algorithm="HS256")
```

- [ ] **Step 4: Run, expect pass**

```bash
uv run pytest tests/unit/test_user_token_signer.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/mcp/user_token.py \
        backend/tests/unit/test_user_token_signer.py
git commit -m "feat(mcp): add HS256 user-token signer for passthrough auth"
```

---

### Task 13: Connection params builder + unit test

**Files:**
- Create: `backend/cubeplex/mcp/connection_params.py`
- Create: `backend/tests/unit/test_connection_params.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/unit/test_connection_params.py
"""Unit tests for MCP connection params dispatch."""

import pytest

from cubeplex.mcp.connection_params import build_connection_params
from cubeplex.models import MCPServer


def _server(**overrides) -> MCPServer:
    base = dict(
        org_id="o", name="t", server_url="https://x", server_url_hash="h",
        transport="streamable_http", auth_method="static",
        credential_scope="org", credential_id="c",
        created_by_user_id="u",
    )
    base.update(overrides)
    return MCPServer(**base)


def test_streamable_http_with_static_token() -> None:
    s = _server(transport="streamable_http", credential_scope="org")
    params = build_connection_params(s, credential_or_token="ghp_xxx")
    assert params["url"] == "https://x"
    assert params["transport"] == "streamable_http"
    assert params["headers"] == {"Authorization": "Bearer ghp_xxx"}


def test_sse_with_static_token() -> None:
    s = _server(transport="sse")
    params = build_connection_params(s, credential_or_token="tok")
    assert params["transport"] == "sse"
    assert params["headers"]["Authorization"] == "Bearer tok"


def test_stdio_credential_passed_via_env() -> None:
    s = _server(
        transport="stdio",
        server_url="mcp-server-cli --foo",
        headers={"env_var_for_token": "GITHUB_TOKEN"},
    )
    params = build_connection_params(s, credential_or_token="ghp_xxx")
    assert params["transport"] == "stdio"
    assert params["env"]["GITHUB_TOKEN"] == "ghp_xxx"


def test_none_scope_no_auth_header() -> None:
    s = _server(transport="streamable_http", auth_method="none", credential_scope="none",
                credential_id=None)
    params = build_connection_params(s, credential_or_token=None)
    assert "Authorization" not in params.get("headers", {})


def test_user_passthrough_uses_jwt_in_header() -> None:
    s = _server(credential_scope="user", credential_id=None)
    params = build_connection_params(s, credential_or_token="<jwt-token>")
    assert params["headers"]["Authorization"] == "Bearer <jwt-token>"


def test_custom_headers_merged() -> None:
    s = _server(headers={"X-Custom": "v"})
    params = build_connection_params(s, credential_or_token="tok")
    assert params["headers"]["X-Custom"] == "v"
    assert params["headers"]["Authorization"] == "Bearer tok"


def test_unknown_transport_raises() -> None:
    s = _server(transport="something_weird")
    with pytest.raises(ValueError, match="unsupported transport"):
        build_connection_params(s, credential_or_token="x")
```

- [ ] **Step 2: Run, expect fail**

```bash
uv run pytest tests/unit/test_connection_params.py -v
```

- [ ] **Step 3: Implement**

```python
# backend/cubeplex/mcp/connection_params.py
"""Build MultiServerMCPClient connection params from a DB MCPServer + resolved credential.

`credential_or_token` is either:
- The decrypted plaintext credential (for credential_scope in {org, workspace, user-static})
- A signed cubeplex JWT (for credential_scope=none — passthrough mode)
- None (for credential_scope=none with auth_method=none — server expects no auth)
"""

from typing import Any

from cubeplex.models import MCPServer

_HTTP_TRANSPORTS = {"streamable_http", "sse"}


def build_connection_params(
    server: MCPServer, *, credential_or_token: str | None,
) -> dict[str, Any]:
    """Build MultiServerMCPClient params dict for one server."""
    if server.transport in _HTTP_TRANSPORTS:
        return _http_params(server, credential_or_token)
    if server.transport == "stdio":
        return _stdio_params(server, credential_or_token)
    raise ValueError(f"unsupported transport '{server.transport}'")


def _http_params(server: MCPServer, token: str | None) -> dict[str, Any]:
    headers = dict(server.headers or {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params: dict[str, Any] = {
        "url": server.server_url,
        "transport": server.transport,
    }
    if headers:
        params["headers"] = headers
    return params


def _stdio_params(server: MCPServer, token: str | None) -> dict[str, Any]:
    """server_url for stdio is "<cmd> [args...]"; token (if any) goes via env."""
    cmd_parts = server.server_url.split()
    if not cmd_parts:
        raise ValueError("stdio server_url must contain command")
    params: dict[str, Any] = {
        "command": cmd_parts[0],
        "args": cmd_parts[1:],
        "transport": "stdio",
    }
    env_var = server.headers.get("env_var_for_token") if server.headers else None
    if token and env_var:
        params["env"] = {env_var: token}
    return params
```

- [ ] **Step 4: Run, expect pass**

```bash
uv run pytest tests/unit/test_connection_params.py -v
```

Expected: 7 pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/mcp/connection_params.py \
        backend/tests/unit/test_connection_params.py
git commit -m "feat(mcp): add connection params builder dispatching by transport + auth"
```

---

### Task 14: Discovery + tool serialize + unit test

**Files:**
- Create: `backend/cubeplex/mcp/discovery.py`
- Create: `backend/tests/unit/test_discovery_serialize.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/unit/test_discovery_serialize.py
"""Unit tests for tool serialize round-trip."""

from langchain_core.tools import StructuredTool

from cubeplex.mcp.discovery import construct_basetools_from_cache, serialize_tool


def _dummy_tool(name: str, description: str) -> StructuredTool:
    def _fn(query: str) -> str:
        return f"echo: {query}"

    return StructuredTool.from_function(
        func=_fn, name=name, description=description,
    )


def test_serialize_returns_dict_with_required_fields() -> None:
    t = _dummy_tool("echo", "Echoes input")
    blob = serialize_tool(t)
    assert blob["name"] == "echo"
    assert blob["description"] == "Echoes input"
    assert "input_schema" in blob


def test_construct_returns_basetools_with_correct_metadata() -> None:
    cache = [
        {"name": "a", "description": "tool a",
         "input_schema": {"type": "object", "properties": {"q": {"type": "string"}},
                          "required": ["q"]}},
        {"name": "b", "description": "tool b",
         "input_schema": {"type": "object", "properties": {}, "required": []}},
    ]
    params = {"url": "https://srv", "transport": "streamable_http"}
    tools = construct_basetools_from_cache(cache, params)
    assert len(tools) == 2
    assert {t.name for t in tools} == {"a", "b"}
```

- [ ] **Step 2: Run, expect fail**

```bash
uv run pytest tests/unit/test_discovery_serialize.py -v
```

- [ ] **Step 3: Implement**

```python
# backend/cubeplex/mcp/discovery.py
"""MCP server discovery: connect, list tools, serialize for tools_cache.

Runtime BaseTool construction reverses the serialize: each cached tool definition
becomes a StructuredTool whose ainvoke opens a fresh MCP session via the cached
connection params.
"""

import json
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import Connection
from loguru import logger

from cubeplex.mcp.connection_params import build_connection_params
from cubeplex.models import MCPServer


async def discover_tools(
    server: MCPServer, *, credential_or_token: str | None,
) -> tuple[bool, list[dict[str, Any]] | None, str | None]:
    """Connect, list tools, return (success, serialized_tools, error_msg).

    On failure, returns (False, None, error_str). Caller persists last_error.
    """
    try:
        params = build_connection_params(server, credential_or_token=credential_or_token)
    except ValueError as e:
        return False, None, f"params build failed: {e}"

    try:
        client = MultiServerMCPClient({server.name: cast_to_connection(params)})
        raw_tools: list[BaseTool] = await client.get_tools()
        return True, [serialize_tool(t) for t in raw_tools], None
    except Exception as e:
        if isinstance(e, BaseExceptionGroup):
            causes = "; ".join(str(sub) for sub in e.exceptions)
            return False, None, f"{e}; causes: {causes}"
        return False, None, str(e)


def cast_to_connection(params: dict[str, Any]) -> Connection:
    return params  # type: ignore[return-value]  # langchain_mcp_adapters Connection is a TypedDict


def serialize_tool(tool: BaseTool) -> dict[str, Any]:
    """Extract name / description / input_schema as JSON-safe dict."""
    schema: dict[str, Any] = {}
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is not None:
        # pydantic v2 model
        if hasattr(args_schema, "model_json_schema"):
            schema = args_schema.model_json_schema()
        elif hasattr(args_schema, "schema"):
            schema = args_schema.schema()
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": schema,
    }


def construct_basetools_from_cache(
    cache: list[dict[str, Any]], connection_params: dict[str, Any],
) -> list[BaseTool]:
    """Reverse of serialize: build BaseTools whose ainvoke calls back into MCP per-call."""
    tools: list[BaseTool] = []
    for entry in cache:
        try:
            tools.append(_build_basetool_for_entry(entry, connection_params))
        except Exception as e:
            logger.warning("MCP cache entry '{}' deserialization failed: {}",
                           entry.get("name"), e)
    return tools


def _build_basetool_for_entry(
    entry: dict[str, Any], connection_params: dict[str, Any],
) -> BaseTool:
    name = entry["name"]
    description = entry.get("description", "")
    input_schema = entry.get("input_schema", {"type": "object", "properties": {}})

    async def _ainvoke(**kwargs: Any) -> Any:
        client = MultiServerMCPClient({name: cast_to_connection(connection_params)})
        # langchain-mcp-adapters: call_tool opens session, invokes, closes.
        return await client.call_tool(name, kwargs)

    # StructuredTool.from_function expects a sync function; use coroutine variant
    return StructuredTool.from_function(
        func=lambda **kwargs: _sync_wrapper(_ainvoke, kwargs),
        coroutine=_ainvoke,
        name=name,
        description=description,
        args_schema=_dict_to_pydantic(name, input_schema),
    )


def _sync_wrapper(coro_factory, kwargs):
    """Sync stub — should never be hit because LangChain prefers coroutine."""
    raise RuntimeError("MCP tools must be invoked via ainvoke")


def _dict_to_pydantic(name: str, schema: dict[str, Any]) -> Any:
    """Build a pydantic model from a JSON schema dict for args validation.

    For v1 we accept anything (no strict validation) so a permissive model suffices.
    Future enhancement: full JSON schema → pydantic conversion.
    """
    from pydantic import BaseModel, ConfigDict

    class _Permissive(BaseModel):
        model_config = ConfigDict(extra="allow")

    _Permissive.__name__ = f"{name}Args"
    return _Permissive
```

- [ ] **Step 4: Run, expect pass**

```bash
uv run pytest tests/unit/test_discovery_serialize.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/mcp/discovery.py \
        backend/tests/unit/test_discovery_serialize.py
git commit -m "feat(mcp): add discovery + tool serialize/deserialize"
```

---

### Task 15: MCPServerService — create + invariants

**Files:**
- Create: `backend/cubeplex/services/mcp.py`
- Create: `backend/tests/unit/test_mcp_service_invariants.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/unit/test_mcp_service_invariants.py
"""Unit tests for MCPServerService invariant enforcement.

Uses real DB session (via fixture) but no real MCP server — discovery is mocked
with a stub that returns success or failure as the test directs.
"""

import pytest

from cubeplex.mcp.exceptions import (
    MCPCredentialRequired,
    MCPOAuthNotImplemented,
    MCPServerNameConflict,
    MCPServerURLConflict,
    MCPUserScopeCredentialForbidden,
)


async def test_create_org_scope_requires_credential(mcp_service) -> None:
    with pytest.raises(MCPCredentialRequired):
        await mcp_service.create(
            name="x", server_url="https://a", transport="streamable_http",
            auth_method="static", credential_scope="org",
            credential_plaintext=None,
        )


async def test_create_user_scope_rejects_credential(mcp_service) -> None:
    with pytest.raises(MCPUserScopeCredentialForbidden):
        await mcp_service.create(
            name="x", server_url="https://a", transport="streamable_http",
            auth_method="static", credential_scope="user",
            credential_plaintext="should-not-be-here",
        )


async def test_create_oauth_v1_rejected(mcp_service) -> None:
    with pytest.raises(MCPOAuthNotImplemented):
        await mcp_service.create(
            name="x", server_url="https://a", transport="streamable_http",
            auth_method="oauth", credential_scope="org",
            credential_plaintext="x",
        )


async def test_duplicate_url_in_same_scope_conflicts(mcp_service) -> None:
    await mcp_service.create(
        name="a", server_url="https://x", transport="streamable_http",
        auth_method="none", credential_scope="none",
    )
    with pytest.raises(MCPServerURLConflict):
        await mcp_service.create(
            name="b", server_url="https://x", transport="streamable_http",
            auth_method="none", credential_scope="none",
        )


async def test_duplicate_name_in_same_scope_conflicts(mcp_service) -> None:
    await mcp_service.create(
        name="dup", server_url="https://a", transport="streamable_http",
        auth_method="none", credential_scope="none",
    )
    with pytest.raises(MCPServerNameConflict):
        await mcp_service.create(
            name="dup", server_url="https://b", transport="streamable_http",
            auth_method="none", credential_scope="none",
        )
```

(`mcp_service` fixture: see Step 2 — define in `backend/tests/conftest.py` or a new `tests/unit/conftest.py`.)

- [ ] **Step 2: Add `mcp_service` fixture to `tests/unit/conftest.py`**

```python
# backend/tests/unit/conftest.py — append (create file if missing)
import pytest
from cryptography.fernet import Fernet

from cubeplex.auth.context import RequestContext
from cubeplex.credentials.encryption import FernetBackend
from cubeplex.repositories.credential import CredentialRepository
from cubeplex.repositories.mcp import (
    MCPServerRepository,
    UserMCPCredentialRepository,
    WorkspaceMCPBindingRepository,
    WorkspaceMCPCredentialRepository,
)
from cubeplex.services.credential import CredentialService
from cubeplex.services.mcp import MCPServerService


@pytest.fixture
def request_context() -> RequestContext:
    return RequestContext(
        user_id="u1", org_id="org-test", workspace_id="ws-test", role="admin"
    )


@pytest.fixture
def encryption_backend() -> FernetBackend:
    return FernetBackend([Fernet.generate_key()])


@pytest.fixture
async def cred_service(db_session, encryption_backend, request_context):
    repo = CredentialRepository(db_session, org_id=request_context.org_id)
    return CredentialService(repo, encryption_backend, request_context)


@pytest.fixture
async def mcp_service(db_session, cred_service, request_context):
    server_repo = MCPServerRepository(db_session, org_id=request_context.org_id)
    ws_cred_repo = WorkspaceMCPCredentialRepository(db_session, org_id=request_context.org_id)
    user_cred_repo = UserMCPCredentialRepository(db_session, org_id=request_context.org_id)
    binding_repo = WorkspaceMCPBindingRepository(db_session, org_id=request_context.org_id)
    return MCPServerService(
        server_repo=server_repo,
        ws_cred_repo=ws_cred_repo,
        user_cred_repo=user_cred_repo,
        binding_repo=binding_repo,
        cred_service=cred_service,
        request_context=request_context,
    )
```

- [ ] **Step 3: Run, expect fail (service missing)**

```bash
uv run pytest tests/unit/test_mcp_service_invariants.py -v
```

- [ ] **Step 4: Implement service skeleton + create**

```python
# backend/cubeplex/services/mcp.py
"""MCP connector service — CRUD + invariants + promote + cred mgmt.

discover_tools is invoked from create / refresh paths; failures yield
authed=False rather than raising. Caller decides what HTTP code to return.
"""

import hashlib

from cubeplex.auth.context import RequestContext
from cubeplex.mcp.discovery import discover_tools
from cubeplex.mcp.exceptions import (
    MCPCredentialPathMismatch,
    MCPCredentialRequired,
    MCPOAuthNotImplemented,
    MCPServerAlreadyOrgWide,
    MCPServerNameConflict,
    MCPServerNotFound,
    MCPServerNotOwnedByWorkspace,
    MCPServerURLConflict,
    MCPShareCredentialOnlyForWorkspaceScope,
    MCPUserScopeCredentialForbidden,
    MCPWorkspaceOwnedNoBinding,
)
from cubeplex.models import (
    MCPServer,
    UserMCPCredential,
    WorkspaceMCPBinding,
    WorkspaceMCPCredential,
)
from cubeplex.repositories.mcp import (
    MCPServerRepository,
    UserMCPCredentialRepository,
    WorkspaceMCPBindingRepository,
    WorkspaceMCPCredentialRepository,
)
from cubeplex.services.credential import CredentialService

_VALID_SCOPES = {"org", "workspace", "user", "none"}
_VALID_METHODS = {"static", "oauth", "none"}
_HTTP_TRANSPORTS = {"streamable_http", "sse", "stdio"}
_CREDENTIAL_KIND_MCP = "mcp_server"


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class MCPServerService:
    def __init__(
        self,
        server_repo: MCPServerRepository,
        ws_cred_repo: WorkspaceMCPCredentialRepository,
        user_cred_repo: UserMCPCredentialRepository,
        binding_repo: WorkspaceMCPBindingRepository,
        cred_service: CredentialService,
        request_context: RequestContext,
    ) -> None:
        self.server_repo = server_repo
        self.ws_cred_repo = ws_cred_repo
        self.user_cred_repo = user_cred_repo
        self.binding_repo = binding_repo
        self.cred_service = cred_service
        self._ctx = request_context

    async def create(
        self, *, name: str, server_url: str, transport: str,
        auth_method: str, credential_scope: str,
        credential_plaintext: str | None = None,
        credential_name: str | None = None,
        owner_workspace_id: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0, sse_read_timeout: float = 300.0,
    ) -> MCPServer:
        self._validate_create_invariants(
            transport=transport, auth_method=auth_method,
            credential_scope=credential_scope,
            credential_plaintext=credential_plaintext,
            owner_workspace_id=owner_workspace_id,
        )
        url_hash = _sha256_hex(server_url)
        # url uniqueness within (org, owner_ws)
        if await self.server_repo.find_by_url_hash(
            owner_workspace_id=owner_workspace_id, server_url_hash=url_hash,
        ):
            raise MCPServerURLConflict(server_url)
        # name uniqueness within (org, owner_ws) — check via list_for_org filter
        for existing in await self.server_repo.list_for_org(
            owner_workspace_id=owner_workspace_id,
        ):
            if existing.name == name:
                raise MCPServerNameConflict(name)

        # Persist credential (if applicable) → get credential_id
        credential_id: str | None = None
        if credential_scope == "org":
            credential_id = await self.cred_service.create(
                kind=_CREDENTIAL_KIND_MCP,
                name=credential_name or f"mcp:{name}:org",
                plaintext=credential_plaintext or "",  # invariants ensure non-None
            )

        server = MCPServer(
            org_id=self._ctx.org_id,
            owner_workspace_id=owner_workspace_id,
            name=name, server_url=server_url, server_url_hash=url_hash,
            transport=transport, auth_method=auth_method,
            credential_scope=credential_scope, credential_id=credential_id,
            headers=headers or {}, timeout=timeout, sse_read_timeout=sse_read_timeout,
            created_by_user_id=self._ctx.user_id,
        )
        server = await self.server_repo.add(server)

        # workspace scope: insert into workspace_mcp_credentials
        if credential_scope == "workspace":
            assert credential_plaintext is not None  # invariant
            assert owner_workspace_id is not None
            ws_cred_id = await self.cred_service.create(
                kind=_CREDENTIAL_KIND_MCP,
                name=credential_name or f"mcp:{name}:ws:{owner_workspace_id}",
                plaintext=credential_plaintext,
            )
            await self.ws_cred_repo.add(WorkspaceMCPCredential(
                org_id=self._ctx.org_id,
                workspace_id=owner_workspace_id,
                mcp_server_id=server.id,
                credential_id=ws_cred_id,
                created_by_user_id=self._ctx.user_id,
            ))

        # discovery (best-effort)
        await self._refresh_tools_for_server(server)
        return await self.server_repo.get(server.id) or server

    def _validate_create_invariants(
        self, *, transport: str, auth_method: str,
        credential_scope: str, credential_plaintext: str | None,
        owner_workspace_id: str | None,
    ) -> None:
        if auth_method == "oauth":
            raise MCPOAuthNotImplemented()
        if auth_method not in _VALID_METHODS:
            raise ValueError(f"unknown auth_method: {auth_method}")
        if credential_scope not in _VALID_SCOPES:
            raise ValueError(f"unknown credential_scope: {credential_scope}")
        if transport not in _HTTP_TRANSPORTS:
            raise ValueError(f"unknown transport: {transport}")

        # Lock auth_method=none ⇔ credential_scope=none
        if (auth_method == "none") != (credential_scope == "none"):
            raise ValueError(
                "auth_method=none and credential_scope=none must be set together"
            )

        # Plaintext required for org/workspace; forbidden for user/none
        if credential_scope in ("org", "workspace") and not credential_plaintext:
            raise MCPCredentialRequired()
        if credential_scope in ("user", "none") and credential_plaintext:
            raise MCPUserScopeCredentialForbidden()

        # workspace-private constrains scope
        if owner_workspace_id is not None and credential_scope == "org":
            raise ValueError(
                "workspace-private servers cannot use credential_scope=org"
            )

    async def _refresh_tools_for_server(self, server: MCPServer) -> None:
        """Internal: discover + persist results. Used by create + refresh_tools."""
        from datetime import UTC, datetime
        # Resolve credential / token for discovery probe.
        # For credential_scope=user: skip discovery (no user context here);
        # admin/test-connection paths supply alternative discovery.
        if server.credential_scope == "user":
            # Discovery for user-scope happens via /test-connection with admin's
            # plaintext or via an explicit refresh-tools call by a user with creds.
            return
        token: str | None = None
        if server.credential_scope == "org":
            assert server.credential_id is not None
            token = await self.cred_service.get_decrypted(
                credential_id=server.credential_id,
                requesting_kind=_CREDENTIAL_KIND_MCP,
            )
        elif server.credential_scope == "workspace":
            ws_cred = await self.ws_cred_repo.get(
                workspace_id=server.owner_workspace_id or "",
                mcp_server_id=server.id,
            )
            if ws_cred is None:
                return  # no creds, can't discover
            token = await self.cred_service.get_decrypted(
                credential_id=ws_cred.credential_id,
                requesting_kind=_CREDENTIAL_KIND_MCP,
            )
        # credential_scope=none: discover unauthenticated

        success, tools, error = await discover_tools(server, credential_or_token=token)
        if success:
            server.tools_cache = tools or []
            server.authed = True
            server.last_error = None
            server.last_discovered_at = datetime.now(UTC)
        else:
            server.tools_cache = []
            server.authed = False
            server.last_error = error
            server.last_discovered_at = datetime.now(UTC)
        await self.server_repo.update(server)
```

- [ ] **Step 5: Run, expect pass**

```bash
uv run pytest tests/unit/test_mcp_service_invariants.py -v
```

Expected: 5 pass.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/services/mcp.py \
        backend/tests/unit/test_mcp_service_invariants.py \
        backend/tests/unit/conftest.py
git commit -m "feat(mcp): add MCPServerService.create with full invariant enforcement"
```

---

### Task 16: MCPServerService — update / delete with cascade

**Files:**
- Modify: `backend/cubeplex/services/mcp.py`

- [ ] **Step 1: Add update / delete methods**

```python
# backend/cubeplex/services/mcp.py — append to MCPServerService class

    async def update(
        self, *, server_id: str,
        name: str | None = None,
        server_url: str | None = None,
        transport: str | None = None,
        credential_plaintext: str | None = None,  # update inline org-scope cred
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        sse_read_timeout: float | None = None,
    ) -> MCPServer:
        server = await self.server_repo.get(server_id)
        if server is None:
            raise MCPServerNotFound(server_id)

        if name is not None and name != server.name:
            for existing in await self.server_repo.list_for_org(
                owner_workspace_id=server.owner_workspace_id,
            ):
                if existing.id != server.id and existing.name == name:
                    raise MCPServerNameConflict(name)
            server.name = name
        if server_url is not None and server_url != server.server_url:
            new_hash = _sha256_hex(server_url)
            existing = await self.server_repo.find_by_url_hash(
                owner_workspace_id=server.owner_workspace_id,
                server_url_hash=new_hash,
            )
            if existing and existing.id != server.id:
                raise MCPServerURLConflict(server_url)
            server.server_url = server_url
            server.server_url_hash = new_hash
        if transport is not None:
            if transport not in _HTTP_TRANSPORTS:
                raise ValueError(f"unknown transport: {transport}")
            server.transport = transport
        if credential_plaintext is not None:
            if server.credential_scope == "org":
                if server.credential_id is None:
                    # bootstrap missing inline cred (defensive)
                    server.credential_id = await self.cred_service.create(
                        kind=_CREDENTIAL_KIND_MCP,
                        name=f"mcp:{server.name}:org",
                        plaintext=credential_plaintext,
                    )
                else:
                    await self.cred_service.update(
                        credential_id=server.credential_id,
                        plaintext=credential_plaintext,
                    )
            else:
                raise MCPUserScopeCredentialForbidden(
                    "update plaintext only for credential_scope=org via this path; "
                    "use workspace-credential / my-credential routes for other scopes"
                )
        if headers is not None:
            server.headers = headers
        if timeout is not None:
            server.timeout = timeout
        if sse_read_timeout is not None:
            server.sse_read_timeout = sse_read_timeout

        await self.server_repo.update(server)
        # Re-discover after meaningful changes
        if server_url is not None or transport is not None or credential_plaintext is not None:
            await self._refresh_tools_for_server(server)
        return await self.server_repo.get(server.id) or server

    async def delete(self, *, server_id: str) -> None:
        server = await self.server_repo.get(server_id)
        if server is None:
            raise MCPServerNotFound(server_id)

        # Cascade: delete bindings, ws creds, user creds, then inline cred, then server
        for b in await self.binding_repo.list_for_server(server_id):
            await self.binding_repo.delete(
                workspace_id=b.workspace_id, mcp_server_id=server_id,
            )
        for wc in await self.ws_cred_repo.list_for_server(server_id):
            await self.ws_cred_repo.delete(
                workspace_id=wc.workspace_id, mcp_server_id=server_id,
            )
            try:
                await self.cred_service.delete(credential_id=wc.credential_id)
            except Exception:
                pass  # idempotent
        for uc in await self.user_cred_repo.list_for_server(server_id):
            await self.user_cred_repo.delete(
                user_id=uc.user_id, mcp_server_id=server_id,
            )
            try:
                await self.cred_service.delete(credential_id=uc.credential_id)
            except Exception:
                pass

        if server.credential_id:
            try:
                await self.cred_service.delete(credential_id=server.credential_id)
            except Exception:
                pass

        await self.server_repo.delete(server_id)
```

- [ ] **Step 2: Add a unit test**

```python
# backend/tests/unit/test_mcp_service_invariants.py — append

async def test_update_renaming_to_existing_name_conflicts(mcp_service) -> None:
    s1 = await mcp_service.create(
        name="a", server_url="https://x", transport="streamable_http",
        auth_method="none", credential_scope="none",
    )
    await mcp_service.create(
        name="b", server_url="https://y", transport="streamable_http",
        auth_method="none", credential_scope="none",
    )
    with pytest.raises(MCPServerNameConflict):
        await mcp_service.update(server_id=s1.id, name="b")


async def test_delete_cascades_bindings_and_creds(mcp_service) -> None:
    s = await mcp_service.create(
        name="c", server_url="https://z", transport="streamable_http",
        auth_method="none", credential_scope="none",
    )
    await mcp_service.delete(server_id=s.id)
    with pytest.raises(MCPServerNotFound):
        await mcp_service.update(server_id=s.id, name="x")
```

- [ ] **Step 3: Run, expect pass**

```bash
uv run pytest tests/unit/test_mcp_service_invariants.py -v
```

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/services/mcp.py \
        backend/tests/unit/test_mcp_service_invariants.py
git commit -m "feat(mcp): MCPServerService.update + delete with cascade"
```

---

### Task 17: refresh_tools + test_connection

**Files:**
- Modify: `backend/cubeplex/services/mcp.py`

- [ ] **Step 1: Add methods**

```python
# backend/cubeplex/services/mcp.py — append to MCPServerService

    async def refresh_tools(self, *, server_id: str) -> MCPServer:
        server = await self.server_repo.get(server_id)
        if server is None:
            raise MCPServerNotFound(server_id)
        await self._refresh_tools_for_server(server)
        return await self.server_repo.get(server.id) or server

    async def test_connection(
        self, *, server_url: str, transport: str,
        auth_method: str, credential_scope: str,
        credential_plaintext: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0, sse_read_timeout: float = 300.0,
        owner_workspace_id: str | None = None,
    ) -> tuple[bool, list[dict] | None, str | None]:
        """Dry-run discovery without persisting anything.

        Returns (success, tools, error_msg). For credential_scope=user/none we skip
        the live probe and just report params validity (since no real creds available).
        """
        self._validate_create_invariants(
            transport=transport, auth_method=auth_method,
            credential_scope=credential_scope,
            credential_plaintext=credential_plaintext,
            owner_workspace_id=owner_workspace_id,
        )
        # Build a transient MCPServer (not persisted)
        transient = MCPServer(
            org_id=self._ctx.org_id,
            owner_workspace_id=owner_workspace_id,
            name="__test__",
            server_url=server_url, server_url_hash=_sha256_hex(server_url),
            transport=transport, auth_method=auth_method,
            credential_scope=credential_scope, credential_id=None,
            headers=headers or {},
            timeout=timeout, sse_read_timeout=sse_read_timeout,
            created_by_user_id=self._ctx.user_id,
        )
        token = credential_plaintext  # for org/workspace static
        if credential_scope == "user":
            return True, None, "user-scope: per-user discovery not supported in test-connection"
        if credential_scope == "none":
            token = None  # no auth header
        return await discover_tools(transient, credential_or_token=token)
```

- [ ] **Step 2: Commit**

```bash
git add backend/cubeplex/services/mcp.py
git commit -m "feat(mcp): add refresh_tools + test_connection dry-run"
```

---

### Task 18: promote_to_org (α + β paths) + unit test

**Files:**
- Modify: `backend/cubeplex/services/mcp.py`
- Modify: `backend/tests/unit/test_mcp_service_invariants.py`

- [ ] **Step 1: Append failing unit test**

```python
# backend/tests/unit/test_mcp_service_invariants.py — append

async def test_promote_alpha_moves_workspace_cred_to_inline(
    mcp_service, db_session, request_context,
) -> None:
    s = await mcp_service.create(
        name="prom-a", server_url="https://p1",
        transport="streamable_http", auth_method="static",
        credential_scope="workspace",
        credential_plaintext="ws-key-1",
        owner_workspace_id="ws-test",
    )
    assert s.owner_workspace_id == "ws-test"
    assert s.credential_scope == "workspace"

    await mcp_service.promote_to_org(server_id=s.id, share_credential=True)
    refreshed = await mcp_service.server_repo.get(s.id)
    assert refreshed.owner_workspace_id is None
    assert refreshed.credential_scope == "org"
    assert refreshed.credential_id is not None  # inline now
    # original ws still has access via binding
    binding = await mcp_service.binding_repo.get(
        workspace_id="ws-test", mcp_server_id=s.id,
    )
    assert binding is not None and binding.enabled


async def test_promote_beta_keeps_workspace_cred(mcp_service) -> None:
    s = await mcp_service.create(
        name="prom-b", server_url="https://p2",
        transport="streamable_http", auth_method="static",
        credential_scope="workspace",
        credential_plaintext="ws-key-2",
        owner_workspace_id="ws-test",
    )
    await mcp_service.promote_to_org(server_id=s.id, share_credential=False)
    refreshed = await mcp_service.server_repo.get(s.id)
    assert refreshed.owner_workspace_id is None
    assert refreshed.credential_scope == "workspace"
    # ws_cred row preserved
    ws_cred = await mcp_service.ws_cred_repo.get(
        workspace_id="ws-test", mcp_server_id=s.id,
    )
    assert ws_cred is not None
    binding = await mcp_service.binding_repo.get(
        workspace_id="ws-test", mcp_server_id=s.id,
    )
    assert binding is not None


async def test_promote_already_org_wide_raises(mcp_service) -> None:
    s = await mcp_service.create(
        name="prom-c", server_url="https://p3",
        transport="streamable_http", auth_method="none",
        credential_scope="none",
    )  # owner_workspace_id=None already
    with pytest.raises(MCPServerAlreadyOrgWide):
        await mcp_service.promote_to_org(server_id=s.id, share_credential=False)
```

- [ ] **Step 2: Run, expect fail**

```bash
uv run pytest tests/unit/test_mcp_service_invariants.py::test_promote_alpha_moves_workspace_cred_to_inline -v
```

- [ ] **Step 3: Implement**

```python
# backend/cubeplex/services/mcp.py — append to MCPServerService

    async def promote_to_org(
        self, *, server_id: str, share_credential: bool,
    ) -> MCPServer:
        server = await self.server_repo.get(server_id)
        if server is None:
            raise MCPServerNotFound(server_id)
        if server.owner_workspace_id is None:
            raise MCPServerAlreadyOrgWide(server_id)

        if server.credential_scope not in ("workspace",) and share_credential:
            raise MCPShareCredentialOnlyForWorkspaceScope()

        original_ws = server.owner_workspace_id

        if server.credential_scope == "workspace" and share_credential:
            # α: move ws cred → inline + scope=org
            ws_cred = await self.ws_cred_repo.get(
                workspace_id=original_ws, mcp_server_id=server_id,
            )
            if ws_cred is None:
                raise ValueError(
                    "workspace-scope server has no workspace_mcp_credentials row"
                )
            server.credential_scope = "org"
            server.credential_id = ws_cred.credential_id
            await self.ws_cred_repo.delete(
                workspace_id=original_ws, mcp_server_id=server_id,
            )
        # β (workspace, not share) and user/none: scope unchanged

        server.owner_workspace_id = None
        await self.server_repo.update(server)

        # Original workspace gets a binding so it doesn't lose access
        existing_binding = await self.binding_repo.get(
            workspace_id=original_ws, mcp_server_id=server_id,
        )
        if existing_binding is None:
            await self.binding_repo.add(WorkspaceMCPBinding(
                org_id=self._ctx.org_id,
                workspace_id=original_ws,
                mcp_server_id=server_id,
                enabled=True,
                created_by_user_id=self._ctx.user_id,
            ))

        return await self.server_repo.get(server.id) or server
```

- [ ] **Step 4: Run, expect pass**

```bash
uv run pytest tests/unit/test_mcp_service_invariants.py -v
```

Expected: all (8+) pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/services/mcp.py \
        backend/tests/unit/test_mcp_service_invariants.py
git commit -m "feat(mcp): promote_to_org with α (share-cred) and β (keep-cred) paths"
```

---

### Task 19: Workspace-credential / user-credential management methods

**Files:**
- Modify: `backend/cubeplex/services/mcp.py`

- [ ] **Step 1: Implement set/get/delete for both scope kinds**

```python
# backend/cubeplex/services/mcp.py — append to MCPServerService

    async def set_workspace_credential(
        self, *, server_id: str, workspace_id: str, plaintext: str,
        credential_name: str | None = None,
    ) -> str:
        """Set/update workspace-shared credential for a (server, workspace) pair.

        Used by ws router for credential_scope=workspace org-wide servers OR
        the workspace-private workspace-scope server's own ws.
        """
        server = await self.server_repo.get(server_id)
        if server is None:
            raise MCPServerNotFound(server_id)
        if server.credential_scope != "workspace":
            raise MCPCredentialPathMismatch(
                f"server {server_id} has scope={server.credential_scope}, not 'workspace'"
            )
        existing = await self.ws_cred_repo.get(
            workspace_id=workspace_id, mcp_server_id=server_id,
        )
        if existing is None:
            cred_id = await self.cred_service.create(
                kind=_CREDENTIAL_KIND_MCP,
                name=credential_name or f"mcp:{server.name}:ws:{workspace_id}",
                plaintext=plaintext,
            )
            await self.ws_cred_repo.add(WorkspaceMCPCredential(
                org_id=self._ctx.org_id,
                workspace_id=workspace_id,
                mcp_server_id=server_id,
                credential_id=cred_id,
                created_by_user_id=self._ctx.user_id,
            ))
            return cred_id
        # update existing
        await self.cred_service.update(
            credential_id=existing.credential_id, plaintext=plaintext,
        )
        return existing.credential_id

    async def delete_workspace_credential(
        self, *, server_id: str, workspace_id: str,
    ) -> None:
        existing = await self.ws_cred_repo.get(
            workspace_id=workspace_id, mcp_server_id=server_id,
        )
        if existing is None:
            return
        await self.ws_cred_repo.delete(
            workspace_id=workspace_id, mcp_server_id=server_id,
        )
        try:
            await self.cred_service.delete(credential_id=existing.credential_id)
        except Exception:
            pass

    async def has_workspace_credential(
        self, *, server_id: str, workspace_id: str,
    ) -> bool:
        return (
            await self.ws_cred_repo.get(
                workspace_id=workspace_id, mcp_server_id=server_id,
            )
        ) is not None

    async def set_user_credential(
        self, *, server_id: str, user_id: str, plaintext: str,
        credential_name: str | None = None,
    ) -> str:
        server = await self.server_repo.get(server_id)
        if server is None:
            raise MCPServerNotFound(server_id)
        if server.credential_scope != "user":
            raise MCPCredentialPathMismatch(
                f"server {server_id} has scope={server.credential_scope}, not 'user'"
            )
        existing = await self.user_cred_repo.get(
            user_id=user_id, mcp_server_id=server_id,
        )
        if existing is None:
            cred_id = await self.cred_service.create(
                kind=_CREDENTIAL_KIND_MCP,
                name=credential_name or f"mcp:{server.name}:user:{user_id}",
                plaintext=plaintext,
            )
            await self.user_cred_repo.add(UserMCPCredential(
                org_id=self._ctx.org_id,
                user_id=user_id,
                mcp_server_id=server_id,
                credential_id=cred_id,
            ))
            return cred_id
        await self.cred_service.update(
            credential_id=existing.credential_id, plaintext=plaintext,
        )
        return existing.credential_id

    async def delete_user_credential(
        self, *, server_id: str, user_id: str,
    ) -> None:
        existing = await self.user_cred_repo.get(
            user_id=user_id, mcp_server_id=server_id,
        )
        if existing is None:
            return
        await self.user_cred_repo.delete(
            user_id=user_id, mcp_server_id=server_id,
        )
        try:
            await self.cred_service.delete(credential_id=existing.credential_id)
        except Exception:
            pass

    async def has_user_credential(
        self, *, server_id: str, user_id: str,
    ) -> bool:
        return (
            await self.user_cred_repo.get(
                user_id=user_id, mcp_server_id=server_id,
            )
        ) is not None

    async def replace_bindings(
        self, *, server_id: str, bindings: list[tuple[str, bool]],
    ) -> None:
        server = await self.server_repo.get(server_id)
        if server is None:
            raise MCPServerNotFound(server_id)
        if server.owner_workspace_id is not None:
            raise MCPWorkspaceOwnedNoBinding()
        await self.binding_repo.upsert_bulk(
            mcp_server_id=server_id, bindings=bindings,
            created_by_user_id=self._ctx.user_id,
        )
```

- [ ] **Step 2: Commit**

```bash
git add backend/cubeplex/services/mcp.py
git commit -m "feat(mcp): add workspace/user credential mgmt + bindings replace"
```

---

### Task 20: Wire vault delete-in-use check

**Files:**
- Modify: `backend/cubeplex/services/credential.py`
- Modify: `backend/tests/e2e/test_credentials_vault.py`

- [ ] **Step 1: Append failing E2E**

```python
# backend/tests/e2e/test_credentials_vault.py — append

async def test_delete_credential_referenced_by_mcp_server_raises(
    db_session, backend, request_context,
) -> None:
    """If a credential is still referenced by mcp_servers.credential_id, delete fails."""
    from cubeplex.models import MCPServer
    from cubeplex.repositories.credential import CredentialRepository
    from cubeplex.repositories.mcp import MCPServerRepository
    from cubeplex.services.credential import CredentialService

    cred_repo = CredentialRepository(db_session, org_id="org-A")
    server_repo = MCPServerRepository(db_session, org_id="org-A")
    svc = CredentialService(
        cred_repo, backend, request_context._replace(org_id="org-A"),
        # delete-in-use check needs a way to discover references — see Task 20 step 2
    )
    cred_id = await svc.create(kind="mcp_server", name="x", plaintext="secret")
    await server_repo.add(MCPServer(
        org_id="org-A", name="srv", server_url="https://a",
        server_url_hash="h", transport="streamable_http",
        auth_method="static", credential_scope="org",
        credential_id=cred_id, created_by_user_id="u1",
    ))
    from cubeplex.credentials.exceptions import CredentialInUseError
    with pytest.raises(CredentialInUseError):
        await svc.delete(credential_id=cred_id)
```

- [ ] **Step 2: Add reverse-ref check to CredentialService.delete**

```python
# backend/cubeplex/services/credential.py — modify delete

    async def delete(self, *, credential_id: str) -> None:
        cred = await self._repo.get(credential_id)
        if cred is None:
            raise CredentialNotFound(credential_id)
        # Reverse-reference check: refuse if anyone still references this credential.
        await self._guard_references(credential_id)
        await self._repo.delete(credential_id)

    async def _guard_references(self, credential_id: str) -> None:
        """Raise CredentialInUseError if any MCP table still references this cred."""
        from cubeplex.repositories.mcp import (
            MCPServerRepository,
            UserMCPCredentialRepository,
            WorkspaceMCPCredentialRepository,
        )
        session = self._repo.session
        for repo_cls in (
            MCPServerRepository,
            WorkspaceMCPCredentialRepository,
            UserMCPCredentialRepository,
        ):
            repo = repo_cls(session, org_id=self._ctx.org_id)
            refs = await repo.find_by_credential_id(credential_id)
            if refs:
                from cubeplex.credentials.exceptions import CredentialInUseError
                raise CredentialInUseError(
                    f"credential {credential_id} referenced by {repo_cls.__name__}: "
                    f"{[getattr(r, 'id', '?') for r in refs]}"
                )
```

- [ ] **Step 3: Run, expect pass**

```bash
uv run pytest tests/e2e/test_credentials_vault.py -v
```

Expected: 6 pass (5 prior + 1 new).

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/services/credential.py \
        backend/tests/e2e/test_credentials_vault.py
git commit -m "feat(vault): refuse delete when credential is referenced by MCP rows"
```

---

## Phase D · MCP API Routes (Stage 4)

### Task 21: Pydantic schemas

**Files:**
- Create: `backend/cubeplex/api/schemas/mcp.py`

- [ ] **Step 1: Define request/response schemas**

```python
# backend/cubeplex/api/schemas/mcp.py
"""MCP request/response schemas. Plaintext credentials only flow IN; never OUT."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class CredentialRefOut(BaseModel):
    id: str
    name: str
    has_value: bool = True


class MCPServerOut(BaseModel):
    id: str
    name: str
    server_url: str
    transport: str
    auth_method: str
    credential_scope: str
    credential: CredentialRefOut | None
    owner_workspace_id: str | None
    headers: dict[str, str]
    tools_cache: list[dict[str, Any]] | None  # null in list responses
    authed: bool
    last_error: str | None
    last_discovered_at: datetime | None
    timeout: float
    sse_read_timeout: float
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime


class MCPServerCreateAdmin(BaseModel):
    """Admin path: scope ∈ {org, user, none}. workspace must use ws path."""
    name: str = Field(min_length=1, max_length=64)
    server_url: str = Field(min_length=1, max_length=2048)
    transport: Literal["streamable_http", "sse", "stdio"]
    auth_method: Literal["static", "oauth", "none"]
    credential_scope: Literal["org", "user", "none"]
    credential_plaintext: str | None = None
    credential_name: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: float = 30.0
    sse_read_timeout: float = 300.0


class MCPServerCreateWS(BaseModel):
    """WS path: workspace-private + scope ∈ {workspace, user, none}."""
    name: str = Field(min_length=1, max_length=64)
    server_url: str = Field(min_length=1, max_length=2048)
    transport: Literal["streamable_http", "sse", "stdio"]
    auth_method: Literal["static", "oauth", "none"]
    credential_scope: Literal["workspace", "user", "none"]
    credential_plaintext: str | None = None
    credential_name: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: float = 30.0
    sse_read_timeout: float = 300.0


class MCPServerPatch(BaseModel):
    name: str | None = None
    server_url: str | None = None
    transport: Literal["streamable_http", "sse", "stdio"] | None = None
    credential_plaintext: str | None = None
    headers: dict[str, str] | None = None
    timeout: float | None = None
    sse_read_timeout: float | None = None


class MCPTestConnectionRequest(BaseModel):
    server_url: str
    transport: Literal["streamable_http", "sse", "stdio"]
    auth_method: Literal["static", "oauth", "none"]
    credential_scope: Literal["org", "workspace", "user", "none"]
    credential_plaintext: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: float = 30.0
    sse_read_timeout: float = 300.0


class MCPTestConnectionResponse(BaseModel):
    success: bool
    tools: list[dict[str, Any]] | None = None
    error: str | None = None


class WorkspaceBindingItem(BaseModel):
    workspace_id: str
    enabled: bool


class MCPBindingsReplace(BaseModel):
    bindings: list[WorkspaceBindingItem]


class MCPPromoteRequest(BaseModel):
    share_credential: bool = False


class MCPCredentialUpsert(BaseModel):
    plaintext: str = Field(min_length=1)
    name: str | None = None


class MCPCredentialStatus(BaseModel):
    has_value: bool


class MCPServerListWS(BaseModel):
    """ws GET response — owned + via_binding."""
    owned: list[MCPServerOut]
    via_binding: list[MCPServerOut]
```

- [ ] **Step 2: Commit**

```bash
git add backend/cubeplex/api/schemas/mcp.py
git commit -m "feat(mcp): add API pydantic schemas for admin + ws routes"
```

---

### Task 22: Audit sink no-op

**Files:**
- Create: `backend/cubeplex/audit/__init__.py`
- Create: `backend/cubeplex/audit/sink.py`

- [ ] **Step 1: Define Protocol + no-op impl**

```python
# backend/cubeplex/audit/__init__.py
"""Audit sink — Protocol + CE no-op default. M1-E5 will register a real sink."""

from cubeplex.audit.sink import AuditSink, NoOpAuditSink

__all__ = ["AuditSink", "NoOpAuditSink"]
```

```python
# backend/cubeplex/audit/sink.py
"""Audit log sink: structured event recorder. CE default no-op; EE registers real sink."""

from typing import Any, Protocol


class AuditSink(Protocol):
    async def record(
        self, *, event: str, actor_user_id: str, org_id: str,
        target_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None: ...


class NoOpAuditSink:
    async def record(
        self, *, event: str, actor_user_id: str, org_id: str,
        target_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        pass
```

- [ ] **Step 2: Commit**

```bash
git add backend/cubeplex/audit/__init__.py backend/cubeplex/audit/sink.py
git commit -m "feat(audit): add Protocol + NoOpAuditSink (M1-E5 will replace)"
```

---

### Task 23: FastAPI DI providers

**Files:**
- Modify: `backend/cubeplex/auth/dependencies.py` (or wherever DI providers live — find by `grep -r "Depends(get_session)" cubeplex/api`)
- Create: `backend/cubeplex/credentials/dependencies.py`
- Create: `backend/cubeplex/mcp/dependencies.py`

- [ ] **Step 1: Vault DI provider**

```python
# backend/cubeplex/credentials/dependencies.py
"""FastAPI DI providers for vault."""

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import require_member
from cubeplex.credentials.encryption import EncryptionBackend
from cubeplex.db.session import get_session
from cubeplex.repositories.credential import CredentialRepository
from cubeplex.services.credential import CredentialService


async def get_encryption_backend(request: Request) -> EncryptionBackend:
    """Master EncryptionBackend stored on app.state at startup."""
    return request.app.state.encryption_backend


def build_credential_service(
    session: AsyncSession,
    backend: EncryptionBackend,
    *,
    org_id: str,
    actor_user_id: str,
) -> CredentialService:
    repo = CredentialRepository(session, org_id=org_id)
    return CredentialService(
        repo,
        backend,
        org_id=org_id,
        actor_user_id=actor_user_id,
    )


async def get_credential_service(
    session: AsyncSession = Depends(get_session),
    backend: EncryptionBackend = Depends(get_encryption_backend),
    ctx: RequestContext = Depends(require_member),
) -> CredentialService:
    return build_credential_service(
        session,
        backend,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
    )
```

- [ ] **Step 2: MCP DI provider**

```python
# backend/cubeplex/mcp/dependencies.py
"""FastAPI DI providers for MCP service + signer."""

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import require_member
from cubeplex.audit.sink import AuditSink, NoOpAuditSink
from cubeplex.credentials.dependencies import get_credential_service
from cubeplex.db.session import get_session
from cubeplex.mcp.user_token import HS256Signer, MCPUserTokenSigner
from cubeplex.repositories.mcp import (
    MCPServerRepository,
    UserMCPCredentialRepository,
    WorkspaceMCPBindingRepository,
    WorkspaceMCPCredentialRepository,
)
from cubeplex.services.credential import CredentialService
from cubeplex.services.mcp import MCPServerService


def build_user_token_signer() -> MCPUserTokenSigner:
    """Build signer from config/env for non-route code such as RunManager."""
    from cubeplex.config import config

    secret = config.get("auth.jwt_secret")
    if not secret:
        raise RuntimeError("CUBEPLEX_AUTH__JWT_SECRET missing")
    return HS256Signer(secret=secret)


async def get_user_token_signer(request: Request) -> MCPUserTokenSigner:
    """Stored on app.state at startup using CUBEPLEX_AUTH__JWT_SECRET."""
    return request.app.state.mcp_user_token_signer


async def get_audit_sink(request: Request) -> AuditSink:
    return request.app.state.audit_sink


async def get_mcp_service(
    session: AsyncSession = Depends(get_session),
    cred_service: CredentialService = Depends(get_credential_service),
    ctx: RequestContext = Depends(require_member),
) -> MCPServerService:
    server_repo = MCPServerRepository(session, org_id=ctx.org_id)
    ws_cred_repo = WorkspaceMCPCredentialRepository(session, org_id=ctx.org_id)
    user_cred_repo = UserMCPCredentialRepository(session, org_id=ctx.org_id)
    binding_repo = WorkspaceMCPBindingRepository(session, org_id=ctx.org_id)
    return MCPServerService(
        server_repo=server_repo, ws_cred_repo=ws_cred_repo,
        user_cred_repo=user_cred_repo, binding_repo=binding_repo,
        cred_service=cred_service, request_context=ctx,
    )
```

- [ ] **Step 3: Wire app.state at startup**

In `backend/cubeplex/api/app.py` lifespan / startup function, append:

```python
# inside lifespan startup
from cubeplex.audit.sink import NoOpAuditSink
from cubeplex.mcp.user_token import HS256Signer

app.state.encryption_backend = _build_encryption_backend()  # already added in Task 3
app.state.mcp_user_token_signer = HS256Signer(
    secret=os.environ.get("CUBEPLEX_AUTH__JWT_SECRET")
    or settings.get("auth.jwt_secret")
    or _fail("CUBEPLEX_AUTH__JWT_SECRET missing")
)
app.state.audit_sink = NoOpAuditSink()


def _fail(msg: str) -> None:
    raise RuntimeError(msg)
```

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/credentials/dependencies.py \
        backend/cubeplex/mcp/dependencies.py \
        backend/cubeplex/api/app.py
git commit -m "feat(api): add DI providers for credential service / mcp service / signer"
```

---

### Task 24: Admin MCP routes

**Files:**
- Create: `backend/cubeplex/api/routes/v1/admin_mcp.py`

- [ ] **Step 1: Implement routes**

```python
# backend/cubeplex/api/routes/v1/admin_mcp.py
"""Admin MCP routes — gated by require_org_admin.

scope ∈ {org, user, none}. workspace-scope is exclusive to the WS path
(/api/v1/ws/{wsId}/mcp/...).
"""

import hashlib

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from cubeplex.audit.sink import AuditSink
from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import get_request_context, require_org_admin
from cubeplex.api.schemas.mcp import (
    CredentialRefOut,
    MCPBindingsReplace,
    MCPServerCreateAdmin,
    MCPServerOut,
    MCPServerPatch,
    MCPTestConnectionRequest,
    MCPTestConnectionResponse,
    WorkspaceBindingItem,
)
from cubeplex.mcp.dependencies import get_audit_sink, get_mcp_service
from cubeplex.mcp.exceptions import (
    MCPCredentialRequired,
    MCPOAuthNotImplemented,
    MCPServerNameConflict,
    MCPServerNotFound,
    MCPServerURLConflict,
    MCPUserScopeCredentialForbidden,
    MCPWorkspaceOwnedNoBinding,
)
from cubeplex.models import MCPServer
from cubeplex.services.mcp import MCPServerService

router = APIRouter(
    prefix="/api/v1/admin/mcp",
    tags=["admin-mcp"],
    dependencies=[Depends(require_org_admin)],
)


def _server_to_out(
    server: MCPServer,
    *,
    include_tools_cache: bool,
    cred_name: str | None = None,
) -> MCPServerOut:
    cred_ref: CredentialRefOut | None = None
    if server.credential_id is not None:
        cred_ref = CredentialRefOut(
            id=server.credential_id,
            name=cred_name or "—",
            has_value=True,
        )
    return MCPServerOut(
        id=server.id, name=server.name, server_url=server.server_url,
        transport=server.transport, auth_method=server.auth_method,
        credential_scope=server.credential_scope, credential=cred_ref,
        owner_workspace_id=server.owner_workspace_id,
        headers=server.headers or {},
        tools_cache=server.tools_cache if include_tools_cache else None,
        authed=server.authed, last_error=server.last_error,
        last_discovered_at=server.last_discovered_at,
        timeout=server.timeout, sse_read_timeout=server.sse_read_timeout,
        created_by_user_id=server.created_by_user_id,
        created_at=server.created_at, updated_at=server.updated_at,
    )


@router.get("/servers")
async def list_servers(
    scope: str | None = Query(default=None),
    owner_workspace_id: str | None = Query(default=None),
    has_error: bool | None = Query(default=None),
    svc: MCPServerService = Depends(get_mcp_service),
) -> list[MCPServerOut]:
    servers = await svc.server_repo.list_for_org(owner_workspace_id=...)
    if scope is not None:
        servers = [s for s in servers if s.credential_scope == scope]
    if owner_workspace_id is not None:
        servers = [s for s in servers if s.owner_workspace_id == owner_workspace_id]
    if has_error is True:
        servers = [s for s in servers if not s.authed]
    return [_server_to_out(s, include_tools_cache=False) for s in servers]


@router.post("/servers", status_code=status.HTTP_201_CREATED)
async def create_server(
    body: MCPServerCreateAdmin,
    svc: MCPServerService = Depends(get_mcp_service),
    ctx: RequestContext = Depends(get_request_context),
    audit: AuditSink = Depends(get_audit_sink),
) -> MCPServerOut:
    try:
        server = await svc.create(
            name=body.name, server_url=body.server_url,
            transport=body.transport, auth_method=body.auth_method,
            credential_scope=body.credential_scope,
            credential_plaintext=body.credential_plaintext,
            credential_name=body.credential_name,
            owner_workspace_id=None,
            headers=body.headers, timeout=body.timeout,
            sse_read_timeout=body.sse_read_timeout,
        )
    except MCPOAuthNotImplemented:
        raise HTTPException(409, detail={"code": "mcp_oauth_not_implemented"})
    except MCPServerURLConflict:
        raise HTTPException(409, detail={"code": "mcp_server_url_conflict"})
    except MCPServerNameConflict:
        raise HTTPException(409, detail={"code": "mcp_server_name_conflict"})
    except MCPCredentialRequired:
        raise HTTPException(400, detail={"code": "mcp_credential_required"})
    except MCPUserScopeCredentialForbidden:
        raise HTTPException(400, detail={"code": "mcp_user_scope_credential_forbidden"})
    await audit.record(
        event="mcp.server.created", actor_user_id=ctx.user_id, org_id=ctx.org_id,
        target_id=server.id, details={"scope": server.credential_scope},
    )
    return _server_to_out(server, include_tools_cache=True)


@router.get("/servers/{server_id}")
async def get_server(
    server_id: str,
    svc: MCPServerService = Depends(get_mcp_service),
) -> MCPServerOut:
    server = await svc.server_repo.get(server_id)
    if server is None:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"})
    return _server_to_out(server, include_tools_cache=True)


@router.patch("/servers/{server_id}")
async def patch_server(
    server_id: str, body: MCPServerPatch,
    svc: MCPServerService = Depends(get_mcp_service),
    ctx: RequestContext = Depends(get_request_context),
    audit: AuditSink = Depends(get_audit_sink),
) -> MCPServerOut:
    try:
        server = await svc.update(
            server_id=server_id, name=body.name, server_url=body.server_url,
            transport=body.transport, credential_plaintext=body.credential_plaintext,
            headers=body.headers, timeout=body.timeout,
            sse_read_timeout=body.sse_read_timeout,
        )
    except MCPServerNotFound:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"})
    except MCPServerNameConflict:
        raise HTTPException(409, detail={"code": "mcp_server_name_conflict"})
    except MCPServerURLConflict:
        raise HTTPException(409, detail={"code": "mcp_server_url_conflict"})
    except MCPUserScopeCredentialForbidden:
        raise HTTPException(400, detail={"code": "mcp_user_scope_credential_forbidden"})
    await audit.record(
        event="mcp.server.updated", actor_user_id=ctx.user_id,
        org_id=ctx.org_id, target_id=server_id,
    )
    return _server_to_out(server, include_tools_cache=True)


@router.delete("/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: str,
    svc: MCPServerService = Depends(get_mcp_service),
    ctx: RequestContext = Depends(get_request_context),
    audit: AuditSink = Depends(get_audit_sink),
) -> None:
    try:
        await svc.delete(server_id=server_id)
    except MCPServerNotFound:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"})
    await audit.record(
        event="mcp.server.deleted", actor_user_id=ctx.user_id,
        org_id=ctx.org_id, target_id=server_id,
    )


@router.post("/servers/{server_id}/refresh-tools")
async def refresh_tools(
    server_id: str,
    svc: MCPServerService = Depends(get_mcp_service),
) -> MCPServerOut:
    try:
        server = await svc.refresh_tools(server_id=server_id)
    except MCPServerNotFound:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"})
    return _server_to_out(server, include_tools_cache=True)


@router.post("/test-connection")
async def test_connection(
    body: MCPTestConnectionRequest,
    svc: MCPServerService = Depends(get_mcp_service),
) -> MCPTestConnectionResponse:
    try:
        success, tools, error = await svc.test_connection(
            server_url=body.server_url, transport=body.transport,
            auth_method=body.auth_method, credential_scope=body.credential_scope,
            credential_plaintext=body.credential_plaintext,
            headers=body.headers, timeout=body.timeout,
            sse_read_timeout=body.sse_read_timeout,
        )
    except MCPOAuthNotImplemented:
        raise HTTPException(409, detail={"code": "mcp_oauth_not_implemented"})
    except (MCPCredentialRequired, MCPUserScopeCredentialForbidden) as e:
        raise HTTPException(400, detail={"code": e.__class__.__name__})
    return MCPTestConnectionResponse(success=success, tools=tools, error=error)


@router.get("/servers/{server_id}/bindings")
async def get_bindings(
    server_id: str,
    svc: MCPServerService = Depends(get_mcp_service),
) -> list[WorkspaceBindingItem]:
    server = await svc.server_repo.get(server_id)
    if server is None:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"})
    if server.owner_workspace_id is not None:
        raise HTTPException(404, detail={"code": "mcp_workspace_owned_no_binding"})
    bindings = await svc.binding_repo.list_for_server(server_id)
    return [
        WorkspaceBindingItem(workspace_id=b.workspace_id, enabled=b.enabled)
        for b in bindings
    ]


@router.put("/servers/{server_id}/bindings")
async def put_bindings(
    server_id: str, body: MCPBindingsReplace,
    svc: MCPServerService = Depends(get_mcp_service),
) -> list[WorkspaceBindingItem]:
    try:
        await svc.replace_bindings(
            server_id=server_id,
            bindings=[(b.workspace_id, b.enabled) for b in body.bindings],
        )
    except MCPServerNotFound:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"})
    except MCPWorkspaceOwnedNoBinding:
        raise HTTPException(400, detail={"code": "mcp_workspace_owned_no_binding"})
    bindings = await svc.binding_repo.list_for_server(server_id)
    return [
        WorkspaceBindingItem(workspace_id=b.workspace_id, enabled=b.enabled)
        for b in bindings
    ]
```

- [ ] **Step 2: Commit**

```bash
git add backend/cubeplex/api/routes/v1/admin_mcp.py
git commit -m "feat(mcp): add admin MCP routes (CRUD + bindings + test-connection)"
```

---

### Task 25: WS member MCP routes

**Files:**
- Create: `backend/cubeplex/api/routes/v1/ws_mcp.py`

- [ ] **Step 1: Implement routes**

```python
# backend/cubeplex/api/routes/v1/ws_mcp.py
"""Workspace-member MCP routes.

Path: /api/v1/ws/{wsId}/mcp/...
- list / create / detail / update / delete / refresh-tools / test-connection
- promote-to-org
- workspace-credential GET/PUT/DELETE (for credential_scope=workspace)
- my-credential GET/PUT/DELETE (for credential_scope=user)
"""

from fastapi import APIRouter, Depends, HTTPException, Path, status

from cubeplex.audit.sink import AuditSink
from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import get_request_context
from cubeplex.api.schemas.mcp import (
    MCPCredentialStatus,
    MCPCredentialUpsert,
    MCPPromoteRequest,
    MCPServerCreateWS,
    MCPServerListWS,
    MCPServerOut,
    MCPServerPatch,
    MCPTestConnectionRequest,
    MCPTestConnectionResponse,
)
from cubeplex.api.routes.v1.admin_mcp import _server_to_out
from cubeplex.mcp.dependencies import get_audit_sink, get_mcp_service
from cubeplex.mcp.exceptions import (
    MCPCredentialPathMismatch,
    MCPCredentialRequired,
    MCPOAuthNotImplemented,
    MCPServerAlreadyOrgWide,
    MCPServerNameConflict,
    MCPServerNotFound,
    MCPServerNotOwnedByWorkspace,
    MCPServerURLConflict,
    MCPShareCredentialOnlyForWorkspaceScope,
    MCPUserScopeCredentialForbidden,
)
from cubeplex.services.mcp import MCPServerService

router = APIRouter(prefix="/api/v1/ws/{wsId}/mcp", tags=["ws-mcp"])


@router.get("/servers")
async def list_servers(
    wsId: str = Path(...),
    svc: MCPServerService = Depends(get_mcp_service),
    ctx: RequestContext = Depends(get_request_context),
) -> MCPServerListWS:
    if ctx.workspace_id != wsId:
        raise HTTPException(403, detail={"code": "workspace_mismatch"})
    owned = await svc.server_repo.list_for_org(owner_workspace_id=wsId)
    via_binding = await svc.server_repo.list_for_workspace(wsId)
    via_binding_only = [s for s in via_binding if s.owner_workspace_id is None]
    return MCPServerListWS(
        owned=[_server_to_out(s, include_tools_cache=False) for s in owned],
        via_binding=[_server_to_out(s, include_tools_cache=False) for s in via_binding_only],
    )


@router.post("/servers", status_code=status.HTTP_201_CREATED)
async def create_server(
    body: MCPServerCreateWS,
    wsId: str = Path(...),
    svc: MCPServerService = Depends(get_mcp_service),
    ctx: RequestContext = Depends(get_request_context),
    audit: AuditSink = Depends(get_audit_sink),
) -> MCPServerOut:
    if ctx.workspace_id != wsId:
        raise HTTPException(403, detail={"code": "workspace_mismatch"})
    try:
        server = await svc.create(
            name=body.name, server_url=body.server_url,
            transport=body.transport, auth_method=body.auth_method,
            credential_scope=body.credential_scope,
            credential_plaintext=body.credential_plaintext,
            credential_name=body.credential_name,
            owner_workspace_id=wsId,
            headers=body.headers, timeout=body.timeout,
            sse_read_timeout=body.sse_read_timeout,
        )
    except MCPOAuthNotImplemented:
        raise HTTPException(409, detail={"code": "mcp_oauth_not_implemented"})
    except MCPServerURLConflict:
        raise HTTPException(409, detail={"code": "mcp_server_url_conflict"})
    except MCPServerNameConflict:
        raise HTTPException(409, detail={"code": "mcp_server_name_conflict"})
    except MCPCredentialRequired:
        raise HTTPException(400, detail={"code": "mcp_credential_required"})
    except MCPUserScopeCredentialForbidden:
        raise HTTPException(400, detail={"code": "mcp_user_scope_credential_forbidden"})
    await audit.record(
        event="mcp.server.created.ws", actor_user_id=ctx.user_id, org_id=ctx.org_id,
        target_id=server.id, details={"workspace_id": wsId},
    )
    return _server_to_out(server, include_tools_cache=True)


def _ensure_owned(server, wsId: str) -> None:
    if server is None:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"})
    if server.owner_workspace_id != wsId:
        raise HTTPException(403, detail={"code": "mcp_server_not_owned_by_workspace"})


@router.get("/servers/{server_id}")
async def get_server(
    server_id: str, wsId: str = Path(...),
    svc: MCPServerService = Depends(get_mcp_service),
    ctx: RequestContext = Depends(get_request_context),
) -> MCPServerOut:
    server = await svc.server_repo.get(server_id)
    if server is None:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"})
    # member can see owned + via-binding (read-only)
    if server.owner_workspace_id and server.owner_workspace_id != wsId:
        raise HTTPException(403, detail={"code": "mcp_server_not_owned_by_workspace"})
    return _server_to_out(server, include_tools_cache=True)


@router.patch("/servers/{server_id}")
async def patch_server(
    server_id: str, body: MCPServerPatch,
    wsId: str = Path(...),
    svc: MCPServerService = Depends(get_mcp_service),
) -> MCPServerOut:
    server = await svc.server_repo.get(server_id)
    _ensure_owned(server, wsId)
    try:
        server = await svc.update(
            server_id=server_id, name=body.name, server_url=body.server_url,
            transport=body.transport, credential_plaintext=body.credential_plaintext,
            headers=body.headers, timeout=body.timeout,
            sse_read_timeout=body.sse_read_timeout,
        )
    except MCPServerNameConflict:
        raise HTTPException(409, detail={"code": "mcp_server_name_conflict"})
    except MCPServerURLConflict:
        raise HTTPException(409, detail={"code": "mcp_server_url_conflict"})
    except MCPUserScopeCredentialForbidden:
        raise HTTPException(400, detail={"code": "mcp_user_scope_credential_forbidden"})
    return _server_to_out(server, include_tools_cache=True)


@router.delete("/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: str, wsId: str = Path(...),
    svc: MCPServerService = Depends(get_mcp_service),
) -> None:
    server = await svc.server_repo.get(server_id)
    _ensure_owned(server, wsId)
    await svc.delete(server_id=server_id)


@router.post("/servers/{server_id}/refresh-tools")
async def refresh_tools(
    server_id: str, wsId: str = Path(...),
    svc: MCPServerService = Depends(get_mcp_service),
) -> MCPServerOut:
    server = await svc.server_repo.get(server_id)
    _ensure_owned(server, wsId)
    server = await svc.refresh_tools(server_id=server_id)
    return _server_to_out(server, include_tools_cache=True)


@router.post("/test-connection")
async def test_connection(
    body: MCPTestConnectionRequest, wsId: str = Path(...),
    svc: MCPServerService = Depends(get_mcp_service),
    ctx: RequestContext = Depends(get_request_context),
) -> MCPTestConnectionResponse:
    if ctx.workspace_id != wsId:
        raise HTTPException(403, detail={"code": "workspace_mismatch"})
    if body.credential_scope == "org":
        raise HTTPException(403, detail={"code": "mcp_org_scope_admin_only"})
    try:
        success, tools, error = await svc.test_connection(
            server_url=body.server_url, transport=body.transport,
            auth_method=body.auth_method, credential_scope=body.credential_scope,
            credential_plaintext=body.credential_plaintext,
            headers=body.headers, timeout=body.timeout,
            sse_read_timeout=body.sse_read_timeout,
            owner_workspace_id=wsId,
        )
    except MCPOAuthNotImplemented:
        raise HTTPException(409, detail={"code": "mcp_oauth_not_implemented"})
    return MCPTestConnectionResponse(success=success, tools=tools, error=error)


@router.post("/servers/{server_id}/promote-to-org")
async def promote(
    server_id: str, body: MCPPromoteRequest,
    wsId: str = Path(...),
    svc: MCPServerService = Depends(get_mcp_service),
) -> MCPServerOut:
    server = await svc.server_repo.get(server_id)
    _ensure_owned(server, wsId)
    try:
        server = await svc.promote_to_org(
            server_id=server_id, share_credential=body.share_credential,
        )
    except MCPServerAlreadyOrgWide:
        raise HTTPException(409, detail={"code": "mcp_server_already_org_wide"})
    except MCPShareCredentialOnlyForWorkspaceScope:
        raise HTTPException(400, detail={
            "code": "mcp_share_credential_only_for_workspace_scope"
        })
    return _server_to_out(server, include_tools_cache=True)


# --- Workspace-shared credential management ---

@router.get("/servers/{server_id}/workspace-credential")
async def get_workspace_credential(
    server_id: str, wsId: str = Path(...),
    svc: MCPServerService = Depends(get_mcp_service),
) -> MCPCredentialStatus:
    server = await svc.server_repo.get(server_id)
    if server is None:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"})
    if server.credential_scope != "workspace":
        raise HTTPException(400, detail={"code": "mcp_credential_path_mismatch"})
    has = await svc.has_workspace_credential(server_id=server_id, workspace_id=wsId)
    return MCPCredentialStatus(has_value=has)


@router.put("/servers/{server_id}/workspace-credential", status_code=status.HTTP_204_NO_CONTENT)
async def put_workspace_credential(
    server_id: str, body: MCPCredentialUpsert,
    wsId: str = Path(...),
    svc: MCPServerService = Depends(get_mcp_service),
) -> None:
    try:
        await svc.set_workspace_credential(
            server_id=server_id, workspace_id=wsId,
            plaintext=body.plaintext, credential_name=body.name,
        )
    except MCPServerNotFound:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"})
    except MCPCredentialPathMismatch:
        raise HTTPException(400, detail={"code": "mcp_credential_path_mismatch"})


@router.delete("/servers/{server_id}/workspace-credential", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace_credential(
    server_id: str, wsId: str = Path(...),
    svc: MCPServerService = Depends(get_mcp_service),
) -> None:
    await svc.delete_workspace_credential(server_id=server_id, workspace_id=wsId)


# --- Per-user credential management ---

@router.get("/servers/{server_id}/my-credential")
async def get_my_credential(
    server_id: str, wsId: str = Path(...),
    svc: MCPServerService = Depends(get_mcp_service),
    ctx: RequestContext = Depends(get_request_context),
) -> MCPCredentialStatus:
    server = await svc.server_repo.get(server_id)
    if server is None:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"})
    if server.credential_scope != "user":
        raise HTTPException(400, detail={"code": "mcp_credential_path_mismatch"})
    has = await svc.has_user_credential(server_id=server_id, user_id=ctx.user_id)
    return MCPCredentialStatus(has_value=has)


@router.put("/servers/{server_id}/my-credential", status_code=status.HTTP_204_NO_CONTENT)
async def put_my_credential(
    server_id: str, body: MCPCredentialUpsert,
    wsId: str = Path(...),
    svc: MCPServerService = Depends(get_mcp_service),
    ctx: RequestContext = Depends(get_request_context),
) -> None:
    try:
        await svc.set_user_credential(
            server_id=server_id, user_id=ctx.user_id,
            plaintext=body.plaintext, credential_name=body.name,
        )
    except MCPServerNotFound:
        raise HTTPException(404, detail={"code": "mcp_server_not_found"})
    except MCPCredentialPathMismatch:
        raise HTTPException(400, detail={"code": "mcp_credential_path_mismatch"})


@router.delete("/servers/{server_id}/my-credential", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_credential(
    server_id: str, wsId: str = Path(...),
    svc: MCPServerService = Depends(get_mcp_service),
    ctx: RequestContext = Depends(get_request_context),
) -> None:
    await svc.delete_user_credential(server_id=server_id, user_id=ctx.user_id)
```

- [ ] **Step 2: Commit**

```bash
git add backend/cubeplex/api/routes/v1/ws_mcp.py
git commit -m "feat(mcp): add ws member MCP routes (CRUD + promote + cred mgmt)"
```

---

### Task 26: Mount routers + smoke test

**Files:**
- Modify: `backend/cubeplex/api/app.py` (or wherever routers are mounted — check for existing pattern)
- Create: `backend/tests/e2e/test_mcp_routers_mounted.py`

- [ ] **Step 1: Find existing router mount location**

```bash
grep -rn "include_router" backend/cubeplex/api/ | head
```

- [ ] **Step 2: Mount new routers in the file that mounts others**

```python
# backend/cubeplex/api/app.py (or routes/__init__.py — wherever others are)
from cubeplex.api.routes.v1 import admin_mcp, ws_mcp

app.include_router(admin_mcp.router)
app.include_router(ws_mcp.router)
```

- [ ] **Step 3: Smoke test that routes are registered**

```python
# backend/tests/e2e/test_mcp_routers_mounted.py
"""Smoke test: routes are reachable (auth gate honored)."""

import pytest


async def test_admin_servers_list_requires_auth(client) -> None:
    """Without auth cookie, must 401."""
    resp = await client.get("/api/v1/admin/mcp/servers")
    assert resp.status_code == 401


async def test_ws_servers_list_requires_auth(client) -> None:
    resp = await client.get("/api/v1/ws/some-ws/mcp/servers")
    assert resp.status_code == 401


async def test_admin_endpoint_404_for_unknown_server(authed_admin_client) -> None:
    resp = await authed_admin_client.get(
        "/api/v1/admin/mcp/servers/nonexistent-id"
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "mcp_server_not_found"
```

(`client` and `authed_admin_client` fixtures: use existing patterns in `tests/conftest.py`. If `authed_admin_client` does not exist, model it on `authed_client` or whatever the codebase uses for an admin user — see `tests/e2e/test_admin_*.py`.)

- [ ] **Step 4: Run, expect pass**

```bash
uv run pytest tests/e2e/test_mcp_routers_mounted.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/api/app.py \
        backend/tests/e2e/test_mcp_routers_mounted.py
git commit -m "feat(mcp): mount admin/ws MCP routers + smoke E2E"
```

---

## Phase E · Reference Fixture + Runtime Wiring (Stage 5)

### Task 27: Reference MCP server fixture

**Files:**
- Create: `backend/tests/fixtures/__init__.py` (if missing)
- Create: `backend/tests/fixtures/reference_mcp_server.py`

- [ ] **Step 1: Add `mcp` python SDK as test extra (only if not present)**

```bash
cd backend
uv add --dev mcp
uv run python -c "import mcp; print(mcp.__version__)"
```

- [ ] **Step 2: Implement fixture**

```python
# backend/tests/fixtures/reference_mcp_server.py
"""Reference MCP server for E2E tests.

Spawns a streamable-http MCP server in a subprocess, returns base URL.
Three auth modes:
- "none": no auth header expected
- "bearer-static": expects Authorization: Bearer <STATIC_TOKEN>
- "bearer-jwt-verify": expects Authorization: Bearer <jwt>; verifies HS256 with shared secret
"""

import asyncio
import json
import socket
import subprocess
import sys
import textwrap
import time
from contextlib import contextmanager
from dataclasses import dataclass

import httpx
import pytest


@dataclass
class _RefServer:
    base_url: str
    process: subprocess.Popen


def _alloc_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _server_script(auth_mode: str, jwt_secret: str | None, static_token: str | None) -> str:
    """Return Python source for a tiny streamable-http MCP server."""
    return textwrap.dedent(f"""
        import asyncio, json, os, sys
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import Tool, TextContent
        # NOTE: a real impl would use mcp.server.streamable_http; for cross-version
        # safety, this fixture uses a small starlette+uvicorn shim.
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse, Response
        from starlette.routing import Route
        import uvicorn
        import jwt as _jwt

        AUTH_MODE = {auth_mode!r}
        STATIC_TOKEN = {static_token!r}
        JWT_SECRET = {jwt_secret!r}

        TOOLS = [
            {{"name": "echo", "description": "echoes input",
              "inputSchema": {{"type": "object", "properties":
                {{"text": {{"type": "string"}}}}, "required": ["text"]}}}},
            {{"name": "ping", "description": "responds pong",
              "inputSchema": {{"type": "object", "properties": {{}}}}}},
        ]

        def _verify_auth(request: Request) -> tuple[bool, str]:
            if AUTH_MODE == "none":
                return True, ""
            header = request.headers.get("authorization", "")
            if not header.startswith("Bearer "):
                return False, "missing bearer"
            tok = header[len("Bearer "):]
            if AUTH_MODE == "bearer-static":
                return (tok == STATIC_TOKEN, "" if tok == STATIC_TOKEN else "static mismatch")
            if AUTH_MODE == "bearer-jwt-verify":
                try:
                    claims = _jwt.decode(tok, JWT_SECRET, algorithms=["HS256"])
                    if claims.get("iss") != "cubeplex":
                        return False, f"bad iss: {{claims.get('iss')}}"
                    return True, json.dumps(claims)
                except Exception as e:
                    return False, f"jwt: {{e}}"
            return False, f"unknown auth mode {{AUTH_MODE}}"


        async def list_tools_endpoint(request: Request):
            ok, _ = _verify_auth(request)
            if not ok:
                return Response("unauthorized", status_code=401)
            return JSONResponse({{"tools": TOOLS}})


        async def call_tool_endpoint(request: Request):
            ok, claims = _verify_auth(request)
            if not ok:
                return Response("unauthorized", status_code=401)
            body = await request.json()
            name = body.get("name")
            args = body.get("arguments", {{}})
            if name == "echo":
                return JSONResponse({{"content": [{{"type": "text",
                    "text": args.get("text", "")}}]}})
            if name == "ping":
                return JSONResponse({{"content": [{{"type": "text",
                    "text": f"pong (claims={{claims}})"}}]}})
            return JSONResponse({{"error": f"unknown tool {{name}}"}}, status_code=404)


        # Minimal MCP-compat HTTP surface mimicking what langchain_mcp_adapters
        # discovers via "streamable_http" transport. This is a faithful enough
        # stand-in for E2E discovery + invocation testing.
        app = Starlette(routes=[
            Route("/mcp/tools/list", list_tools_endpoint, methods=["GET", "POST"]),
            Route("/mcp/tools/call", call_tool_endpoint, methods=["POST"]),
        ])

        if __name__ == "__main__":
            port = int(sys.argv[1])
            uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    """)


@pytest.fixture
def reference_mcp_server():
    """Yield a callable factory(auth_mode, **kwargs) -> _RefServer."""
    spawned: list[_RefServer] = []

    @contextmanager
    def _spawn(
        auth_mode: str = "none",
        *,
        jwt_secret: str | None = None,
        static_token: str | None = None,
    ):
        port = _alloc_port()
        script = _server_script(auth_mode, jwt_secret, static_token)
        # Write script to temp file so subprocess can run it
        import tempfile, os
        tf = tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        )
        tf.write(script)
        tf.flush()
        proc = subprocess.Popen(
            [sys.executable, tf.name, str(port)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        # Wait for port to be reachable (max 5s)
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.2)
                    s.connect(("127.0.0.1", port))
                break
            except Exception:
                time.sleep(0.05)
        else:
            proc.terminate()
            os.unlink(tf.name)
            raise RuntimeError("reference MCP server failed to start")

        rs = _RefServer(base_url=f"http://127.0.0.1:{port}", process=proc)
        spawned.append(rs)
        try:
            yield rs
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
            os.unlink(tf.name)

    yield _spawn

    for rs in spawned:
        if rs.process.poll() is None:
            rs.process.kill()
```

> **Note for executing engineer:** the script above uses an HTTP shim rather than the real MCP streamable-http transport because langchain-mcp-adapters' transport handshake is verbose to reproduce in a tiny fixture. For our tests, we exercise discovery + tool invocation through `MultiServerMCPClient`; if it cannot speak to this shim directly, replace `Starlette` with `mcp.server.streamable_http.streamable_http_server` once the impl version is verified.

- [ ] **Step 3: Smoke test the fixture**

```python
# backend/tests/fixtures/test_reference_server_smoke.py
"""Verify the reference server fixture starts and serves tool list."""

import httpx
import pytest


async def test_fixture_serves_tools_list(reference_mcp_server) -> None:
    with reference_mcp_server(auth_mode="none") as srv:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{srv.base_url}/mcp/tools/list")
        assert resp.status_code == 200
        data = resp.json()
        assert "tools" in data
        assert any(t["name"] == "echo" for t in data["tools"])
```

- [ ] **Step 4: Run smoke test**

```bash
uv run pytest tests/fixtures/test_reference_server_smoke.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/tests/fixtures/__init__.py \
        backend/tests/fixtures/reference_mcp_server.py \
        backend/tests/fixtures/test_reference_server_smoke.py
git commit -m "test(mcp): add reference MCP server fixture (3 auth modes)"
```

> **Caveat:** if `langchain-mcp-adapters` cannot connect to the Starlette shim due to transport-specific framing, the executing engineer must swap the shim for the genuine `mcp.server.streamable_http` API. See spec Task 27 risk.

---

### Task 28: Refactor `MCPManager` — split legacy + DB paths

**Files:**
- Modify: `backend/cubeplex/mcp/client.py`

- [ ] **Step 1: Refactor existing class**

Replace `backend/cubeplex/mcp/client.py` content (existing config-driven `MCPManager`) with the dual-path version:

```python
# backend/cubeplex/mcp/client.py
"""MCPManager — two independent loading paths.

Legacy: read config.yaml mcp.servers at startup, share across all workspaces.
DB:     per (ws, user) load from DB MCPServer rows + workspace bindings.
"""

from typing import Any, cast

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import Connection
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession


def _build_legacy_connection_params(
    server_name: str, server_config: dict[str, Any],
) -> dict[str, Any] | None:
    """Same shape as previous _build_connection_params — kept for legacy config path."""
    transport = server_config.get("transport")
    if transport in ("streamable_http", "sse"):
        url = server_config.get("url")
        if not url:
            logger.warning("Legacy MCP '{}': missing url, skipping", server_name)
            return None
        params: dict[str, Any] = {"url": url, "transport": transport}
        key = server_config.get("key")
        if key:
            params["headers"] = {"Authorization": f"Bearer {key}"}
        return params
    if transport == "stdio":
        cmd = server_config.get("command")
        if not cmd:
            return None
        return {"command": cmd, "args": server_config.get("args", []),
                "transport": "stdio", "env": server_config.get("env")}
    return None


class MCPManager:
    """Static methods only — no per-instance state."""

    _legacy_cache: list[BaseTool] | None = None

    @classmethod
    async def load_legacy_config_servers(cls) -> list[BaseTool]:
        """Load mcp.servers from config.yaml ONCE at process startup. Cached."""
        if cls._legacy_cache is not None:
            return cls._legacy_cache

        from cubeplex.config import config
        if not config.get("mcp.enabled", False):
            cls._legacy_cache = []
            return cls._legacy_cache

        servers = config.get("mcp.servers", {}) or {}
        all_tools: list[BaseTool] = []
        for server_name, sc in servers.items():
            if not sc.get("enabled", True):
                continue
            params = _build_legacy_connection_params(server_name, sc)
            if params is None:
                continue
            try:
                client = MultiServerMCPClient({server_name: cast(Connection, params)})
                tools = await client.get_tools()
                # Optional per-tool filter
                tool_filter = sc.get("tools")
                if tool_filter:
                    names = {
                        t if isinstance(t, str) else t["name"]
                        for t in tool_filter
                    }
                    tools = [t for t in tools if t.name in names]
                all_tools.extend(tools)
                logger.info("Legacy MCP '{}': loaded {} tools", server_name, len(tools))
            except Exception as e:
                logger.warning("Legacy MCP '{}' failed: {}", server_name, e)
        cls._legacy_cache = all_tools
        if all_tools:
            logger.info(
                "Loaded {} legacy MCP tools from config.yaml; "
                "consider migrating via /admin/mcp",
                len(all_tools),
            )
        return cls._legacy_cache

    @classmethod
    def legacy_tools_cache(cls) -> list[BaseTool]:
        """Synchronous accessor; assumes load_legacy_config_servers ran at startup."""
        return cls._legacy_cache or []
```

- [ ] **Step 2: Wire legacy loader at app startup**

In `backend/cubeplex/api/app.py` lifespan startup:

```python
# after _build_encryption_backend() and signer registration
from cubeplex.mcp.client import MCPManager
await MCPManager.load_legacy_config_servers()
```

- [ ] **Step 3: Run existing legacy tests to confirm no regression**

```bash
uv run pytest tests/ -k mcp -v
```

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/mcp/client.py backend/cubeplex/api/app.py
git commit -m "refactor(mcp): split MCPManager into legacy-cache + future db path"
```

---

### Task 29: Runtime — `load_db_servers_for_workspace`

**Files:**
- Create: `backend/cubeplex/mcp/runtime.py`

- [ ] **Step 1: Implement**

```python
# backend/cubeplex/mcp/runtime.py
"""Per-(workspace, user) MCP tool assembly for create_cubeplex_agent."""

from datetime import timedelta

from langchain_core.tools import BaseTool
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.credentials.exceptions import CredentialNotFound
from cubeplex.mcp.connection_params import build_connection_params
from cubeplex.mcp.discovery import construct_basetools_from_cache
from cubeplex.mcp.user_token import MCPUserTokenSigner
from cubeplex.models import MCPServer
from cubeplex.repositories.mcp import (
    MCPServerRepository,
    UserMCPCredentialRepository,
    WorkspaceMCPCredentialRepository,
)
from cubeplex.services.credential import CredentialService

_USER_TOKEN_TTL = timedelta(minutes=5)
_CREDENTIAL_KIND_MCP = "mcp_server"


async def load_db_servers_for_workspace(
    *,
    org_id: str,
    workspace_id: str,
    user_id: str,
    cred_service: CredentialService,
    signer: MCPUserTokenSigner,
    session: AsyncSession,
) -> list[BaseTool]:
    """Resolve DB MCP server rows visible to (ws, user) and assemble BaseTools.

    Soft-skips any server whose credential cannot be resolved (e.g. user-scope
    not yet filled by this user, decrypt failure, signer error).
    """
    server_repo = MCPServerRepository(session, org_id=org_id)
    ws_cred_repo = WorkspaceMCPCredentialRepository(session, org_id=org_id)
    user_cred_repo = UserMCPCredentialRepository(session, org_id=org_id)

    servers = await server_repo.list_for_workspace(workspace_id)

    tools: list[BaseTool] = []
    for s in servers:
        try:
            token = await _resolve_token(
                s, user_id=user_id, workspace_id=workspace_id,
                cred_service=cred_service, signer=signer,
                ws_cred_repo=ws_cred_repo, user_cred_repo=user_cred_repo,
            )
        except CredentialNotFound:
            logger.warning(
                "MCP server '{}' missing credential (user={}, ws={}); skipping",
                s.name, user_id, workspace_id,
            )
            continue
        except Exception as e:
            logger.warning("MCP server '{}' token resolution failed: {}; skipping",
                           s.name, e)
            continue
        if token is None and s.credential_scope != "none":
            # ws/user not provided creds — silently skip
            continue
        try:
            connection_params = build_connection_params(s, credential_or_token=token)
            tools.extend(construct_basetools_from_cache(s.tools_cache, connection_params))
        except Exception as e:
            logger.warning(
                "MCP server '{}' BaseTool construction failed: {}; skipping",
                s.name, e,
            )
    return tools


async def _resolve_token(
    server: MCPServer, *,
    user_id: str, workspace_id: str,
    cred_service: CredentialService,
    signer: MCPUserTokenSigner,
    ws_cred_repo: WorkspaceMCPCredentialRepository,
    user_cred_repo: UserMCPCredentialRepository,
) -> str | None:
    if server.credential_scope == "org":
        if server.credential_id is None:
            return None
        return await cred_service.get_decrypted(
            credential_id=server.credential_id,
            requesting_kind=_CREDENTIAL_KIND_MCP,
        )
    if server.credential_scope == "workspace":
        ws_cred = await ws_cred_repo.get(
            workspace_id=workspace_id, mcp_server_id=server.id,
        )
        if ws_cred is None:
            return None
        return await cred_service.get_decrypted(
            credential_id=ws_cred.credential_id,
            requesting_kind=_CREDENTIAL_KIND_MCP,
        )
    if server.credential_scope == "user":
        user_cred = await user_cred_repo.get(
            user_id=user_id, mcp_server_id=server.id,
        )
        if user_cred is None:
            return None
        return await cred_service.get_decrypted(
            credential_id=user_cred.credential_id,
            requesting_kind=_CREDENTIAL_KIND_MCP,
        )
    if server.credential_scope == "none":
        return await signer.sign(
            user_id=user_id, org_id=server.org_id,
            workspace_id=workspace_id, mcp_server_id=server.id,
            ttl=_USER_TOKEN_TTL,
        )
    return None  # unknown scope (defensive)
```

- [ ] **Step 2: Commit**

```bash
git add backend/cubeplex/mcp/runtime.py
git commit -m "feat(mcp): add per-(ws, user) DB server loader for runtime"
```

---

### Task 30: RunManager DB MCP tool assembly

**Files:**
- Modify: `backend/cubeplex/streams/run_manager.py`
- Test: `backend/tests/unit/test_run_streaming.py`

- [ ] **Step 1: Add a unit test that DB MCP tools are appended per run**

Append a test near the existing `create_cubeplex_agent` monkeypatch tests:

```python
# backend/tests/unit/test_run_streaming.py

@pytest.mark.asyncio
async def test_run_manager_appends_db_mcp_tools(monkeypatch, fake_app, fake_redis) -> None:
    """RunManager loads DB MCP tools with RunContext and passes them into the agent."""
    from langchain_core.tools import tool

    captured_tools: list[str] = []

    @tool
    def db_echo(value: str) -> str:
        """Echo a value."""
        return value

    async def fake_load_db_servers_for_workspace(**kwargs):
        assert kwargs["org_id"] == "org-1"
        assert kwargs["workspace_id"] == "ws-1"
        assert kwargs["user_id"] == "user-1"
        return [db_echo]

    def fake_create_cubeplex_agent(**kwargs):
        captured_tools.extend(t.name for t in kwargs["tools"])
        return _FakeAgent()

    monkeypatch.setattr(
        "cubeplex.mcp.runtime.load_db_servers_for_workspace",
        fake_load_db_servers_for_workspace,
    )
    monkeypatch.setattr("cubeplex.agents.graph.create_cubeplex_agent", fake_create_cubeplex_agent)

    manager = RunManager(
        app=fake_app,
        redis=fake_redis,
        key_prefix="test",
        run_event_ttl_seconds=60,
    )
    run_id = await manager.start_run(
        conversation_id="conv-1",
        content="hello",
        attachments=[],
        ctx=RunContext(user_id="user-1", org_id="org-1", workspace_id="ws-1"),
    )
    await manager.drain(timeout_seconds=5)

    assert run_id
    assert "db_echo" in captured_tools
```

Adapt fixture names to the existing file; keep the assertion focused on the `tools` list passed
to `create_cubeplex_agent()`.

- [ ] **Step 2: Run, expect fail**

```bash
cd backend
uv run pytest tests/unit/test_run_streaming.py::test_run_manager_appends_db_mcp_tools -v
```

Expected: fail because `RunManager` has not called `load_db_servers_for_workspace()`.

- [ ] **Step 3: Wire DB MCP tools in `RunManager`**

In `_execute_run`, after `tools = get_registry().list_tools()` and before
`create_cubeplex_agent(...)`, add:

```python
from cubeplex.credentials.dependencies import build_credential_service
from cubeplex.db.engine import async_session_maker
from cubeplex.mcp.dependencies import build_user_token_signer
from cubeplex.mcp.runtime import load_db_servers_for_workspace

try:
    async with async_session_maker() as mcp_session:
        cred_service = build_credential_service(
            mcp_session,
            self._app.state.encryption_backend,
            org_id=ctx.org_id,
            actor_user_id=ctx.user_id,
        )
        signer = build_user_token_signer()
        db_mcp_tools = await load_db_servers_for_workspace(
            org_id=ctx.org_id,
            workspace_id=ctx.workspace_id,
            user_id=ctx.user_id,
            cred_service=cred_service,
            signer=signer,
            session=mcp_session,
        )
    tools = [*tools, *db_mcp_tools]
except Exception as exc:
    logger.warning("DB MCP tool assembly failed; continuing without DB MCP tools: {}", exc)
```

Keep `create_cubeplex_agent()` synchronous and leave its signature unchanged.

- [ ] **Step 4: Run, expect pass**

```bash
cd backend
uv run pytest tests/unit/test_run_streaming.py::test_run_manager_appends_db_mcp_tools -v
```

Expected: pass.

- [ ] **Step 5: Run existing graph/run tests**

```bash
cd backend
uv run pytest tests/unit/test_graph.py tests/unit/test_run_streaming.py -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/streams/run_manager.py backend/tests/unit/test_run_streaming.py
git commit -m "feat(mcp): append db-backed MCP tools per run"
```

---

### Task 31: Conversation route regression check

**Files:**
- No production file changes expected.

- [ ] **Step 1: Confirm conversations route still only starts runs**

```bash
cd backend
grep -n "run_manager.start_run\\|create_cubeplex_agent" cubeplex/api/routes/v1/conversations.py
```

Expected: `run_manager.start_run` is present; `create_cubeplex_agent` is absent.

- [ ] **Step 2: Run conversation regression tests**

```bash
cd backend
uv run pytest tests/e2e/test_agents.py tests/e2e/test_conversations.py -v
```

Expected: existing tests pass with no DB MCP rows configured.

- [ ] **Step 3: Commit only if tests required fixture updates**

```bash
git status --short
# If no files changed, skip commit.
# If test fixtures changed:
git add backend/tests
git commit -m "test(mcp): keep conversation run startup compatible"
```

---

### Task 32: Admin MCP CRUD E2E

**Files:**
- Create: `backend/tests/e2e/test_admin_mcp_crud.py`

- [ ] **Step 1: Write E2E**

```python
# backend/tests/e2e/test_admin_mcp_crud.py
"""Admin CRUD lifecycle: create org-shared / user / none scope; refresh-tools; delete.

Uses reference_mcp_server fixture; admin_authed_client wraps cookie auth as org admin.
"""

import pytest


async def test_create_org_scope_static_with_reference_server(
    admin_authed_client, reference_mcp_server,
) -> None:
    with reference_mcp_server(auth_mode="bearer-static", static_token="tok-123") as srv:
        resp = await admin_authed_client.post(
            "/api/v1/admin/mcp/servers",
            json={
                "name": "RefServer",
                "server_url": f"{srv.base_url}/mcp",
                "transport": "streamable_http",
                "auth_method": "static",
                "credential_scope": "org",
                "credential_plaintext": "tok-123",
                "credential_name": "Ref token",
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "RefServer"
    assert body["credential_scope"] == "org"
    assert body["authed"] is True
    assert any(t["name"] == "echo" for t in body["tools_cache"])
    assert "credential_plaintext" not in body  # never echoed


async def test_create_none_scope_works_without_credential(
    admin_authed_client, reference_mcp_server,
) -> None:
    with reference_mcp_server(auth_mode="none") as srv:
        resp = await admin_authed_client.post(
            "/api/v1/admin/mcp/servers",
            json={
                "name": "Public", "server_url": f"{srv.base_url}/mcp",
                "transport": "streamable_http", "auth_method": "none",
                "credential_scope": "none",
            },
        )
    assert resp.status_code == 201
    assert resp.json()["credential"] is None


async def test_list_filters_by_scope(admin_authed_client, reference_mcp_server) -> None:
    # Create one of each scope
    with reference_mcp_server(auth_mode="none") as srv:
        await admin_authed_client.post("/api/v1/admin/mcp/servers", json={
            "name": "ns1", "server_url": f"{srv.base_url}/mcp",
            "transport": "streamable_http", "auth_method": "none",
            "credential_scope": "none",
        })
    with reference_mcp_server(auth_mode="bearer-static", static_token="t") as srv:
        await admin_authed_client.post("/api/v1/admin/mcp/servers", json={
            "name": "os1", "server_url": f"{srv.base_url}/mcp",
            "transport": "streamable_http", "auth_method": "static",
            "credential_scope": "org", "credential_plaintext": "t",
        })
    resp = await admin_authed_client.get("/api/v1/admin/mcp/servers?scope=org")
    assert resp.status_code == 200
    items = resp.json()
    assert all(s["credential_scope"] == "org" for s in items)
    assert any(s["name"] == "os1" for s in items)


async def test_patch_updates_name_and_rediscovers(
    admin_authed_client, reference_mcp_server,
) -> None:
    with reference_mcp_server(auth_mode="none") as srv:
        create_resp = await admin_authed_client.post("/api/v1/admin/mcp/servers", json={
            "name": "old", "server_url": f"{srv.base_url}/mcp",
            "transport": "streamable_http", "auth_method": "none",
            "credential_scope": "none",
        })
        sid = create_resp.json()["id"]
        patch_resp = await admin_authed_client.patch(
            f"/api/v1/admin/mcp/servers/{sid}",
            json={"name": "new"},
        )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["name"] == "new"


async def test_delete_cascades(admin_authed_client, reference_mcp_server) -> None:
    with reference_mcp_server(auth_mode="bearer-static", static_token="t") as srv:
        create_resp = await admin_authed_client.post("/api/v1/admin/mcp/servers", json={
            "name": "to-delete", "server_url": f"{srv.base_url}/mcp",
            "transport": "streamable_http", "auth_method": "static",
            "credential_scope": "org", "credential_plaintext": "t",
        })
        sid = create_resp.json()["id"]
    del_resp = await admin_authed_client.delete(f"/api/v1/admin/mcp/servers/{sid}")
    assert del_resp.status_code == 204
    get_resp = await admin_authed_client.get(f"/api/v1/admin/mcp/servers/{sid}")
    assert get_resp.status_code == 404


async def test_credential_never_returned_in_plaintext(
    admin_authed_client, reference_mcp_server,
) -> None:
    with reference_mcp_server(auth_mode="bearer-static", static_token="ghp_secret") as srv:
        create_resp = await admin_authed_client.post("/api/v1/admin/mcp/servers", json={
            "name": "secret", "server_url": f"{srv.base_url}/mcp",
            "transport": "streamable_http", "auth_method": "static",
            "credential_scope": "org", "credential_plaintext": "ghp_secret",
        })
        sid = create_resp.json()["id"]
        get_resp = await admin_authed_client.get(f"/api/v1/admin/mcp/servers/{sid}")
    body = get_resp.json()
    assert "ghp_secret" not in resp_text(body)
    assert body["credential"]["has_value"] is True


def resp_text(obj) -> str:
    import json
    return json.dumps(obj)
```

- [ ] **Step 2: Run, expect pass**

```bash
uv run pytest tests/e2e/test_admin_mcp_crud.py -v
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_admin_mcp_crud.py
git commit -m "test(mcp): admin MCP CRUD E2E (scopes / list filter / cascade / write-only cred)"
```

---

### Task 33: WS member CRUD E2E

**Files:**
- Create: `backend/tests/e2e/test_ws_mcp_crud.py`

- [ ] **Step 1: Write**

```python
# backend/tests/e2e/test_ws_mcp_crud.py
"""Workspace member self-service CRUD E2E."""

import pytest


async def test_member_creates_workspace_scope_server(
    ws_member_client, reference_mcp_server, ws_id,
) -> None:
    with reference_mcp_server(auth_mode="bearer-static", static_token="ws-tok") as srv:
        resp = await ws_member_client.post(
            f"/api/v1/ws/{ws_id}/mcp/servers",
            json={
                "name": "MyTool",
                "server_url": f"{srv.base_url}/mcp",
                "transport": "streamable_http",
                "auth_method": "static",
                "credential_scope": "workspace",
                "credential_plaintext": "ws-tok",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["owner_workspace_id"] == ws_id
    assert body["credential_scope"] == "workspace"


async def test_member_cannot_create_org_scope_via_ws_path(
    ws_member_client, reference_mcp_server, ws_id,
) -> None:
    with reference_mcp_server(auth_mode="none") as srv:
        resp = await ws_member_client.post(
            f"/api/v1/ws/{ws_id}/mcp/servers",
            json={
                "name": "x", "server_url": f"{srv.base_url}/mcp",
                "transport": "streamable_http", "auth_method": "none",
                "credential_scope": "org",  # not allowed via ws path
            },
        )
    assert resp.status_code == 422  # pydantic Literal rejects "org"


async def test_other_workspace_member_cannot_edit_owned_server(
    ws_member_client, ws_member_other_client, reference_mcp_server,
    ws_id, ws_id_other,
) -> None:
    with reference_mcp_server(auth_mode="none") as srv:
        create_resp = await ws_member_client.post(
            f"/api/v1/ws/{ws_id}/mcp/servers",
            json={
                "name": "private", "server_url": f"{srv.base_url}/mcp",
                "transport": "streamable_http", "auth_method": "none",
                "credential_scope": "none",
            },
        )
        sid = create_resp.json()["id"]
    # Other workspace tries to PATCH
    resp = await ws_member_other_client.patch(
        f"/api/v1/ws/{ws_id_other}/mcp/servers/{sid}",
        json={"name": "stolen"},
    )
    assert resp.status_code in (403, 404)


async def test_list_returns_owned_and_via_binding(
    admin_authed_client, ws_member_client, reference_mcp_server, ws_id,
) -> None:
    """Admin creates org-wide + binds to ws; member sees it under via_binding (read-only)."""
    with reference_mcp_server(auth_mode="none") as srv:
        admin_resp = await admin_authed_client.post(
            "/api/v1/admin/mcp/servers",
            json={
                "name": "OrgWide", "server_url": f"{srv.base_url}/mcp",
                "transport": "streamable_http", "auth_method": "none",
                "credential_scope": "none",
            },
        )
        sid = admin_resp.json()["id"]
        await admin_authed_client.put(
            f"/api/v1/admin/mcp/servers/{sid}/bindings",
            json={"bindings": [{"workspace_id": ws_id, "enabled": True}]},
        )
    list_resp = await ws_member_client.get(f"/api/v1/ws/{ws_id}/mcp/servers")
    body = list_resp.json()
    assert any(s["id"] == sid for s in body["via_binding"])
    assert all(s["id"] != sid for s in body["owned"])
```

(`ws_member_client`, `ws_member_other_client`, `ws_id`, `ws_id_other` fixtures: model on existing patterns. Look at `tests/e2e/test_ws_skills.py` if available, else build in conftest.)

- [ ] **Step 2: Run, expect pass**

```bash
uv run pytest tests/e2e/test_ws_mcp_crud.py -v
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_ws_mcp_crud.py
git commit -m "test(mcp): ws member CRUD E2E (workspace-private + via-binding visibility)"
```

---

### Task 34: Promote E2E (α + β)

**Files:**
- Create: `backend/tests/e2e/test_mcp_promote.py`

- [ ] **Step 1: Write**

```python
# backend/tests/e2e/test_mcp_promote.py
"""Promote workspace-private → org-wide. α (share key) and β (keep key)."""

import pytest


async def test_promote_alpha_other_ws_can_use_immediately(
    ws_member_client, ws_member_other_client, admin_authed_client,
    reference_mcp_server, ws_id, ws_id_other,
) -> None:
    with reference_mcp_server(auth_mode="bearer-static", static_token="tok") as srv:
        create_resp = await ws_member_client.post(
            f"/api/v1/ws/{ws_id}/mcp/servers",
            json={
                "name": "P-A", "server_url": f"{srv.base_url}/mcp",
                "transport": "streamable_http", "auth_method": "static",
                "credential_scope": "workspace", "credential_plaintext": "tok",
            },
        )
        sid = create_resp.json()["id"]
        promote_resp = await ws_member_client.post(
            f"/api/v1/ws/{ws_id}/mcp/servers/{sid}/promote-to-org",
            json={"share_credential": True},
        )
        assert promote_resp.status_code == 200
        promoted = promote_resp.json()
        assert promoted["owner_workspace_id"] is None
        assert promoted["credential_scope"] == "org"

        # Admin binds to other ws
        await admin_authed_client.put(
            f"/api/v1/admin/mcp/servers/{sid}/bindings",
            json={"bindings": [
                {"workspace_id": ws_id, "enabled": True},
                {"workspace_id": ws_id_other, "enabled": True},
            ]},
        )

    # Other ws's member sees server under via_binding without filling cred
    list_resp = await ws_member_other_client.get(
        f"/api/v1/ws/{ws_id_other}/mcp/servers"
    )
    assert any(s["id"] == sid for s in list_resp.json()["via_binding"])


async def test_promote_beta_other_ws_must_fill_credential(
    ws_member_client, ws_member_other_client, admin_authed_client,
    reference_mcp_server, ws_id, ws_id_other,
) -> None:
    with reference_mcp_server(auth_mode="bearer-static", static_token="tok") as srv:
        create_resp = await ws_member_client.post(
            f"/api/v1/ws/{ws_id}/mcp/servers",
            json={
                "name": "P-B", "server_url": f"{srv.base_url}/mcp",
                "transport": "streamable_http", "auth_method": "static",
                "credential_scope": "workspace", "credential_plaintext": "tok",
            },
        )
        sid = create_resp.json()["id"]
        promote_resp = await ws_member_client.post(
            f"/api/v1/ws/{ws_id}/mcp/servers/{sid}/promote-to-org",
            json={"share_credential": False},
        )
        assert promote_resp.status_code == 200
        promoted = promote_resp.json()
        assert promoted["credential_scope"] == "workspace"
        await admin_authed_client.put(
            f"/api/v1/admin/mcp/servers/{sid}/bindings",
            json={"bindings": [
                {"workspace_id": ws_id, "enabled": True},
                {"workspace_id": ws_id_other, "enabled": True},
            ]},
        )

        # Other ws starts without cred
        status_resp = await ws_member_other_client.get(
            f"/api/v1/ws/{ws_id_other}/mcp/servers/{sid}/workspace-credential"
        )
        assert status_resp.json()["has_value"] is False

        # Other ws fills cred
        put_resp = await ws_member_other_client.put(
            f"/api/v1/ws/{ws_id_other}/mcp/servers/{sid}/workspace-credential",
            json={"plaintext": "tok-other-ws"},
        )
        assert put_resp.status_code == 204
        status_resp2 = await ws_member_other_client.get(
            f"/api/v1/ws/{ws_id_other}/mcp/servers/{sid}/workspace-credential"
        )
        assert status_resp2.json()["has_value"] is True


async def test_promote_already_org_wide_returns_409(
    admin_authed_client, reference_mcp_server, ws_id,
) -> None:
    with reference_mcp_server(auth_mode="none") as srv:
        create_resp = await admin_authed_client.post(
            "/api/v1/admin/mcp/servers",
            json={
                "name": "alreadyOrg", "server_url": f"{srv.base_url}/mcp",
                "transport": "streamable_http", "auth_method": "none",
                "credential_scope": "none",
            },
        )
        sid = create_resp.json()["id"]
    promote_resp = await admin_authed_client.post(
        f"/api/v1/ws/{ws_id}/mcp/servers/{sid}/promote-to-org",
        json={"share_credential": False},
    )
    # admin client may also be a ws member; promote endpoint is on ws path
    assert promote_resp.status_code in (403, 404, 409)  # not the original ws
```

- [ ] **Step 2: Run**

```bash
uv run pytest tests/e2e/test_mcp_promote.py -v
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_mcp_promote.py
git commit -m "test(mcp): promote α + β E2E"
```

---

### Task 35: User-scope multi-user E2E

**Files:**
- Create: `backend/tests/e2e/test_mcp_user_scope.py`

- [ ] **Step 1: Write**

```python
# backend/tests/e2e/test_mcp_user_scope.py
"""User-scope MCP server: per-user credentials. A fills, B doesn't — A sees tools, B doesn't."""

import pytest


async def test_user_scope_only_user_with_cred_sees_tools(
    admin_authed_client, ws_user_a_client, ws_user_b_client,
    reference_mcp_server, ws_id, user_a_id, user_b_id,
) -> None:
    """Admin creates user-scope server; bind ws; A fills cred, B doesn't.

    Then issue a chat message in each user's session and inspect tool registry
    via a backend-side helper (see conftest helper `tools_for_message`).
    """
    with reference_mcp_server(auth_mode="bearer-static", static_token="user-A-tok") as srv:
        create_resp = await admin_authed_client.post(
            "/api/v1/admin/mcp/servers",
            json={
                "name": "user-scope", "server_url": f"{srv.base_url}/mcp",
                "transport": "streamable_http", "auth_method": "static",
                "credential_scope": "user",
            },
        )
        sid = create_resp.json()["id"]
        await admin_authed_client.put(
            f"/api/v1/admin/mcp/servers/{sid}/bindings",
            json={"bindings": [{"workspace_id": ws_id, "enabled": True}]},
        )

        # User A fills their credential. Refresh-tools so authed=true.
        await ws_user_a_client.put(
            f"/api/v1/ws/{ws_id}/mcp/servers/{sid}/my-credential",
            json={"plaintext": "user-A-tok"},
        )
        await admin_authed_client.post(
            f"/api/v1/admin/mcp/servers/{sid}/refresh-tools"
        )
        # We can refresh as admin using A's cred—but that's by-design simplification:
        # admin's refresh-tools uses no token for user-scope (skips); we mark authed
        # via a separate path. For E2E, the assertion below is on the runtime tool
        # registry, which uses per-user creds at conversation start.

    # User A starts a conversation; assert tool list includes echo
    a_tools = await _conversation_tools_for(ws_user_a_client, ws_id)
    assert "echo" in a_tools

    # User B has no cred; tool not present
    b_tools = await _conversation_tools_for(ws_user_b_client, ws_id)
    assert "echo" not in b_tools


async def _conversation_tools_for(client, ws_id: str) -> set[str]:
    """Helper: open a conversation and inspect available tools.

    Implementation note: cubeplex does not expose a direct "tools list"
    endpoint per conversation. Use the SSE stream — first text_delta or
    a debug event reveals the tool set. If the codebase has an internal
    helper (e.g. /debug/tools), use that. Otherwise, send a message
    "list tools" and observe the agent's response.
    """
    # ... implementation depends on existing helpers; see tests/e2e/test_agents.py
    # for the SSE consumer pattern.
    raise NotImplementedError("adapt to existing test infrastructure")
```

> **Note:** the `_conversation_tools_for` helper requires inspecting the runtime tool registry. The simplest reliable path is to add a backend-only debug endpoint **just for tests** (gated by `if config.debug`). If that violates project conventions, use the SSE stream of an actual chat turn and assert tool_call events. Either approach is acceptable; the executing engineer picks based on existing patterns.

- [ ] **Step 2: Run**

```bash
uv run pytest tests/e2e/test_mcp_user_scope.py -v
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_mcp_user_scope.py
git commit -m "test(mcp): user-scope multi-user E2E"
```

---

### Task 36: Bindings E2E

**Files:**
- Create: `backend/tests/e2e/test_mcp_bindings.py`

- [ ] **Step 1: Write**

```python
# backend/tests/e2e/test_mcp_bindings.py
"""Workspace bindings: bulk PUT replaces; visibility per-ws."""

import pytest


async def test_bulk_put_replaces_bindings_set(
    admin_authed_client, reference_mcp_server, ws_id, ws_id_other,
) -> None:
    with reference_mcp_server(auth_mode="none") as srv:
        create_resp = await admin_authed_client.post(
            "/api/v1/admin/mcp/servers",
            json={
                "name": "B1", "server_url": f"{srv.base_url}/mcp",
                "transport": "streamable_http", "auth_method": "none",
                "credential_scope": "none",
            },
        )
        sid = create_resp.json()["id"]

    # Initial: bind both ws enabled
    r1 = await admin_authed_client.put(
        f"/api/v1/admin/mcp/servers/{sid}/bindings",
        json={"bindings": [
            {"workspace_id": ws_id, "enabled": True},
            {"workspace_id": ws_id_other, "enabled": True},
        ]},
    )
    assert r1.status_code == 200
    items = r1.json()
    assert {b["workspace_id"] for b in items} == {ws_id, ws_id_other}

    # Replace: only ws_id, disabled
    r2 = await admin_authed_client.put(
        f"/api/v1/admin/mcp/servers/{sid}/bindings",
        json={"bindings": [{"workspace_id": ws_id, "enabled": False}]},
    )
    assert r2.status_code == 200
    items = r2.json()
    assert len(items) == 1
    assert items[0]["workspace_id"] == ws_id and items[0]["enabled"] is False


async def test_workspace_owned_server_has_no_bindings_endpoint(
    admin_authed_client, ws_member_client, reference_mcp_server, ws_id,
) -> None:
    with reference_mcp_server(auth_mode="none") as srv:
        create_resp = await ws_member_client.post(
            f"/api/v1/ws/{ws_id}/mcp/servers",
            json={
                "name": "ws-owned", "server_url": f"{srv.base_url}/mcp",
                "transport": "streamable_http", "auth_method": "none",
                "credential_scope": "none",
            },
        )
        sid = create_resp.json()["id"]
    resp = await admin_authed_client.get(
        f"/api/v1/admin/mcp/servers/{sid}/bindings"
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/e2e/test_mcp_bindings.py -v
git add backend/tests/e2e/test_mcp_bindings.py
git commit -m "test(mcp): workspace bindings bulk PUT E2E"
```

---

### Task 37: Discovery failure E2E

**Files:**
- Create: `backend/tests/e2e/test_mcp_discovery_failure.py`

- [ ] **Step 1: Write**

```python
# backend/tests/e2e/test_mcp_discovery_failure.py
"""Discovery soft-fails: server saved with authed=false; refresh-tools recovers."""

import pytest


async def test_create_with_unreachable_url_returns_201_authed_false(
    admin_authed_client,
) -> None:
    resp = await admin_authed_client.post(
        "/api/v1/admin/mcp/servers",
        json={
            "name": "Unreachable",
            "server_url": "http://127.0.0.1:1/mcp",  # nothing listening
            "transport": "streamable_http",
            "auth_method": "none",
            "credential_scope": "none",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["authed"] is False
    assert body["last_error"] is not None
    assert body["tools_cache"] == []


async def test_refresh_tools_after_server_comes_up(
    admin_authed_client, reference_mcp_server,
) -> None:
    """Create with bad URL → fix URL via PATCH → refresh-tools → authed=true."""
    create_resp = await admin_authed_client.post(
        "/api/v1/admin/mcp/servers",
        json={
            "name": "FixMe", "server_url": "http://127.0.0.1:1/mcp",
            "transport": "streamable_http", "auth_method": "none",
            "credential_scope": "none",
        },
    )
    sid = create_resp.json()["id"]
    assert create_resp.json()["authed"] is False

    with reference_mcp_server(auth_mode="none") as srv:
        await admin_authed_client.patch(
            f"/api/v1/admin/mcp/servers/{sid}",
            json={"server_url": f"{srv.base_url}/mcp"},
        )
        refresh_resp = await admin_authed_client.post(
            f"/api/v1/admin/mcp/servers/{sid}/refresh-tools"
        )
    assert refresh_resp.status_code == 200
    assert refresh_resp.json()["authed"] is True
```

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/e2e/test_mcp_discovery_failure.py -v
git add backend/tests/e2e/test_mcp_discovery_failure.py
git commit -m "test(mcp): discovery soft-fail + refresh recovery E2E"
```

---

### Task 38: OAuth placeholder E2E

**Files:**
- Create: `backend/tests/e2e/test_mcp_oauth_placeholder.py`

- [ ] **Step 1: Write + run**

```python
# backend/tests/e2e/test_mcp_oauth_placeholder.py
"""auth_method=oauth is reserved enum but rejected at create."""

import pytest


async def test_oauth_create_returns_409(admin_authed_client) -> None:
    resp = await admin_authed_client.post(
        "/api/v1/admin/mcp/servers",
        json={
            "name": "OAuthAttempt", "server_url": "https://oauth.example.com/mcp",
            "transport": "streamable_http", "auth_method": "oauth",
            "credential_scope": "org", "credential_plaintext": "x",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "mcp_oauth_not_implemented"


async def test_oauth_test_connection_returns_409(admin_authed_client) -> None:
    resp = await admin_authed_client.post(
        "/api/v1/admin/mcp/test-connection",
        json={
            "server_url": "https://oauth.example.com/mcp",
            "transport": "streamable_http", "auth_method": "oauth",
            "credential_scope": "org", "credential_plaintext": "x",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "mcp_oauth_not_implemented"
```

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/e2e/test_mcp_oauth_placeholder.py -v
git add backend/tests/e2e/test_mcp_oauth_placeholder.py
git commit -m "test(mcp): oauth auth_method rejected with 409"
```

---

### Task 39: Passthrough JWT E2E

**Files:**
- Create: `backend/tests/e2e/test_mcp_passthrough.py`

- [ ] **Step 1: Write**

```python
# backend/tests/e2e/test_mcp_passthrough.py
"""credential_scope=none: runtime signs JWT with cubeplex identity; reference server verifies claims."""

import os

import jwt
import pytest


async def test_passthrough_jwt_claims_received_by_server(
    admin_authed_client, ws_user_a_client, reference_mcp_server,
    ws_id, user_a_id,
) -> None:
    """Use bearer-jwt-verify mode; assert reference server received valid JWT
    with sub=user_a_id and iss=cubeplex.

    Verification path: invoke the `ping` tool via a chat turn; the reference
    server's response embeds the decoded claims, which the agent surfaces
    in tool_result.
    """
    secret = os.environ["CUBEPLEX_AUTH__JWT_SECRET"]
    with reference_mcp_server(
        auth_mode="bearer-jwt-verify", jwt_secret=secret,
    ) as srv:
        create_resp = await admin_authed_client.post(
            "/api/v1/admin/mcp/servers",
            json={
                "name": "Passthrough", "server_url": f"{srv.base_url}/mcp",
                "transport": "streamable_http", "auth_method": "none",
                "credential_scope": "none",
            },
        )
        sid = create_resp.json()["id"]
        await admin_authed_client.put(
            f"/api/v1/admin/mcp/servers/{sid}/bindings",
            json={"bindings": [{"workspace_id": ws_id, "enabled": True}]},
        )

        # Issue a chat message that triggers `ping` tool
        # (helper function: see conftest._send_chat_message_and_get_tool_results)
        results = await _send_message_capture_tool_results(
            ws_user_a_client, ws_id, message="please call ping",
        )
        assert any("pong" in r for r in results)
        # Reference server's pong response embeds decoded claims as JSON string:
        claims_json = next(r for r in results if "pong" in r)
        assert f'"sub": "{user_a_id}"' in claims_json
        assert '"iss": "cubeplex"' in claims_json


async def _send_message_capture_tool_results(client, ws_id, message: str) -> list[str]:
    """Send a chat message and collect tool_result event payloads. Implementation
    follows the SSE consumer pattern in tests/e2e/test_agents.py."""
    raise NotImplementedError("adapt to existing SSE consumer helper")
```

> **Note:** if the agent doesn't reliably call `ping` from the prompt alone, use a bypass: the helper opens the conversation, then the test makes a direct backend invocation of the BaseTool via an internal helper (e.g. `MCPManager.call_tool_for(server_id, tool_name)` if exposed; otherwise add a debug endpoint `POST /api/v1/debug/mcp/{sid}/invoke` gated by debug mode). Either route is acceptable.

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/e2e/test_mcp_passthrough.py -v
git add backend/tests/e2e/test_mcp_passthrough.py
git commit -m "test(mcp): passthrough JWT signed claims verified by reference server"
```

---

### Task 40: Legacy + DB coexistence E2E

**Files:**
- Create: `backend/tests/e2e/test_legacy_mcp_coexists.py`

- [ ] **Step 1: Write**

```python
# backend/tests/e2e/test_legacy_mcp_coexists.py
"""When config.yaml has mcp.servers AND DB has servers, both contribute to ToolRegistry."""

import pytest


async def test_both_sources_load(
    admin_authed_client, ws_user_a_client, reference_mcp_server,
    ws_id, monkeypatch,
) -> None:
    """Set up a legacy server via config + DB server via API; observe both tools."""
    # Legacy: set CUBEPLEX_MCP__SERVERS via env override or test config
    # (adapt to project's test config layering — see config.test.yaml)
    legacy_tool_name = "legacy_echo"  # imagine config-loaded server has this tool
    db_tool_name = "echo"

    with reference_mcp_server(auth_mode="none") as srv:
        await admin_authed_client.post(
            "/api/v1/admin/mcp/servers",
            json={
                "name": "DB-side", "server_url": f"{srv.base_url}/mcp",
                "transport": "streamable_http", "auth_method": "none",
                "credential_scope": "none",
            },
        )
        # ... bind to ws_id ...

        # Capture conversation tools (helper as in Task 35)
        tools = await _capture_runtime_tools(ws_user_a_client, ws_id)
    assert db_tool_name in tools
    # legacy_tool_name presence depends on test config's mcp.servers setup
    # If legacy is configured in tests/conftest.py, assert it's present too:
    if _legacy_configured():
        assert legacy_tool_name in tools


async def _capture_runtime_tools(client, ws_id):
    raise NotImplementedError("adapt to existing helper")


def _legacy_configured() -> bool:
    from cubeplex.config import config
    return bool(config.get("mcp.servers"))
```

> **Note:** legacy config setup for tests is environment-dependent. If existing tests already have a legacy server configured (for the `webtools` example), assert presence; else skip the legacy half cleanly.

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/e2e/test_legacy_mcp_coexists.py -v
git add backend/tests/e2e/test_legacy_mcp_coexists.py
git commit -m "test(mcp): legacy config + DB MCP coexistence E2E"
```

---

## Phase F · Frontend (Stage 6)

### Task 41: shadcn install + types

**Files:**
- Create: `frontend/packages/core/src/types/mcp.ts`
- Modify: `frontend/packages/core/src/index.ts` (export new types)

- [ ] **Step 1: Install shadcn primitives (only those missing)**

```bash
cd frontend/packages/web
npx shadcn-ui@latest add radio-group switch accordion alert
```

(If a component is already installed, the CLI is a no-op.)

- [ ] **Step 2: Define types**

```ts
// frontend/packages/core/src/types/mcp.ts
export type MCPTransport = "streamable_http" | "sse" | "stdio";
export type MCPAuthMethod = "static" | "oauth" | "none";
export type MCPCredentialScope = "org" | "workspace" | "user" | "none";

export interface MCPCredentialRef {
  id: string;
  name: string;
  has_value: boolean;
}

export interface MCPToolEntry {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export interface MCPServer {
  id: string;
  name: string;
  server_url: string;
  transport: MCPTransport;
  auth_method: MCPAuthMethod;
  credential_scope: MCPCredentialScope;
  credential: MCPCredentialRef | null;
  owner_workspace_id: string | null;
  headers: Record<string, string>;
  tools_cache: MCPToolEntry[] | null;
  authed: boolean;
  last_error: string | null;
  last_discovered_at: string | null;
  timeout: number;
  sse_read_timeout: number;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
}

export interface MCPServerCreateAdminBody {
  name: string;
  server_url: string;
  transport: MCPTransport;
  auth_method: MCPAuthMethod;
  credential_scope: "org" | "user" | "none";
  credential_plaintext?: string;
  credential_name?: string;
  headers?: Record<string, string>;
  timeout?: number;
  sse_read_timeout?: number;
}

export interface MCPServerCreateWSBody {
  name: string;
  server_url: string;
  transport: MCPTransport;
  auth_method: MCPAuthMethod;
  credential_scope: "workspace" | "user" | "none";
  credential_plaintext?: string;
  credential_name?: string;
  headers?: Record<string, string>;
  timeout?: number;
  sse_read_timeout?: number;
}

export interface MCPServerPatchBody {
  name?: string;
  server_url?: string;
  transport?: MCPTransport;
  credential_plaintext?: string;
  headers?: Record<string, string>;
  timeout?: number;
  sse_read_timeout?: number;
}

export interface MCPTestConnectionBody {
  server_url: string;
  transport: MCPTransport;
  auth_method: MCPAuthMethod;
  credential_scope: MCPCredentialScope;
  credential_plaintext?: string;
  headers?: Record<string, string>;
  timeout?: number;
  sse_read_timeout?: number;
}

export interface MCPTestConnectionResult {
  success: boolean;
  tools: MCPToolEntry[] | null;
  error: string | null;
}

export interface WorkspaceBinding {
  workspace_id: string;
  enabled: boolean;
}

export interface MCPServerListWS {
  owned: MCPServer[];
  via_binding: MCPServer[];
}

export interface PromoteBody {
  share_credential: boolean;
}

export interface CredentialUpsertBody {
  plaintext: string;
  name?: string;
}

export interface CredentialStatus {
  has_value: boolean;
}
```

- [ ] **Step 3: Re-export**

```ts
// frontend/packages/core/src/index.ts — append
export * from "./types/mcp";
```

- [ ] **Step 4: Build core**

```bash
cd frontend
pnpm --filter @cubeplex/core build
```

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/core/src/types/mcp.ts \
        frontend/packages/core/src/index.ts \
        frontend/packages/web/components/ui/radio-group.tsx \
        frontend/packages/web/components/ui/switch.tsx \
        frontend/packages/web/components/ui/accordion.tsx \
        frontend/packages/web/components/ui/alert.tsx
git commit -m "feat(mcp/web): add shadcn primitives + MCP TypeScript types"
```

---

### Task 42: API client functions

**Files:**
- Modify: `frontend/packages/core/src/api/client.ts`
- Create: `frontend/packages/core/src/api/mcp.ts`
- Modify: `frontend/packages/core/src/api/index.ts`
- Modify: `frontend/packages/core/src/index.ts`

- [ ] **Step 1: Add PUT support to ApiClient**

```ts
// frontend/packages/core/src/api/client.ts
export interface ApiClient {
  // existing fields...
  put(path: string, body: unknown): Promise<Response>
}
```

Add the method next to `patch`:

```ts
put(path, body) {
  return doFetch(path, {
    method: 'PUT',
    headers: buildHeaders('PUT', { 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  })
},
```

- [ ] **Step 2: Implement**

Every helper must follow existing core package style:

```ts
const res = await client.get('/api/v1/...')
if (!res.ok) throw await toApiError(res)
return (await res.json()) as SomeType
```

Do not return `client.get(...)`, `client.post(...)`, `client.put(...)`, or `client.patch(...)`
directly; those return `Response`, not parsed JSON.

```ts
// frontend/packages/core/src/api/mcp.ts
import { toApiError, type ApiClient } from './client'
import type {
  CredentialStatus,
  CredentialUpsertBody,
  MCPServer,
  MCPServerCreateAdminBody,
  MCPServerCreateWSBody,
  MCPServerListWS,
  MCPServerPatchBody,
  MCPTestConnectionBody,
  MCPTestConnectionResult,
  PromoteBody,
  WorkspaceBinding,
} from '../types/mcp'

// --- Admin ---
export async function adminListServers(
  client: ApiClient,
  filters?: { scope?: string; owner_workspace_id?: string; has_error?: boolean },
): Promise<MCPServer[]> {
  const qs = new URLSearchParams();
  if (filters?.scope) qs.set("scope", filters.scope);
  if (filters?.owner_workspace_id)
    qs.set("owner_workspace_id", filters.owner_workspace_id);
  if (filters?.has_error !== undefined)
    qs.set("has_error", String(filters.has_error));
  const q = qs.toString();
  return client.get(`/api/v1/admin/mcp/servers${q ? "?" + q : ""}`);
}

export async function adminCreateServer(
  client: ApiClient, body: MCPServerCreateAdminBody,
): Promise<MCPServer> {
  return client.post("/api/v1/admin/mcp/servers", body);
}

export async function adminGetServer(
  client: ApiClient, id: string,
): Promise<MCPServer> {
  return client.get(`/api/v1/admin/mcp/servers/${id}`);
}

export async function adminPatchServer(
  client: ApiClient, id: string, body: MCPServerPatchBody,
): Promise<MCPServer> {
  return client.patch(`/api/v1/admin/mcp/servers/${id}`, body);
}

export async function adminDeleteServer(
  client: ApiClient, id: string,
): Promise<void> {
  await client.delete(`/api/v1/admin/mcp/servers/${id}`);
}

export async function adminRefreshTools(
  client: ApiClient, id: string,
): Promise<MCPServer> {
  return client.post(`/api/v1/admin/mcp/servers/${id}/refresh-tools`, {});
}

export async function adminTestConnection(
  client: ApiClient, body: MCPTestConnectionBody,
): Promise<MCPTestConnectionResult> {
  return client.post("/api/v1/admin/mcp/test-connection", body);
}

export async function adminGetBindings(
  client: ApiClient, id: string,
): Promise<WorkspaceBinding[]> {
  return client.get(`/api/v1/admin/mcp/servers/${id}/bindings`);
}

export async function adminPutBindings(
  client: ApiClient, id: string, bindings: WorkspaceBinding[],
): Promise<WorkspaceBinding[]> {
  return client.put(`/api/v1/admin/mcp/servers/${id}/bindings`, { bindings });
}

// --- WS member ---
export async function wsListServers(
  client: ApiClient, wsId: string,
): Promise<MCPServerListWS> {
  return client.get(`/api/v1/ws/${wsId}/mcp/servers`);
}

export async function wsCreateServer(
  client: ApiClient, wsId: string, body: MCPServerCreateWSBody,
): Promise<MCPServer> {
  return client.post(`/api/v1/ws/${wsId}/mcp/servers`, body);
}

export async function wsGetServer(
  client: ApiClient, wsId: string, id: string,
): Promise<MCPServer> {
  return client.get(`/api/v1/ws/${wsId}/mcp/servers/${id}`);
}

export async function wsPatchServer(
  client: ApiClient, wsId: string, id: string, body: MCPServerPatchBody,
): Promise<MCPServer> {
  return client.patch(`/api/v1/ws/${wsId}/mcp/servers/${id}`, body);
}

export async function wsDeleteServer(
  client: ApiClient, wsId: string, id: string,
): Promise<void> {
  await client.delete(`/api/v1/ws/${wsId}/mcp/servers/${id}`);
}

export async function wsRefreshTools(
  client: ApiClient, wsId: string, id: string,
): Promise<MCPServer> {
  return client.post(`/api/v1/ws/${wsId}/mcp/servers/${id}/refresh-tools`, {});
}

export async function wsTestConnection(
  client: ApiClient, wsId: string, body: MCPTestConnectionBody,
): Promise<MCPTestConnectionResult> {
  return client.post(`/api/v1/ws/${wsId}/mcp/test-connection`, body);
}

export async function wsPromote(
  client: ApiClient, wsId: string, id: string, body: PromoteBody,
): Promise<MCPServer> {
  return client.post(
    `/api/v1/ws/${wsId}/mcp/servers/${id}/promote-to-org`, body,
  );
}

export async function wsGetMyCredential(
  client: ApiClient, wsId: string, id: string,
): Promise<CredentialStatus> {
  return client.get(
    `/api/v1/ws/${wsId}/mcp/servers/${id}/my-credential`,
  );
}

export async function wsPutMyCredential(
  client: ApiClient, wsId: string, id: string, body: CredentialUpsertBody,
): Promise<void> {
  await client.put(
    `/api/v1/ws/${wsId}/mcp/servers/${id}/my-credential`, body,
  );
}

export async function wsDeleteMyCredential(
  client: ApiClient, wsId: string, id: string,
): Promise<void> {
  await client.delete(
    `/api/v1/ws/${wsId}/mcp/servers/${id}/my-credential`,
  );
}

export async function wsGetWorkspaceCredential(
  client: ApiClient, wsId: string, id: string,
): Promise<CredentialStatus> {
  return client.get(
    `/api/v1/ws/${wsId}/mcp/servers/${id}/workspace-credential`,
  );
}

export async function wsPutWorkspaceCredential(
  client: ApiClient, wsId: string, id: string, body: CredentialUpsertBody,
): Promise<void> {
  await client.put(
    `/api/v1/ws/${wsId}/mcp/servers/${id}/workspace-credential`, body,
  );
}

export async function wsDeleteWorkspaceCredential(
  client: ApiClient, wsId: string, id: string,
): Promise<void> {
  await client.delete(
    `/api/v1/ws/${wsId}/mcp/servers/${id}/workspace-credential`,
  );
}
```

- [ ] **Step 2: Re-export + build**

```ts
// frontend/packages/core/src/index.ts — append
export * from "./api/mcp";
```

```bash
cd frontend && pnpm --filter @cubeplex/core build
```

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/core/src/api/client.ts \
        frontend/packages/core/src/api/mcp.ts \
        frontend/packages/core/src/api/index.ts \
        frontend/packages/core/src/index.ts
git commit -m "feat(mcp/web): API client functions for admin + ws routes"
```

---

### Task 43: Zustand stores

**Files:**
- Create: `frontend/packages/core/src/stores/mcpStore.ts`
- Create: `frontend/packages/core/src/stores/workspaceMcpStore.ts`
- Modify: `frontend/packages/core/src/index.ts`

- [ ] **Step 1: Implement admin store**

```ts
// frontend/packages/core/src/stores/mcpStore.ts
import { create } from "zustand";

import * as api from "../api/mcp";
import type { ApiClient } from "../api/client";
import type {
  MCPServer,
  MCPServerCreateAdminBody,
  MCPServerPatchBody,
  MCPTestConnectionBody,
  MCPTestConnectionResult,
  WorkspaceBinding,
} from "../types/mcp";

interface State {
  servers: MCPServer[];
  loading: boolean;
  error: string | null;
}

interface Actions {
  fetchAll: (client: ApiClient) => Promise<void>;
  create: (client: ApiClient, body: MCPServerCreateAdminBody) => Promise<MCPServer>;
  update: (client: ApiClient, id: string, body: MCPServerPatchBody) => Promise<MCPServer>;
  remove: (client: ApiClient, id: string) => Promise<void>;
  refreshTools: (client: ApiClient, id: string) => Promise<MCPServer>;
  testConnection: (
    client: ApiClient, body: MCPTestConnectionBody,
  ) => Promise<MCPTestConnectionResult>;
  fetchBindings: (client: ApiClient, id: string) => Promise<WorkspaceBinding[]>;
  saveBindings: (
    client: ApiClient, id: string, bindings: WorkspaceBinding[],
  ) => Promise<WorkspaceBinding[]>;
}

export const useMcpStore = create<State & Actions>()((set, get) => ({
  servers: [],
  loading: false,
  error: null,

  fetchAll: async (client) => {
    set({ loading: true, error: null });
    try {
      const servers = await api.adminListServers(client);
      set({ servers, loading: false });
    } catch (e) {
      set({ error: String(e), loading: false });
    }
  },

  create: async (client, body) => {
    const created = await api.adminCreateServer(client, body);
    set({ servers: [...get().servers, created] });
    return created;
  },

  update: async (client, id, body) => {
    const updated = await api.adminPatchServer(client, id, body);
    set({ servers: get().servers.map((s) => (s.id === id ? updated : s)) });
    return updated;
  },

  remove: async (client, id) => {
    await api.adminDeleteServer(client, id);
    set({ servers: get().servers.filter((s) => s.id !== id) });
  },

  refreshTools: async (client, id) => {
    const refreshed = await api.adminRefreshTools(client, id);
    set({ servers: get().servers.map((s) => (s.id === id ? refreshed : s)) });
    return refreshed;
  },

  testConnection: async (client, body) => api.adminTestConnection(client, body),

  fetchBindings: async (client, id) => api.adminGetBindings(client, id),

  saveBindings: async (client, id, bindings) =>
    api.adminPutBindings(client, id, bindings),
}));
```

- [ ] **Step 2: Implement workspace store**

```ts
// frontend/packages/core/src/stores/workspaceMcpStore.ts
import { create } from "zustand";

import * as api from "../api/mcp";
import type { ApiClient } from "../api/client";
import type {
  CredentialStatus,
  CredentialUpsertBody,
  MCPServer,
  MCPServerCreateWSBody,
  MCPServerListWS,
  MCPServerPatchBody,
  PromoteBody,
} from "../types/mcp";

interface State {
  owned: MCPServer[];
  viaBinding: MCPServer[];
  loading: boolean;
  error: string | null;
}

interface Actions {
  fetchAll: (client: ApiClient, wsId: string) => Promise<void>;
  create: (
    client: ApiClient, wsId: string, body: MCPServerCreateWSBody,
  ) => Promise<MCPServer>;
  update: (
    client: ApiClient, wsId: string, id: string, body: MCPServerPatchBody,
  ) => Promise<MCPServer>;
  remove: (client: ApiClient, wsId: string, id: string) => Promise<void>;
  refreshTools: (client: ApiClient, wsId: string, id: string) => Promise<MCPServer>;
  promote: (
    client: ApiClient, wsId: string, id: string, body: PromoteBody,
  ) => Promise<MCPServer>;
  getMyCredentialStatus: (
    client: ApiClient, wsId: string, id: string,
  ) => Promise<CredentialStatus>;
  setMyCredential: (
    client: ApiClient, wsId: string, id: string, body: CredentialUpsertBody,
  ) => Promise<void>;
  clearMyCredential: (client: ApiClient, wsId: string, id: string) => Promise<void>;
  getWorkspaceCredentialStatus: (
    client: ApiClient, wsId: string, id: string,
  ) => Promise<CredentialStatus>;
  setWorkspaceCredential: (
    client: ApiClient, wsId: string, id: string, body: CredentialUpsertBody,
  ) => Promise<void>;
  clearWorkspaceCredential: (
    client: ApiClient, wsId: string, id: string,
  ) => Promise<void>;
}

export const useWorkspaceMcpStore = create<State & Actions>()((set, get) => ({
  owned: [],
  viaBinding: [],
  loading: false,
  error: null,

  fetchAll: async (client, wsId) => {
    set({ loading: true, error: null });
    try {
      const list: MCPServerListWS = await api.wsListServers(client, wsId);
      set({ owned: list.owned, viaBinding: list.via_binding, loading: false });
    } catch (e) {
      set({ error: String(e), loading: false });
    }
  },

  create: async (client, wsId, body) => {
    const created = await api.wsCreateServer(client, wsId, body);
    set({ owned: [...get().owned, created] });
    return created;
  },

  update: async (client, wsId, id, body) => {
    const updated = await api.wsPatchServer(client, wsId, id, body);
    set({ owned: get().owned.map((s) => (s.id === id ? updated : s)) });
    return updated;
  },

  remove: async (client, wsId, id) => {
    await api.wsDeleteServer(client, wsId, id);
    set({ owned: get().owned.filter((s) => s.id !== id) });
  },

  refreshTools: async (client, wsId, id) => {
    const refreshed = await api.wsRefreshTools(client, wsId, id);
    set({ owned: get().owned.map((s) => (s.id === id ? refreshed : s)) });
    return refreshed;
  },

  promote: async (client, wsId, id, body) => {
    const promoted = await api.wsPromote(client, wsId, id, body);
    // After promote, server is org-wide → moves out of `owned` into `via_binding`
    set({
      owned: get().owned.filter((s) => s.id !== id),
      viaBinding: [...get().viaBinding, promoted],
    });
    return promoted;
  },

  getMyCredentialStatus: api.wsGetMyCredential,
  setMyCredential: api.wsPutMyCredential,
  clearMyCredential: api.wsDeleteMyCredential,
  getWorkspaceCredentialStatus: api.wsGetWorkspaceCredential,
  setWorkspaceCredential: api.wsPutWorkspaceCredential,
  clearWorkspaceCredential: api.wsDeleteWorkspaceCredential,
}));
```

- [ ] **Step 3: Re-export + build**

```ts
// frontend/packages/core/src/index.ts — append
export * from "./stores/mcpStore";
export * from "./stores/workspaceMcpStore";
```

```bash
pnpm --filter @cubeplex/core build
```

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/core/src/stores/mcpStore.ts \
        frontend/packages/core/src/stores/workspaceMcpStore.ts \
        frontend/packages/core/src/index.ts
git commit -m "feat(mcp/web): zustand stores for admin + ws"
```

---

### Task 44: Atom components

**Files:**
- Create: `frontend/packages/web/components/mcp/MCPSecretInput.tsx`
- Create: `frontend/packages/web/components/mcp/MCPScopeBadge.tsx`

- [ ] **Step 1: MCPSecretInput**

```tsx
// frontend/packages/web/components/mcp/MCPSecretInput.tsx
"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export interface MCPSecretInputProps {
  label: string;
  hasValue: boolean;          // server says "credential is set"
  onChange: (plaintext: string) => void;  // emits new plaintext to parent
  required?: boolean;
}

export function MCPSecretInput({
  label, hasValue, onChange, required,
}: MCPSecretInputProps) {
  const [editing, setEditing] = useState(!hasValue);
  const [value, setValue] = useState("");

  if (!editing) {
    return (
      <div className="flex items-center gap-3">
        <Label className="font-medium">{label}</Label>
        <span className="text-muted-foreground font-mono text-sm">
          •••• (已设置)
        </span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setEditing(true)}
        >
          替换
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      <Label htmlFor="secret-input">
        {label}{required && <span className="text-destructive ml-0.5">*</span>}
      </Label>
      <div className="flex items-center gap-2">
        <Input
          id="secret-input"
          type="password"
          autoComplete="new-password"
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            onChange(e.target.value);
          }}
          placeholder="API key / token"
          required={required && !hasValue}
        />
        {hasValue && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              setValue("");
              setEditing(false);
            }}
          >
            取消
          </Button>
        )}
      </div>
      <p className="text-muted-foreground text-xs">
        保存后不会再次显示。
      </p>
    </div>
  );
}
```

- [ ] **Step 2: MCPScopeBadge**

```tsx
// frontend/packages/web/components/mcp/MCPScopeBadge.tsx
"use client";

import type { MCPCredentialScope } from "@cubeplex/core";

import { Badge } from "@/components/ui/badge";

export function MCPScopeBadge({ scope }: { scope: MCPCredentialScope }) {
  const styles: Record<MCPCredentialScope, string> = {
    org: "bg-blue-100 text-blue-900 hover:bg-blue-100",
    workspace: "bg-violet-100 text-violet-900 hover:bg-violet-100",
    user: "bg-amber-100 text-amber-900 hover:bg-amber-100",
    none: "bg-zinc-100 text-zinc-900 hover:bg-zinc-100",
  };
  const labels: Record<MCPCredentialScope, string> = {
    org: "Org shared",
    workspace: "Workspace shared",
    user: "Per user",
    none: "Identity passthrough",
  };
  return <Badge className={styles[scope]}>{labels[scope]}</Badge>;
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/mcp/MCPSecretInput.tsx \
        frontend/packages/web/components/mcp/MCPScopeBadge.tsx
git commit -m "feat(mcp/web): MCPSecretInput + MCPScopeBadge atoms"
```

---

### Task 45: MCPServerForm component

**Files:**
- Create: `frontend/packages/web/components/mcp/MCPServerForm.tsx`

- [ ] **Step 1: Implement (admin variant; ws variant via prop)**

```tsx
// frontend/packages/web/components/mcp/MCPServerForm.tsx
"use client";

import { useState } from "react";
import { Plug, Loader2 } from "lucide-react";

import type {
  MCPAuthMethod,
  MCPCredentialScope,
  MCPTestConnectionResult,
  MCPTransport,
} from "@cubeplex/core";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

import { MCPSecretInput } from "./MCPSecretInput";

export type FormMode = "admin" | "ws-member";

export interface MCPServerFormValues {
  name: string;
  server_url: string;
  transport: MCPTransport;
  auth_method: MCPAuthMethod;
  credential_scope: MCPCredentialScope;
  credential_plaintext: string;
  credential_name: string;
  headers: Record<string, string>;
  timeout: number;
  sse_read_timeout: number;
}

const DEFAULT_VALUES: MCPServerFormValues = {
  name: "",
  server_url: "",
  transport: "streamable_http",
  auth_method: "static",
  credential_scope: "workspace",
  credential_plaintext: "",
  credential_name: "",
  headers: {},
  timeout: 30,
  sse_read_timeout: 300,
};

export interface MCPServerFormProps {
  mode: FormMode;
  initial?: Partial<MCPServerFormValues>;
  onSubmit: (values: MCPServerFormValues) => Promise<void>;
  onTestConnection: (
    values: MCPServerFormValues,
  ) => Promise<MCPTestConnectionResult>;
  onCancel: () => void;
}

export function MCPServerForm({
  mode, initial, onSubmit, onTestConnection, onCancel,
}: MCPServerFormProps) {
  const [values, setValues] = useState<MCPServerFormValues>({
    ...DEFAULT_VALUES,
    credential_scope: mode === "admin" ? "org" : "workspace",
    ...initial,
  });
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<MCPTestConnectionResult | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const set = <K extends keyof MCPServerFormValues>(
    key: K, val: MCPServerFormValues[K],
  ) => setValues((v) => ({ ...v, [key]: val }));

  // Lock auth_method=none ⇔ credential_scope=none
  const onScopeChange = (scope: MCPCredentialScope) => {
    setValues((v) => ({
      ...v,
      credential_scope: scope,
      auth_method: scope === "none" ? "none" : "static",
      credential_plaintext: scope === "user" || scope === "none" ? "" : v.credential_plaintext,
    }));
  };

  const adminScopes: MCPCredentialScope[] = ["org", "user", "none"];
  const wsScopes: MCPCredentialScope[] = ["workspace", "user", "none"];
  const scopes = mode === "admin" ? adminScopes : wsScopes;

  const scopeCopy: Record<MCPCredentialScope, { title: string; help: string }> = {
    org: {
      title: "Organization shared",
      help: "一份 key 整个 org 共用",
    },
    workspace: {
      title: "Workspace shared",
      help: "本 workspace 一份 key，本 ws 内所有人共用",
    },
    user: {
      title: "Per user",
      help: "每个用户填自己的 key",
    },
    none: {
      title: "Cubeplex identity passthrough",
      help: "不存 key — 由 MCP server 凭你的 cubeplex 身份自鉴权",
    },
  };

  return (
    <form
      className="space-y-6"
      onSubmit={async (e) => {
        e.preventDefault();
        setSubmitting(true);
        try {
          await onSubmit(values);
        } finally {
          setSubmitting(false);
        }
      }}
    >
      <Card>
        <CardHeader>
          <CardTitle className="text-base">基本信息</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="name">Name *</Label>
            <Input
              id="name" required value={values.name}
              onChange={(e) => set("name", e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="url">Server URL *</Label>
            <Input
              id="url" required value={values.server_url}
              placeholder="https://… or stdio command"
              onChange={(e) => set("server_url", e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label>Transport</Label>
            <Select
              value={values.transport}
              onValueChange={(v) => set("transport", v as MCPTransport)}
            >
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="streamable_http">streamable_http</SelectItem>
                <SelectItem value="sse">sse</SelectItem>
                <SelectItem value="stdio">stdio</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">凭证模式 *</CardTitle>
        </CardHeader>
        <CardContent>
          <RadioGroup
            value={values.credential_scope}
            onValueChange={(v) => onScopeChange(v as MCPCredentialScope)}
            className="space-y-3"
          >
            {scopes.map((s) => (
              <label
                key={s}
                className="flex cursor-pointer items-start gap-3 rounded-lg border p-4 hover:bg-accent"
              >
                <RadioGroupItem value={s} id={`scope-${s}`} />
                <div>
                  <div className="font-medium">{scopeCopy[s].title}</div>
                  <div className="text-muted-foreground text-sm">
                    {scopeCopy[s].help}
                  </div>
                </div>
              </label>
            ))}
            <label
              className="flex cursor-not-allowed items-start gap-3 rounded-lg border p-4 opacity-60"
              title="Coming soon"
            >
              <RadioGroupItem value="oauth-disabled" disabled id="scope-oauth" />
              <div>
                <div className="font-medium">OAuth</div>
                <div className="text-muted-foreground text-sm">
                  Coming soon (v1 不可用)
                </div>
              </div>
            </label>
          </RadioGroup>

          {(values.credential_scope === "org" ||
            values.credential_scope === "workspace") && (
            <div className="mt-4 space-y-3">
              <MCPSecretInput
                label="API key / token"
                hasValue={false}
                required
                onChange={(t) => set("credential_plaintext", t)}
              />
              <div className="space-y-1.5">
                <Label htmlFor="cred-name">凭证显示名（可选）</Label>
                <Input
                  id="cred-name"
                  value={values.credential_name}
                  placeholder={`mcp:${values.name || "server"}:${values.credential_scope}`}
                  onChange={(e) => set("credential_name", e.target.value)}
                />
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {testResult && (
        <Alert variant={testResult.success ? "default" : "destructive"}>
          <AlertTitle>
            {testResult.success ? "连接成功" : "连接失败"}
          </AlertTitle>
          <AlertDescription>
            {testResult.success
              ? `发现 ${testResult.tools?.length ?? 0} 个 tool: ${testResult.tools?.map((t) => t.name).join(", ")}`
              : testResult.error}
          </AlertDescription>
        </Alert>
      )}

      <div className="flex items-center justify-between">
        <Button
          type="button" variant="outline"
          disabled={testing || !values.server_url}
          onClick={async () => {
            setTesting(true);
            try {
              const r = await onTestConnection(values);
              setTestResult(r);
            } finally {
              setTesting(false);
            }
          }}
        >
          {testing && <Loader2 className="mr-1.5 size-4 animate-spin" />}
          测试连接
        </Button>
        <div className="space-x-2">
          <Button type="button" variant="ghost" onClick={onCancel}>
            取消
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting && <Loader2 className="mr-1.5 size-4 animate-spin" />}
            保存
          </Button>
        </div>
      </div>
    </form>
  );
}
```

- [ ] **Step 2: Type-check + commit**

```bash
cd frontend && pnpm type-check
git add frontend/packages/web/components/mcp/MCPServerForm.tsx
git commit -m "feat(mcp/web): MCPServerForm with scope dispatch + test-connection inline"
```

---

### Task 46: List + tools table + scope panels

**Files:**
- Create: `frontend/packages/web/components/mcp/MCPServerList.tsx`
- Create: `frontend/packages/web/components/mcp/MCPToolsTable.tsx`
- Create: `frontend/packages/web/components/mcp/MCPCredentialPanel.tsx`

- [ ] **Step 1: MCPServerList**

```tsx
// frontend/packages/web/components/mcp/MCPServerList.tsx
"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { CircleDot, Plug } from "lucide-react";

import type { MCPServer } from "@cubeplex/core";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

import { MCPScopeBadge } from "./MCPScopeBadge";

export interface MCPServerListProps {
  servers: MCPServer[];
  loading: boolean;
  detailHrefBase: string;          // e.g. "/admin/mcp" or "/w/{wsId}/integrations/mcp"
  emptyTitle: string;
  emptyDescription: string;
}

export function MCPServerList({
  servers, loading, detailHrefBase, emptyTitle, emptyDescription,
}: MCPServerListProps) {
  if (loading) return <Card><CardContent className="p-6">Loading…</CardContent></Card>;
  if (servers.length === 0) {
    return (
      <Card>
        <CardContent className="flex flex-col items-center justify-center gap-3 py-16 text-center">
          <Plug className="text-muted-foreground size-10" />
          <h3 className="font-semibold">{emptyTitle}</h3>
          <p className="text-muted-foreground max-w-md text-sm">
            {emptyDescription}
          </p>
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Scope</TableHead>
            <TableHead>Transport</TableHead>
            <TableHead>Tools</TableHead>
            <TableHead></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {servers.map((s) => (
            <TableRow key={s.id}>
              <TableCell className="flex items-center gap-2 font-medium">
                <CircleDot
                  className={`size-3 ${s.authed ? "text-green-600" : "text-red-600"}`}
                  aria-label={s.authed ? "ok" : "error"}
                  title={s.last_error ?? undefined}
                />
                {s.name}
              </TableCell>
              <TableCell><MCPScopeBadge scope={s.credential_scope} /></TableCell>
              <TableCell className="text-muted-foreground text-sm">{s.transport}</TableCell>
              <TableCell className="text-muted-foreground text-sm">
                {s.tools_cache?.length ?? 0}
              </TableCell>
              <TableCell className="text-right">
                <Button asChild variant="ghost" size="sm">
                  <Link href={`${detailHrefBase}/${s.id}`}>详情</Link>
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}
```

- [ ] **Step 2: MCPToolsTable**

```tsx
// frontend/packages/web/components/mcp/MCPToolsTable.tsx
"use client";

import type { MCPToolEntry } from "@cubeplex/core";

import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";

export function MCPToolsTable({ tools }: { tools: MCPToolEntry[] }) {
  if (tools.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">尚未发现 tools，点击 Refresh tools 重试。</p>
    );
  }
  return (
    <Accordion type="multiple" className="w-full">
      {tools.map((t) => (
        <AccordionItem key={t.name} value={t.name}>
          <AccordionTrigger>
            <span className="flex items-center gap-3">
              <span className="font-mono">{t.name}</span>
              <span className="text-muted-foreground text-sm">{t.description}</span>
            </span>
          </AccordionTrigger>
          <AccordionContent>
            <pre className="bg-muted/50 overflow-x-auto rounded-md p-3 font-mono text-xs">
              {JSON.stringify(t.input_schema, null, 2)}
            </pre>
          </AccordionContent>
        </AccordionItem>
      ))}
    </Accordion>
  );
}
```

- [ ] **Step 3: MCPCredentialPanel**

```tsx
// frontend/packages/web/components/mcp/MCPCredentialPanel.tsx
"use client";

import { useEffect, useState } from "react";

import type { ApiClient, MCPServer } from "@cubeplex/core";
import {
  wsDeleteMyCredential, wsDeleteWorkspaceCredential,
  wsGetMyCredential, wsGetWorkspaceCredential,
  wsPutMyCredential, wsPutWorkspaceCredential,
} from "@cubeplex/core";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { MCPSecretInput } from "./MCPSecretInput";

export interface MCPCredentialPanelProps {
  server: MCPServer;
  wsId: string;
  client: ApiClient;
  scopeContext: "owned" | "via-binding";
  /** Forces re-fetch after a change. */
  onChange?: () => void;
}

export function MCPCredentialPanel({
  server, wsId, client, scopeContext, onChange,
}: MCPCredentialPanelProps) {
  const [hasValue, setHasValue] = useState<boolean>(false);
  const [draftPlain, setDraftPlain] = useState<string>("");

  const isUserScope = server.credential_scope === "user";
  const isWorkspaceScope = server.credential_scope === "workspace";
  const isOrgScope = server.credential_scope === "org";
  const isNoneScope = server.credential_scope === "none";

  useEffect(() => {
    let active = true;
    (async () => {
      if (isUserScope) {
        const s = await wsGetMyCredential(client, wsId, server.id);
        if (active) setHasValue(s.has_value);
      } else if (isWorkspaceScope) {
        const s = await wsGetWorkspaceCredential(client, wsId, server.id);
        if (active) setHasValue(s.has_value);
      }
    })();
    return () => { active = false; };
  }, [server.id, wsId, client, isUserScope, isWorkspaceScope]);

  if (isOrgScope) {
    return (
      <Card>
        <CardHeader><CardTitle className="text-base">凭证</CardTitle></CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">由 organization admin 管理。</p>
        </CardContent>
      </Card>
    );
  }
  if (isNoneScope) {
    return (
      <Card>
        <CardHeader><CardTitle className="text-base">认证</CardTitle></CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">
            使用 cubeplex 身份认证 — 无需配置 key。
          </p>
        </CardContent>
      </Card>
    );
  }

  const title = isUserScope ? "我的凭证" : "Workspace 共享凭证";
  const save = async () => {
    if (isUserScope) {
      await wsPutMyCredential(client, wsId, server.id, { plaintext: draftPlain });
    } else {
      await wsPutWorkspaceCredential(client, wsId, server.id, { plaintext: draftPlain });
    }
    setHasValue(true);
    setDraftPlain("");
    onChange?.();
  };
  const clear = async () => {
    if (isUserScope) {
      await wsDeleteMyCredential(client, wsId, server.id);
    } else {
      await wsDeleteWorkspaceCredential(client, wsId, server.id);
    }
    setHasValue(false);
    onChange?.();
  };

  return (
    <Card>
      <CardHeader><CardTitle className="text-base">{title}</CardTitle></CardHeader>
      <CardContent className="space-y-4">
        <MCPSecretInput
          label="API key / token"
          hasValue={hasValue}
          required={!hasValue}
          onChange={setDraftPlain}
        />
        <div className="flex gap-2">
          <Button type="button" onClick={save} disabled={!draftPlain}>保存</Button>
          {hasValue && (
            <Button type="button" variant="outline" onClick={clear}>
              清除
            </Button>
          )}
        </div>
        {!hasValue && isUserScope && (
          <p className="text-muted-foreground text-xs">
            未配置时此 server 在 chat agent 中不会出现。
          </p>
        )}
        {!hasValue && isWorkspaceScope && (
          <p className="text-muted-foreground text-xs">
            未配置时此 server 在该 workspace 任意用户的 agent 中都不会出现。
          </p>
        )}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 4: Type-check + commit**

```bash
cd frontend && pnpm type-check
git add frontend/packages/web/components/mcp/MCPServerList.tsx \
        frontend/packages/web/components/mcp/MCPToolsTable.tsx \
        frontend/packages/web/components/mcp/MCPCredentialPanel.tsx
git commit -m "feat(mcp/web): server list + tools accordion + credential panel"
```

---

### Task 47: Bindings grid + promote dialog

**Files:**
- Create: `frontend/packages/web/components/mcp/MCPBindingGrid.tsx`
- Create: `frontend/packages/web/components/mcp/MCPPromoteDialog.tsx`

- [ ] **Step 1: MCPBindingGrid**

```tsx
// frontend/packages/web/components/mcp/MCPBindingGrid.tsx
"use client";

import { useEffect, useState } from "react";

import type { ApiClient, WorkspaceBinding } from "@cubeplex/core";
import { adminGetBindings, adminPutBindings } from "@cubeplex/core";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export interface WorkspaceOption {
  id: string;
  name: string;
}

export interface MCPBindingGridProps {
  client: ApiClient;
  serverId: string;
  workspaces: WorkspaceOption[];   // all org workspaces (admin fetches separately)
}

export function MCPBindingGrid({ client, serverId, workspaces }: MCPBindingGridProps) {
  const [bindings, setBindings] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    (async () => {
      const remote = await adminGetBindings(client, serverId);
      const m: Record<string, boolean> = {};
      for (const b of remote) m[b.workspace_id] = b.enabled;
      setBindings(m);
    })();
  }, [client, serverId]);

  const toggle = (wsId: string, enabled: boolean) => {
    setBindings((b) => ({ ...b, [wsId]: enabled }));
    setDirty(true);
  };

  const save = async () => {
    setSaving(true);
    try {
      const list: WorkspaceBinding[] = Object.entries(bindings).map(([wsId, enabled]) => ({
        workspace_id: wsId, enabled,
      }));
      await adminPutBindings(client, serverId, list);
      setDirty(false);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Workspace</TableHead>
              <TableHead className="w-32 text-right">Enabled</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {workspaces.map((w) => (
              <TableRow key={w.id}>
                <TableCell>{w.name}</TableCell>
                <TableCell className="text-right">
                  <Switch
                    checked={bindings[w.id] ?? false}
                    onCheckedChange={(v) => toggle(w.id, v)}
                  />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <div className="flex items-center justify-between border-t p-3">
          <Button
            variant="ghost" size="sm"
            onClick={() => {
              const all: Record<string, boolean> = {};
              for (const w of workspaces) all[w.id] = true;
              setBindings(all); setDirty(true);
            }}
          >全部启用</Button>
          <Button
            variant="ghost" size="sm"
            onClick={() => {
              const all: Record<string, boolean> = {};
              for (const w of workspaces) all[w.id] = false;
              setBindings(all); setDirty(true);
            }}
          >全部禁用</Button>
          <Button onClick={save} disabled={!dirty || saving}>
            {saving ? "保存中…" : "保存"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 2: MCPPromoteDialog**

```tsx
// frontend/packages/web/components/mcp/MCPPromoteDialog.tsx
"use client";

import { useState } from "react";

import type { MCPServer } from "@cubeplex/core";

import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Label } from "@/components/ui/label";

export interface MCPPromoteDialogProps {
  server: MCPServer;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (shareCredential: boolean) => Promise<void>;
}

export function MCPPromoteDialog({
  server, open, onOpenChange, onConfirm,
}: MCPPromoteDialogProps) {
  const [share, setShare] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  const showShareOption = server.credential_scope === "workspace";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>共享给组织</DialogTitle>
          <DialogDescription>
            升级后，admin 可以 binding 给其他 workspace 使用。
          </DialogDescription>
        </DialogHeader>
        {showShareOption && (
          <div className="space-y-3 py-2">
            <Label>凭证一同共享？</Label>
            <RadioGroup
              value={share ? "share" : "keep"}
              onValueChange={(v) => setShare(v === "share")}
            >
              <label className="flex items-start gap-2 rounded-md border p-3">
                <RadioGroupItem value="share" />
                <span>
                  <span className="block font-medium">共享</span>
                  <span className="text-muted-foreground text-sm">
                    其他 workspace 直接复用此 key
                  </span>
                </span>
              </label>
              <label className="flex items-start gap-2 rounded-md border p-3">
                <RadioGroupItem value="keep" />
                <span>
                  <span className="block font-medium">不共享</span>
                  <span className="text-muted-foreground text-sm">
                    其他 workspace 必须各自填 key
                  </span>
                </span>
              </label>
            </RadioGroup>
          </div>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={async () => {
              setSubmitting(true);
              try {
                await onConfirm(showShareOption ? share : false);
                onOpenChange(false);
              } finally { setSubmitting(false); }
            }}
            disabled={submitting}
          >
            确认升级
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 3: Type-check + commit**

```bash
cd frontend && pnpm type-check
git add frontend/packages/web/components/mcp/MCPBindingGrid.tsx \
        frontend/packages/web/components/mcp/MCPPromoteDialog.tsx
git commit -m "feat(mcp/web): bindings grid + promote dialog"
```

---

### Task 48: Detail component

**Files:**
- Create: `frontend/packages/web/components/mcp/MCPServerDetail.tsx`

- [ ] **Step 1: Implement**

```tsx
// frontend/packages/web/components/mcp/MCPServerDetail.tsx
"use client";

import { useState } from "react";
import { CircleDot, RefreshCw, Trash2 } from "lucide-react";

import type { ApiClient, MCPServer } from "@cubeplex/core";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { MCPBindingGrid, type WorkspaceOption } from "./MCPBindingGrid";
import { MCPCredentialPanel } from "./MCPCredentialPanel";
import { MCPPromoteDialog } from "./MCPPromoteDialog";
import { MCPScopeBadge } from "./MCPScopeBadge";
import { MCPToolsTable } from "./MCPToolsTable";

export interface MCPServerDetailProps {
  server: MCPServer;
  mode: "admin" | "ws-owned" | "ws-readonly";
  client: ApiClient;
  wsId?: string;
  workspaces?: WorkspaceOption[];   // for admin bindings tab
  onRefresh: () => Promise<void>;
  onDelete?: () => Promise<void>;
  onPromote?: (shareCredential: boolean) => Promise<void>;
}

export function MCPServerDetail({
  server, mode, client, wsId, workspaces,
  onRefresh, onDelete, onPromote,
}: MCPServerDetailProps) {
  const [refreshing, setRefreshing] = useState(false);
  const [promoteOpen, setPromoteOpen] = useState(false);

  const showBindingsTab =
    mode === "admin" && server.owner_workspace_id === null;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <CircleDot
          className={`size-3 ${server.authed ? "text-green-600" : "text-red-600"}`}
          title={server.last_error ?? undefined}
        />
        <h1 className="text-2xl font-semibold">{server.name}</h1>
        <MCPScopeBadge scope={server.credential_scope} />
        <span className="text-muted-foreground text-sm">
          {server.transport} ·{" "}
          {server.last_discovered_at
            ? `last discovered ${new Date(server.last_discovered_at).toLocaleString()}`
            : "not discovered yet"}
        </span>
        <div className="ml-auto flex gap-2">
          <Button
            variant="outline" size="sm"
            disabled={refreshing}
            onClick={async () => {
              setRefreshing(true);
              try { await onRefresh(); } finally { setRefreshing(false); }
            }}
          >
            <RefreshCw className={`mr-1.5 size-4 ${refreshing ? "animate-spin" : ""}`} />
            Refresh tools
          </Button>
          {mode === "ws-owned" && onPromote && (
            <Button variant="outline" size="sm" onClick={() => setPromoteOpen(true)}>
              共享给组织…
            </Button>
          )}
          {onDelete && (
            <Button variant="destructive" size="sm" onClick={onDelete}>
              <Trash2 className="mr-1.5 size-4" />
              删除
            </Button>
          )}
        </div>
      </div>

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">概览</TabsTrigger>
          <TabsTrigger value="tools">Tools ({server.tools_cache?.length ?? 0})</TabsTrigger>
          {showBindingsTab && <TabsTrigger value="bindings">Workspaces</TabsTrigger>}
        </TabsList>

        <TabsContent value="overview" className="space-y-4">
          <Card>
            <CardHeader><CardTitle className="text-base">基本信息</CardTitle></CardHeader>
            <CardContent className="text-sm space-y-2">
              <div><span className="text-muted-foreground">URL: </span>{server.server_url}</div>
              <div><span className="text-muted-foreground">Transport: </span>{server.transport}</div>
              <div><span className="text-muted-foreground">Auth method: </span>{server.auth_method}</div>
              <div><span className="text-muted-foreground">Timeout: </span>{server.timeout}s / SSE: {server.sse_read_timeout}s</div>
              {server.last_error && (
                <div className="text-destructive">
                  <span className="text-muted-foreground">Last error: </span>{server.last_error}
                </div>
              )}
            </CardContent>
          </Card>
          {(mode === "ws-owned" || mode === "ws-readonly") && wsId && (
            <MCPCredentialPanel
              server={server} wsId={wsId} client={client}
              scopeContext={mode === "ws-owned" ? "owned" : "via-binding"}
            />
          )}
        </TabsContent>

        <TabsContent value="tools">
          <MCPToolsTable tools={server.tools_cache ?? []} />
        </TabsContent>

        {showBindingsTab && workspaces && (
          <TabsContent value="bindings">
            <MCPBindingGrid client={client} serverId={server.id} workspaces={workspaces} />
          </TabsContent>
        )}
      </Tabs>

      {onPromote && (
        <MCPPromoteDialog
          server={server} open={promoteOpen} onOpenChange={setPromoteOpen}
          onConfirm={onPromote}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 2: Type-check + commit**

```bash
cd frontend && pnpm type-check
git add frontend/packages/web/components/mcp/MCPServerDetail.tsx
git commit -m "feat(mcp/web): MCPServerDetail with tabs (overview/tools/bindings)"
```

---

### Task 49: Admin pages

**Files:**
- Modify: `frontend/packages/web/app/admin/mcp/page.tsx` (replace ComingSoonCard)
- Create: `frontend/packages/web/app/admin/mcp/new/page.tsx`
- Create: `frontend/packages/web/app/admin/mcp/[id]/page.tsx`

- [ ] **Step 1: Replace `/admin/mcp/page.tsx`**

```tsx
// frontend/packages/web/app/admin/mcp/page.tsx
"use client";

import Link from "next/link";
import { useEffect, useMemo } from "react";

import { createApiClient, useMcpStore } from "@cubeplex/core";

import { Button } from "@/components/ui/button";
import { MCPServerList } from "@/components/mcp/MCPServerList";

export default function AdminMcpPage() {
  const client = useMemo(() => createApiClient(""), []);
  const { servers, loading, fetchAll } = useMcpStore();

  useEffect(() => {
    fetchAll(client);
  }, [client, fetchAll]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">MCP 连接器</h1>
        <Button asChild>
          <Link href="/admin/mcp/new">+ 添加 server</Link>
        </Button>
      </div>
      <MCPServerList
        servers={servers}
        loading={loading}
        detailHrefBase="/admin/mcp"
        emptyTitle="尚未配置 MCP 连接器"
        emptyDescription="MCP server 让 agent 可调用外部工具。点击右上角添加第一个。"
      />
    </div>
  );
}
```

- [ ] **Step 2: New page**

```tsx
// frontend/packages/web/app/admin/mcp/new/page.tsx
"use client";

import { useRouter } from "next/navigation";

import { useMemo } from "react";

import { createApiClient, useMcpStore } from "@cubeplex/core";

import { MCPServerForm, type MCPServerFormValues } from "@/components/mcp/MCPServerForm";

export default function NewAdminMcpPage() {
  const router = useRouter();
  const client = useMemo(() => createApiClient(""), []);
  const { create, testConnection } = useMcpStore();

  const handleSubmit = async (v: MCPServerFormValues) => {
    const created = await create(client, {
      name: v.name, server_url: v.server_url, transport: v.transport,
      auth_method: v.auth_method,
      credential_scope: v.credential_scope as "org" | "user" | "none",
      credential_plaintext: v.credential_plaintext || undefined,
      credential_name: v.credential_name || undefined,
      headers: v.headers, timeout: v.timeout, sse_read_timeout: v.sse_read_timeout,
    });
    router.push(`/admin/mcp/${created.id}`);
  };
  const handleTest = (v: MCPServerFormValues) =>
    testConnection(client, {
      server_url: v.server_url, transport: v.transport,
      auth_method: v.auth_method, credential_scope: v.credential_scope,
      credential_plaintext: v.credential_plaintext || undefined,
      headers: v.headers, timeout: v.timeout, sse_read_timeout: v.sse_read_timeout,
    });

  return (
    <div className="max-w-3xl space-y-6">
      <h1 className="text-2xl font-semibold">添加 MCP server</h1>
      <MCPServerForm
        mode="admin"
        onSubmit={handleSubmit}
        onTestConnection={handleTest}
        onCancel={() => router.back()}
      />
    </div>
  );
}
```

- [ ] **Step 3: Detail page**

```tsx
// frontend/packages/web/app/admin/mcp/[id]/page.tsx
"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { adminGetServer, createApiClient, useMcpStore, useWorkspaceStore } from "@cubeplex/core";
import type { MCPServer } from "@cubeplex/core";

import { MCPServerDetail } from "@/components/mcp/MCPServerDetail";

export default function AdminMcpDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const client = useMemo(() => createApiClient(""), []);
  const { refreshTools, remove } = useMcpStore();
  const { workspaces, fetchAll: fetchWorkspaces } = useWorkspaceStore();
  const [server, setServer] = useState<MCPServer | null>(null);

  useEffect(() => {
    fetchWorkspaces(client);
    adminGetServer(client, params.id).then(setServer);
  }, [client, params.id, fetchWorkspaces]);

  if (!server) return <div>Loading…</div>;

  return (
    <MCPServerDetail
      server={server}
      mode="admin"
      client={client}
      workspaces={workspaces.map((w) => ({ id: w.id, name: w.name }))}
      onRefresh={async () => {
        const r = await refreshTools(client, server.id);
        setServer(r);
      }}
      onDelete={async () => {
        if (!confirm(`确认删除 ${server.name}？此操作会级联删 binding 与 cred。`)) return;
        await remove(client, server.id);
        router.push("/admin/mcp");
      }}
    />
  );
}
```

- [ ] **Step 4: Type-check + commit**

```bash
cd frontend && pnpm type-check
git add frontend/packages/web/app/admin/mcp/page.tsx \
        frontend/packages/web/app/admin/mcp/new/page.tsx \
        frontend/packages/web/app/admin/mcp/[id]/page.tsx
git commit -m "feat(mcp/web): admin /admin/mcp pages (list / new / detail)"
```

---

### Task 50: WS member pages

**Files:**
- Create: `frontend/packages/web/app/(app)/w/[wsId]/integrations/mcp/page.tsx`
- Create: `frontend/packages/web/app/(app)/w/[wsId]/integrations/mcp/new/page.tsx`
- Create: `frontend/packages/web/app/(app)/w/[wsId]/integrations/mcp/[id]/page.tsx`

- [ ] **Step 1: List page**

```tsx
// frontend/packages/web/app/(app)/w/[wsId]/integrations/mcp/page.tsx
"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo } from "react";

import { createApiClient, useWorkspaceMcpStore } from "@cubeplex/core";

import { Button } from "@/components/ui/button";
import { MCPServerList } from "@/components/mcp/MCPServerList";

export default function WsMcpListPage() {
  const { wsId } = useParams<{ wsId: string }>();
  const client = useMemo(() => {
    const c = createApiClient("");
    c.setWorkspaceId(wsId);
    return c;
  }, [wsId]);
  const { owned, viaBinding, loading, fetchAll } = useWorkspaceMcpStore();

  useEffect(() => {
    fetchAll(client, wsId);
  }, [client, wsId, fetchAll]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Workspace MCP 连接器</h1>
        <Button asChild>
          <Link href={`/w/${wsId}/integrations/mcp/new`}>+ 添加</Link>
        </Button>
      </div>
      <section className="space-y-3">
        <h2 className="text-base font-medium">本 workspace 私有</h2>
        <MCPServerList
          servers={owned}
          loading={loading}
          detailHrefBase={`/w/${wsId}/integrations/mcp`}
          emptyTitle="此 workspace 暂无私有 MCP server"
          emptyDescription="添加一个仅本 workspace 可见的 MCP server。"
        />
      </section>
      <section className="space-y-3">
        <h2 className="text-base font-medium">来自组织（只读）</h2>
        <MCPServerList
          servers={viaBinding}
          loading={loading}
          detailHrefBase={`/w/${wsId}/integrations/mcp`}
          emptyTitle="尚无 admin 共享给本 workspace 的 MCP server"
          emptyDescription="organization admin 在 admin 控制台 binding 后会出现在这里。"
        />
      </section>
    </div>
  );
}
```

- [ ] **Step 2: New page**

```tsx
// frontend/packages/web/app/(app)/w/[wsId]/integrations/mcp/new/page.tsx
"use client";

import { useParams, useRouter } from "next/navigation";

import { useMemo } from "react";

import { createApiClient, useWorkspaceMcpStore, wsTestConnection } from "@cubeplex/core";

import { MCPServerForm, type MCPServerFormValues } from "@/components/mcp/MCPServerForm";

export default function NewWsMcpPage() {
  const { wsId } = useParams<{ wsId: string }>();
  const router = useRouter();
  const client = useMemo(() => {
    const c = createApiClient("");
    c.setWorkspaceId(wsId);
    return c;
  }, [wsId]);
  const { create } = useWorkspaceMcpStore();

  const handleSubmit = async (v: MCPServerFormValues) => {
    if (v.credential_scope === "org") {
      throw new Error("workspace member cannot create org-scope server");
    }
    const created = await create(client, wsId, {
      name: v.name, server_url: v.server_url, transport: v.transport,
      auth_method: v.auth_method,
      credential_scope: v.credential_scope as "workspace" | "user" | "none",
      credential_plaintext: v.credential_plaintext || undefined,
      credential_name: v.credential_name || undefined,
      headers: v.headers, timeout: v.timeout, sse_read_timeout: v.sse_read_timeout,
    });
    router.push(`/w/${wsId}/integrations/mcp/${created.id}`);
  };

  const handleTest = (v: MCPServerFormValues) =>
    wsTestConnection(client, wsId, {
      server_url: v.server_url, transport: v.transport,
      auth_method: v.auth_method, credential_scope: v.credential_scope,
      credential_plaintext: v.credential_plaintext || undefined,
      headers: v.headers, timeout: v.timeout, sse_read_timeout: v.sse_read_timeout,
    });

  return (
    <div className="max-w-3xl space-y-6">
      <h1 className="text-2xl font-semibold">添加 MCP server</h1>
      <MCPServerForm
        mode="ws-member"
        onSubmit={handleSubmit}
        onTestConnection={handleTest}
        onCancel={() => router.back()}
      />
    </div>
  );
}
```

- [ ] **Step 3: Detail page**

```tsx
// frontend/packages/web/app/(app)/w/[wsId]/integrations/mcp/[id]/page.tsx
"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { createApiClient, wsGetServer, useWorkspaceMcpStore } from "@cubeplex/core";
import type { MCPServer } from "@cubeplex/core";

import { MCPServerDetail } from "@/components/mcp/MCPServerDetail";

export default function WsMcpDetailPage() {
  const { wsId, id } = useParams<{ wsId: string; id: string }>();
  const router = useRouter();
  const client = useMemo(() => {
    const c = createApiClient("");
    c.setWorkspaceId(wsId);
    return c;
  }, [wsId]);
  const { remove, refreshTools, promote } = useWorkspaceMcpStore();
  const [server, setServer] = useState<MCPServer | null>(null);

  useEffect(() => {
    wsGetServer(client, wsId, id).then(setServer);
  }, [client, wsId, id]);

  if (!server) return <div>Loading…</div>;

  const isOwned = server.owner_workspace_id === wsId;
  const mode = isOwned ? "ws-owned" : "ws-readonly";

  return (
    <MCPServerDetail
      server={server}
      mode={mode}
      client={client}
      wsId={wsId}
      onRefresh={async () => {
        if (!isOwned) return;
        const r = await refreshTools(client, wsId, id);
        setServer(r);
      }}
      onDelete={
        isOwned
          ? async () => {
              if (!confirm(`确认删除 ${server.name}？`)) return;
              await remove(client, wsId, id);
              router.push(`/w/${wsId}/integrations/mcp`);
            }
          : undefined
      }
      onPromote={
        isOwned
          ? async (shareCredential: boolean) => {
              const promoted = await promote(client, wsId, id, { share_credential: shareCredential });
              setServer(promoted);
            }
          : undefined
      }
    />
  );
}
```

- [ ] **Step 4: Type-check + commit**

```bash
cd frontend && pnpm type-check
git add frontend/packages/web/app/\(app\)/w/\[wsId\]/integrations/mcp/page.tsx \
        frontend/packages/web/app/\(app\)/w/\[wsId\]/integrations/mcp/new/page.tsx \
        frontend/packages/web/app/\(app\)/w/\[wsId\]/integrations/mcp/\[id\]/page.tsx
git commit -m "feat(mcp/web): workspace member pages (list/new/detail)"
```

---

### Task 51: Frontend Playwright E2E

**Files:**
- Create: `frontend/packages/web/__tests__/e2e/mcp/_helpers.ts`
- Create: `frontend/packages/web/__tests__/e2e/mcp/admin-mcp.spec.ts`
- Create: `frontend/packages/web/__tests__/e2e/mcp/ws-mcp.spec.ts`

- [ ] **Step 1: MCP E2E helpers**

```ts
// frontend/packages/web/__tests__/e2e/mcp/_helpers.ts
import { expect, type Page } from '@playwright/test'

import { registerAsAdmin } from '../skills/_helpers'

export async function registerAndGetWorkspace(page: Page): Promise<{ wsId: string }> {
  await registerAsAdmin(page)
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })
  const match = page.url().match(/\/w\/([^/?#]+)/)
  if (!match) throw new Error(`Could not parse workspace id from URL: ${page.url()}`)
  return { wsId: match[1] }
}

async function csrf(page: Page): Promise<string> {
  const cookies = await page.context().cookies()
  return cookies.find((c) => c.name === 'cubeplex_csrf')?.value ?? ''
}

export async function createWorkspace(page: Page, name: string): Promise<{ id: string }> {
  const listResp = await page.request.get('/api/v1/workspaces')
  if (!listResp.ok()) throw new Error(`list workspaces failed: ${listResp.status()}`)
  const workspaces = (await listResp.json()) as Array<{ id: string; org_id: string }>
  const orgId = workspaces[0]?.org_id
  if (!orgId) throw new Error('No bootstrap workspace/org found')

  const createResp = await page.request.post('/api/v1/workspaces', {
    headers: { 'X-CSRF-Token': await csrf(page) },
    data: { name, org_id: orgId },
  })
  if (!createResp.ok()) throw new Error(`create workspace failed: ${createResp.status()}`)
  return (await createResp.json()) as { id: string }
}

export async function createOrgMcpServer(
  page: Page,
  name: string,
): Promise<{ id: string }> {
  const resp = await page.request.post('/api/v1/admin/mcp/servers', {
    headers: { 'X-CSRF-Token': await csrf(page) },
    data: {
      name,
      server_url: 'http://127.0.0.1:9/mcp',
      transport: 'streamable_http',
      auth_method: 'static',
      credential_scope: 'org',
      credential_name: `${name} token`,
      credential_plaintext: 'test-secret-value',
      headers: {},
      timeout: 1,
      sse_read_timeout: 1,
    },
  })
  if (!resp.ok()) throw new Error(`create MCP server failed: ${resp.status()}`)
  return (await resp.json()) as { id: string }
}
```

- [ ] **Step 2: Admin MCP E2E**

```ts
// frontend/packages/web/__tests__/e2e/mcp/admin-mcp.spec.ts
import { expect, test } from '@playwright/test'

import { createOrgMcpServer, createWorkspace, registerAndGetWorkspace } from './_helpers'

test.describe('Admin MCP page', () => {
  test('OAuth radio is disabled with Coming soon', async ({ page }) => {
    await registerAndGetWorkspace(page)
    await page.goto('/admin/mcp/new')
    await expect(page.getByLabel('OAuth')).toBeDisabled()
    await expect(page.getByText('Coming soon')).toBeVisible()
  })

  test('Credential input never reveals saved plaintext', async ({ page }) => {
    await registerAndGetWorkspace(page)
    const server = await createOrgMcpServer(page, 'Secret UI Test')

    await page.goto(`/admin/mcp/${server.id}`)
    await expect(page.getByText('•••• (已设置)')).toBeVisible()
    await page.getByRole('button', { name: '替换' }).click()
    await expect(page.getByLabel('API key / token')).toHaveValue('')
    await expect(page.getByText('test-secret-value')).toHaveCount(0)
  })

  test('Bindings grid bulk save persists', async ({ page }) => {
    await registerAndGetWorkspace(page)
    await createWorkspace(page, 'workspace-A')
    const server = await createOrgMcpServer(page, 'Binding UI Test')

    await page.goto(`/admin/mcp/${server.id}`)
    await page.getByRole('tab', { name: 'Workspaces' }).click()
    await page.getByRole('switch', { name: /workspace-A/i }).click()
    await page.getByRole('button', { name: '保存' }).click()
    await page.reload()
    await page.getByRole('tab', { name: 'Workspaces' }).click()
    await expect(page.getByRole('switch', { name: /workspace-A/i })).toBeChecked()
  })
})
```

- [ ] **Step 3: WS MCP E2E**

```ts
// frontend/packages/web/__tests__/e2e/mcp/ws-mcp.spec.ts
import { expect, test } from '@playwright/test'

import { registerAndGetWorkspace } from './_helpers'

test.describe('Workspace MCP', () => {
  test('Member creates workspace-shared MCP', async ({ page }) => {
    const { wsId } = await registerAndGetWorkspace(page)
    await page.goto(`/w/${wsId}/integrations/mcp/new`)
    await page.getByLabel('Name *').fill('MyTool')
    await page.getByLabel('Server URL *').fill('http://127.0.0.1:9/mcp')
    await page.getByText('Workspace shared').click()
    await page.getByLabel('API key / token').fill('tok')
    await page.getByRole('button', { name: '测试连接' }).click()
    await expect(page.getByText('连接失败').first()).toBeVisible()
    await page.getByRole('button', { name: '保存' }).click()
    await expect(page).toHaveURL(/\/integrations\/mcp\/[^/]+$/)
  })

  test('Promote dialog shows share-credential option for workspace scope', async ({ page }) => {
    const { wsId } = await registerAndGetWorkspace(page)
    await page.goto(`/w/${wsId}/integrations/mcp/new`)
    await page.getByLabel('Name *').fill('PromoteTool')
    await page.getByLabel('Server URL *').fill('http://127.0.0.1:9/mcp')
    await page.getByText('Workspace shared').click()
    await page.getByLabel('API key / token').fill('tok')
    await page.getByRole('button', { name: '保存' }).click()
    await expect(page).toHaveURL(/\/integrations\/mcp\/[^/]+$/)
    await page.getByRole('button', { name: '共享给组织…' }).click()
    await expect(page.getByText('凭证一同共享？')).toBeVisible()
    await expect(page.getByLabel('共享')).toBeVisible()
    await expect(page.getByLabel('不共享')).toBeVisible()
  })
})
```

- [ ] **Step 4: Run**

```bash
cd frontend && pnpm test:e2e mcp
```

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/__tests__/e2e/mcp/_helpers.ts \
        frontend/packages/web/__tests__/e2e/mcp/admin-mcp.spec.ts \
        frontend/packages/web/__tests__/e2e/mcp/ws-mcp.spec.ts
git commit -m "test(mcp/web): playwright e2e for admin + ws MCP flows"
```

---

## Phase G · Final Integration + Docs (Stages 7-8)

### Task 52: Backend final integration gate

**Files:**
- Modify only files needed to fix failures found by this task.

- [ ] **Step 1: Confirm no stale draft paths or runtime assumptions remain**

```bash
rg -n "PHASE[_]B_TODO|PHASE[_]G|await create_cubeplex_agent|create_cubeplex_agent now per" \
  docs/superpowers/plans/2026-04-30-m1e4-vault-and-m2-mcp-connectors.md \
  docs/superpowers/specs/2026-04-30-m1e4-vault-and-m2-mcp-connectors-design.md
```

Expected: no matches. If matches point to implementation instructions, fix the plan/spec before
implementing code.

- [ ] **Step 2: Alembic upgrade / downgrade / upgrade**

```bash
cd backend
alembic upgrade head
alembic downgrade -1
alembic upgrade head
```

Expected: all commands exit 0. If downgrade drops the final MCP migration, `upgrade head` must
recreate it cleanly.

- [ ] **Step 3: Backend focused tests**

```bash
cd backend
uv run pytest tests/unit/test_fernet_rotation.py \
  tests/unit/test_vault_key_loading.py \
  tests/unit/test_user_token_signer.py \
  tests/unit/test_connection_params.py \
  tests/unit/test_discovery_serialize.py \
  tests/unit/test_mcp_service_invariants.py \
  tests/unit/test_run_streaming.py \
  -v
```

Expected: all selected unit tests pass.

- [ ] **Step 4: Backend MCP E2E tests**

```bash
cd backend
uv run pytest tests/e2e/test_credentials_vault.py \
  tests/e2e/test_admin_mcp_crud.py \
  tests/e2e/test_ws_mcp_crud.py \
  tests/e2e/test_mcp_promote.py \
  tests/e2e/test_mcp_user_scope.py \
  tests/e2e/test_mcp_bindings.py \
  tests/e2e/test_mcp_discovery_failure.py \
  tests/e2e/test_mcp_oauth_placeholder.py \
  tests/e2e/test_mcp_passthrough.py \
  tests/e2e/test_legacy_mcp_coexists.py \
  -v
```

Expected: all selected E2E tests pass.

- [ ] **Step 5: Backend quality gate**

```bash
cd backend
make lint
make type-check
```

Expected: both commands exit 0.

- [ ] **Step 6: Commit fixes from this gate**

```bash
git status --short
# If files changed:
git add backend
git commit -m "fix(mcp): backend final integration issues"
```

---

### Task 53: Frontend final integration gate

**Files:**
- Modify only files needed to fix failures found by this task.

- [ ] **Step 1: Core exports are complete**

```bash
rg -n "mcp" frontend/packages/core/src/index.ts frontend/packages/core/src/api/index.ts
```

Expected: `types/mcp`, `api/mcp`, `stores/mcpStore`, and `stores/workspaceMcpStore`
exports are present.

- [ ] **Step 2: No non-existent frontend helpers remain**

```bash
rg -n "useApiClient|loginAsAdmin|loginAsMember|createdServerId|serverId = await" \
  frontend/packages/web frontend/packages/core
```

Expected: no matches, except legitimate component prop names such as `serverId`.

- [ ] **Step 3: Build shared core**

```bash
cd frontend
pnpm --filter @cubeplex/core build
```

Expected: exit 0.

- [ ] **Step 4: Type-check web**

```bash
cd frontend
pnpm type-check
```

Expected: exit 0.

- [ ] **Step 5: Playwright MCP E2E**

```bash
cd frontend
pnpm test:e2e mcp
```

Expected: all MCP Playwright tests pass.

- [ ] **Step 6: Commit fixes from this gate**

```bash
git status --short
# If files changed:
git add frontend
git commit -m "fix(mcp/web): frontend final integration issues"
```

---

### Task 54: Security and plaintext regression audit

**Files:**
- Modify only files needed to fix failures found by this task.

- [ ] **Step 1: Search for accidental plaintext exposure in backend responses**

```bash
rg -n "plaintext|value_encrypted|credential_plaintext|test-secret-value|ghp_|tok" \
  backend/cubeplex backend/tests
```

Expected:
- `credential_plaintext` appears only in request schemas/tests.
- `value_encrypted` appears only in models/repositories/vault internals.
- Test secrets appear only in tests.
- No response schema includes plaintext.

- [ ] **Step 2: Search for accidental plaintext exposure in frontend display code**

```bash
rg -n "credential_plaintext|test-secret-value|ghp_|tok|value_encrypted" \
  frontend/packages/core/src frontend/packages/web
```

Expected:
- `credential_plaintext` appears only in create/update request bodies and form state.
- Secret placeholder display uses `•••• (已设置)` or equivalent.
- No page renders saved plaintext.

- [ ] **Step 3: Verify vault key startup failure**

```bash
cd backend
env -u CUBEPLEX_AUTH__VAULT_KEY uv run pytest tests/e2e/test_app_boot.py -v
```

Expected: the test that asserts fail-fast on missing vault key passes.

- [ ] **Step 4: Commit fixes from this audit**

```bash
git status --short
# If files changed:
git add backend frontend
git commit -m "fix(mcp): prevent credential plaintext exposure"
```

---

### Task 55: Operational docs and env examples

**Files:**
- Modify: `backend/.env.example`
- Modify: `AGENTS.md`

- [ ] **Step 1: Ensure `.env.example` includes vault key**

`backend/.env.example` must include:

```bash
# Credential Vault master key. Generate with:
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Rotate by setting newest first: CUBEPLEX_AUTH__VAULT_KEY=<new>,<old>
CUBEPLEX_AUTH__VAULT_KEY=
```

- [ ] **Step 2: Add AGENTS.md backend env note**

Under backend environment variables in `AGENTS.md`, include:

```markdown
  - `CUBEPLEX_AUTH__VAULT_KEY` — comma-separated Fernet keys; first key encrypts,
    all keys decrypt. Required once Credential Vault is enabled.
```

- [ ] **Step 3: Add AGENTS.md operational note**

Under backend essentials or database notes, include:

```markdown
- Vault rotation:
  - Generate a new Fernet key.
  - Deploy `CUBEPLEX_AUTH__VAULT_KEY=<new>,<old>`.
  - Run the key rotation command once it lands.
  - Deploy `CUBEPLEX_AUTH__VAULT_KEY=<new>` only after rotation is verified.
```

- [ ] **Step 4: Commit docs/env updates**

```bash
git add backend/.env.example AGENTS.md
git commit -m "docs(mcp): document vault key configuration"
```

---

### Task 56: Full backend + frontend regression

**Files:**
- Modify only files needed to fix failures found by this task.

- [ ] **Step 1: Backend full check**

```bash
cd backend
make check
```

Expected: format/lint/type-check/test all pass.

- [ ] **Step 2: Frontend full check**

```bash
cd frontend
pnpm --filter @cubeplex/core build
pnpm type-check
pnpm test:e2e
```

Expected: core build, TS type-check, and Playwright suite pass.

- [ ] **Step 3: Commit final regression fixes**

```bash
git status --short
# If files changed:
git add backend frontend
git commit -m "fix(mcp): full regression fallout"
```

---

### Task 57: Implementation self-review

**Files:**
- No production file changes expected.

- [ ] **Step 1: Spec coverage checklist**

Review `docs/superpowers/specs/2026-04-30-m1e4-vault-and-m2-mcp-connectors-design.md`
section by section. Confirm each item maps to an implemented task:

```text
3 Data model -> Tasks 4, 8, 9
4 Vault -> Tasks 2, 3, 5, 6, 7, 20, 54
5 MCP -> Tasks 10-40, 52
6 Frontend UI -> Tasks 41-51, 53
7 Runtime -> Tasks 28-31, 52
8 Tests -> Tasks 7, 12-15, 18, 20, 27, 32-40, 51-56
9 Staging -> Phase A-G task order
10 Risks -> Tasks 52-56
```

- [ ] **Step 2: Git diff review**

```bash
git status --short
git log --oneline --max-count=20
git diff --stat origin/main...HEAD
```

Expected: changes are scoped to vault/MCP/backend/frontend/docs listed in this plan.

- [ ] **Step 3: Commit self-review fixes if needed**

```bash
git status --short
# If files changed:
git add .
git commit -m "fix(mcp): self-review corrections"
```

---

### Task 58: Final handoff

**Files:**
- No file changes.

- [ ] **Step 1: Produce handoff summary**

Write a concise handoff in the PR description or final implementation message:

```markdown
## Summary
- Added Fernet-backed internal Credential Vault with key rotation-ready MultiFernet support.
- Added DB-backed MCP connector management for admin and workspace member flows.
- Added run-scoped DB MCP tool assembly in RunManager.
- Added admin/ws frontend pages and E2E coverage.

## Verification
- backend: `make check`
- frontend: `pnpm --filter @cubeplex/core build`
- frontend: `pnpm type-check`
- frontend: `pnpm test:e2e`

## Operational Notes
- Requires `CUBEPLEX_AUTH__VAULT_KEY`.
- Existing `config.yaml mcp.servers` remains supported as legacy global tools.
- OAuth is intentionally rejected with `mcp_oauth_not_implemented`.
```

- [ ] **Step 2: Confirm no uncommitted implementation changes remain**

```bash
git status --short
```

Expected: empty, or only intentionally untracked local files outside the implementation.
