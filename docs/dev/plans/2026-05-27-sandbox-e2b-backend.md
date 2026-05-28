# Sandbox e2b Backend Implementation Plan

> For agentic workers. Each task is bite-sized, test-first, with exact files,
> real code, the command to run, and the expected output. Do the tasks in
> order; do not skip the test step.

**Spec:** `docs/dev/specs/2026-05-27-sandbox-e2b-backend-design.md`
**Issue:** #146

---

## Goal

Make the sandbox backend pluggable and add **e2b** as a second provider.
Today `SandboxManager` imports the `opensandbox` SDK directly and hardcodes the
`OpenSandbox` driver for lifecycle (create / connect / health / kill / PVC).
We extract a thin **`SandboxProvider`** lifecycle seam (with provider-neutral
argument dataclasses), refactor the manager to call it via an
`OpenSandboxProvider`, then add an `E2BProvider` + `E2BSandbox`. A new config
key `sandbox.provider` plus a factory selects the active provider; the e2b API
key is read from the credential vault. The per-run call site
(`LazySandbox(manager=...)`) is unchanged — no caller names a concrete driver.

This plan implements the v1 scope from the spec and **only** that scope. Out of
scope (deferred, do not build): e2b custom Neko template / browser live view on
e2b, e2b pause/resume wired into #145, egress-exchange secret swap on e2b,
per-run/per-workspace provider override.

## Architecture

```
run_manager  ──>  LazySandbox(manager=get_sandbox_manager())   [unchanged]
                        │  _ensure() → manager.get_or_create(...)
                        ▼
                  SandboxManager
                        │  holds one SandboxProvider (chosen at startup)
                        │  calls provider.create / connect / kill / set_lifetime
                        ▼
            ┌───────────────────────────┐
            │   SandboxProvider (ABC)    │   ← NEW lifecycle seam
            └───────────────────────────┘
              ▲                       ▲
   OpenSandboxProvider          E2BProvider          ← NEW
     │ create→opensandbox.*       │ create→e2b Sandbox.create
     │ PVC volumes                │ ignores volumes
     ▼                            ▼
   OpenSandbox(Sandbox)        E2BSandbox(Sandbox)    ← NEW driver
     [unchanged]                 commands.run / files.*

build_sandbox_provider(config) reads sandbox.provider → returns the provider.
init_sandbox_manager(...) builds the provider once and hands it to the manager.
```

The per-instance `Sandbox` ABC (`base.py`) is **unchanged** — both drivers
already satisfy it. The seam is only for *lifecycle*, which is what is currently
inlined in the manager.

Provider-neutral argument dataclasses (so the manager never passes
OpenSandbox/e2b-shaped objects):

- `SandboxResource(cpu: str, memory: str)`
- `SandboxVolume(name: str, claim_name: str, mount_path: str, read_only: bool)`
- `SandboxNetwork(allow_internet: bool, allow_out: list[str], deny_out: list[str], allow_public_traffic: bool, opensandbox_policy: object | None)`
- `SandboxCreateRequest(image, workdir, resource, network, volumes, ttl_seconds, ready_timeout, create_timeout)`

`SandboxNetwork` carries both shapes: e2b maps `allow_internet` →
top-level `allow_internet_access` kwarg and the rest → the `network` kwarg;
OpenSandbox uses `opensandbox_policy` (the existing `network_policy` object the
egress injector already produces) and ignores the e2b fields. This keeps the
egress wiring (`_apply_egress`, `SandboxEnvInjector`) untouched in v1.

## Tech Stack

- Python 3.12, FastAPI, async SQLModel/SQLAlchemy, loguru, pytest +
  pytest-asyncio. mypy strict, line length 100.
- `e2b` Python SDK (async `from e2b import AsyncSandbox`), added via `uv add`.
- Existing: `opensandbox` SDK, credential vault (`CredentialService` +
  `FernetBackend`), `config` (`config.get(...)`).

---

## Task 0 — Verify worktree + add e2b dependency

**Files:** `backend/pyproject.toml`, `backend/uv.lock` (both via `uv add`, not
hand-edited).

1. Confirm branch:

```bash
cd /home/chris/cubebox/.worktrees/feat/sandbox-e2b-backend && git rev-parse --abbrev-ref HEAD
```

Expected: `feat/sandbox-e2b-backend`. If not, STOP.

2. Add the SDK:

```bash
cd /home/chris/cubebox/.worktrees/feat/sandbox-e2b-backend/backend && uv add e2b
```

Expected: `uv` resolves and writes `e2b` into `[project.dependencies]` of
`pyproject.toml` and into `uv.lock`. Then confirm the import + version, and
that `Sandbox.create` exposes the kwargs the spec relies on:

```bash
cd /home/chris/cubebox/.worktrees/feat/sandbox-e2b-backend/backend && \
  uv run python -c "import e2b, inspect; from e2b import AsyncSandbox; \
print('e2b', e2b.__version__); \
print('create kwargs:', sorted(inspect.signature(AsyncSandbox.create).parameters))"
```

Expected: a version string and a kwargs list containing `allow_internet_access`
and `network` (top-level, separate). If the installed SDK's `create` signature
differs from the spec (e.g. `network` is a typed `SandboxNetworkOpts` vs a
dict, or `allow_internet_access` is missing), record the exact shape in
`docs/dev/notes/2026-05-27-e2b-sdk-shape.md` and use the installed shape in
Task 6 — the spec's Open Question 7 anticipated this; the installed SDK wins.

---

## Task 1 — Provider seam: ABC + neutral arg dataclasses (test-first)

Define the lifecycle seam and the provider-neutral argument types. No behavior
change yet; nothing calls these.

**Test file:** `backend/tests/unit/test_sandbox_provider_types.py`

```python
"""Unit: provider-neutral arg dataclasses + SandboxProvider ABC shape."""

from __future__ import annotations

import inspect

from cubebox.sandbox.provider import (
    SandboxCreateRequest,
    SandboxNetwork,
    SandboxProvider,
    SandboxResource,
    SandboxVolume,
)


def test_create_request_holds_neutral_args() -> None:
    req = SandboxCreateRequest(
        image="ubuntu:22.04",
        workdir="/workspace",
        resource=SandboxResource(cpu="1", memory="1Gi"),
        network=SandboxNetwork(),
        volumes=[SandboxVolume(name="v", claim_name="c", mount_path="/workspace")],
        ttl_seconds=1800,
        ready_timeout=300,
        create_timeout=300,
    )
    assert req.resource.cpu == "1"
    assert req.volumes[0].claim_name == "c"
    # Network defaults: internet on, no rules, public traffic blocked.
    assert req.network.allow_internet is True
    assert req.network.allow_out == []
    assert req.network.allow_public_traffic is False


def test_provider_is_abstract_with_lifecycle_methods() -> None:
    assert inspect.isabstract(SandboxProvider)
    for name in ("create", "connect", "kill", "set_lifetime"):
        assert hasattr(SandboxProvider, name)
```

**Impl file:** `backend/cubebox/sandbox/provider.py`

```python
"""SandboxProvider — provider-neutral sandbox *lifecycle* seam.

The per-instance `Sandbox` ABC (base.py) stays provider neutral. This module
adds the lifecycle abstraction (create / connect / kill / set_lifetime) that was
previously inlined in SandboxManager against the opensandbox SDK. Drivers
(OpenSandbox, E2BSandbox) are the `Sandbox` impls a provider returns.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from cubebox.sandbox.base import Sandbox


@dataclass
class SandboxResource:
    cpu: str
    memory: str


@dataclass
class SandboxVolume:
    """A per-user persistent disk request. Only OpenSandbox (PVC) honors this."""

    name: str
    claim_name: str
    mount_path: str
    read_only: bool = False


@dataclass
class SandboxNetwork:
    """Provider-neutral egress shape.

    e2b: ``allow_internet`` → top-level ``allow_internet_access`` kwarg;
    ``allow_out``/``deny_out``/``allow_public_traffic`` → the ``network`` kwarg.
    OpenSandbox: uses ``opensandbox_policy`` (the egress injector's network_policy
    object) and ignores the e2b fields.
    """

    allow_internet: bool = True
    allow_out: list[str] = field(default_factory=list)
    deny_out: list[str] = field(default_factory=list)
    allow_public_traffic: bool = False
    opensandbox_policy: object | None = None


@dataclass
class SandboxCreateRequest:
    image: str
    workdir: str
    resource: SandboxResource
    network: SandboxNetwork
    volumes: list[SandboxVolume]
    ttl_seconds: int
    ready_timeout: int
    create_timeout: int


class SandboxProvider(ABC):
    """Lifecycle controller for one sandbox backend."""

    @abstractmethod
    async def create(self, req: SandboxCreateRequest) -> Sandbox:
        """Create a sandbox and return a ready `Sandbox` driver."""
        ...

    @abstractmethod
    async def connect(self, sandbox_id: str, *, workdir: str) -> Sandbox | None:
        """Reconnect to a running sandbox; return a live driver or None if
        unreachable/unhealthy (the portable health signal)."""
        ...

    @abstractmethod
    async def kill(self, sandbox_id: str) -> None:
        """Terminate a sandbox by id. Idempotent: a missing sandbox is not an error."""
        ...

    @abstractmethod
    async def set_lifetime(self, sandbox: Sandbox, seconds: int) -> None:
        """Extend the sandbox's remaining lifetime. No-op where unsupported."""
        ...
```

**Run:**

```bash
cd /home/chris/cubebox/.worktrees/feat/sandbox-e2b-backend/backend && \
  uv run pytest tests/unit/test_sandbox_provider_types.py -q
```

Expected: `2 passed`.

---

## Task 2 — `OpenSandboxProvider`: move lifecycle off the manager (test-first)

Implement the seam for OpenSandbox by lifting the create/connect/kill logic out
of `SandboxManager`. PVC building moves here. Behavior must match today.

**Test file:** `backend/tests/unit/test_opensandbox_provider.py`

```python
"""Unit: OpenSandboxProvider maps neutral args → opensandbox SDK and PVC."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cubebox.sandbox.opensandbox_provider import OpenSandboxProvider
from cubebox.sandbox.provider import (
    SandboxCreateRequest,
    SandboxNetwork,
    SandboxResource,
    SandboxVolume,
)


def _req(volumes: list[SandboxVolume]) -> SandboxCreateRequest:
    return SandboxCreateRequest(
        image="ubuntu:22.04",
        workdir="/workspace",
        resource=SandboxResource(cpu="1", memory="1Gi"),
        network=SandboxNetwork(opensandbox_policy="POLICY"),
        volumes=volumes,
        ttl_seconds=1800,
        ready_timeout=300,
        create_timeout=300,
    )


@pytest.mark.asyncio
async def test_create_maps_volumes_resource_policy() -> None:
    provider = OpenSandboxProvider(
        domain="d", api_key=None, use_server_proxy=False, request_timeout=60
    )
    raw = MagicMock()
    raw.id = "sbx-1"
    with (
        patch("cubebox.sandbox.opensandbox_provider.opensandbox.Sandbox.create",
              new=AsyncMock(return_value=raw)) as create,
        patch("cubebox.sandbox.opensandbox_provider.opensandbox.Sandbox.connect",
              new=AsyncMock(return_value=raw)),
    ):
        vol = SandboxVolume(name="user-workspace", claim_name="cubebox-user-abc",
                            mount_path="/workspace")
        sandbox = await provider.create(_req([vol]))
    kwargs = create.call_args.kwargs
    assert kwargs["resource"] == {"cpu": "1", "memory": "1Gi"}
    assert kwargs["network_policy"] == "POLICY"
    assert kwargs["volumes"][0].pvc.claim_name == "cubebox-user-abc"
    assert sandbox.id == "sbx-1"


@pytest.mark.asyncio
async def test_connect_returns_none_when_unhealthy() -> None:
    provider = OpenSandboxProvider(
        domain="d", api_key=None, use_server_proxy=False, request_timeout=60
    )
    raw = MagicMock()
    raw.is_healthy = AsyncMock(return_value=False)
    with patch("cubebox.sandbox.opensandbox_provider.opensandbox.Sandbox.connect",
               new=AsyncMock(return_value=raw)):
        result = await provider.connect("sbx-x", workdir="/workspace")
    assert result is None


@pytest.mark.asyncio
async def test_connect_returns_driver_when_healthy() -> None:
    provider = OpenSandboxProvider(
        domain="d", api_key=None, use_server_proxy=False, request_timeout=60
    )
    raw = MagicMock()
    raw.is_healthy = AsyncMock(return_value=True)
    with patch("cubebox.sandbox.opensandbox_provider.opensandbox.Sandbox.connect",
               new=AsyncMock(return_value=raw)):
        result = await provider.connect("sbx-x", workdir="/workspace")
    assert result is not None
    assert result.workdir == "/workspace"
```

**Impl file:** `backend/cubebox/sandbox/opensandbox_provider.py`

```python
"""OpenSandboxProvider — lifecycle seam impl backed by the opensandbox SDK."""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta

import opensandbox
from opensandbox.config import ConnectionConfig
from opensandbox.exceptions import SandboxException as ProviderSandboxError
from opensandbox.models.sandboxes import PVC, Volume

from cubebox.sandbox.base import Sandbox, SandboxError
from cubebox.sandbox.opensandbox import OpenSandbox
from cubebox.sandbox.provider import SandboxCreateRequest, SandboxProvider, SandboxVolume


class OpenSandboxProvider(SandboxProvider):
    """Create/connect/kill via the opensandbox SDK; supports PVC volumes."""

    def __init__(
        self, *, domain: str, api_key: str | None, use_server_proxy: bool, request_timeout: int
    ) -> None:
        self._domain = domain
        self._api_key = api_key
        self._use_server_proxy = use_server_proxy
        self._request_timeout = request_timeout

    def _conn(self, *, request_timeout: int | None = None) -> ConnectionConfig:
        return ConnectionConfig(
            domain=self._domain,
            api_key=self._api_key,
            request_timeout=timedelta(seconds=request_timeout or self._request_timeout),
            use_server_proxy=self._use_server_proxy,
        )

    @staticmethod
    def _to_volume(v: SandboxVolume) -> Volume:
        return Volume(
            name=v.name, pvc=PVC(claimName=v.claim_name),
            mountPath=v.mount_path, readOnly=v.read_only,
        )

    @staticmethod
    def build_volume_name(user_id: str, prefix: str) -> str:
        """PVC claim name for a user (was SandboxManager._build_user_volume)."""
        sanitized = re.sub(r"[^a-z0-9-]+", "-", user_id.lower()).strip("-")
        if not sanitized:
            sanitized = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]
        max_suffix_len = 63 - len(prefix) - 1
        if len(sanitized) > max_suffix_len:
            sanitized = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]
        return f"{prefix}-{sanitized}"

    async def create(self, req: SandboxCreateRequest) -> Sandbox:
        volumes = [self._to_volume(v) for v in req.volumes] or None
        create_conn = self._conn(request_timeout=req.create_timeout)
        try:
            raw = await opensandbox.Sandbox.create(
                req.image,
                connection_config=create_conn,
                timeout=None,
                ready_timeout=timedelta(seconds=req.ready_timeout),
                volumes=volumes,
                resource={"cpu": req.resource.cpu, "memory": req.resource.memory},
                secure_access=True,
                network_policy=req.network.opensandbox_policy,
            )
            sandbox_id = raw.id
            # Rebind to the default per-command timeout (was inline in the manager).
            raw = await opensandbox.Sandbox.connect(
                sandbox_id, connection_config=self._conn(), skip_health_check=True
            )
        except ProviderSandboxError as exc:
            raise SandboxError(str(exc)) from exc
        return OpenSandbox(sandbox=raw, workdir=req.workdir)

    async def connect(self, sandbox_id: str, *, workdir: str) -> Sandbox | None:
        try:
            raw = await opensandbox.Sandbox.connect(
                sandbox_id, connection_config=self._conn()
            )
            if not await raw.is_healthy():
                return None
        except Exception:
            return None
        return OpenSandbox(sandbox=raw, workdir=workdir)

    async def kill(self, sandbox_id: str) -> None:
        try:
            raw = await opensandbox.Sandbox.connect(
                sandbox_id, connection_config=self._conn(), skip_health_check=True
            )
            await raw.kill()
            await raw.close()
        except Exception:
            pass  # already gone is fine

    async def set_lifetime(self, sandbox: Sandbox, seconds: int) -> None:
        # OpenSandbox lifetime is controlled by cubebox TTL + cleanup task; no
        # provider-side extension call exists. No-op (matches today's behavior).
        return
```

**Run:**

```bash
cd /home/chris/cubebox/.worktrees/feat/sandbox-e2b-backend/backend && \
  uv run pytest tests/unit/test_opensandbox_provider.py -q
```

Expected: `3 passed`.

---

## Task 3 — Refactor `SandboxManager` to call the provider (test-first)

Make the manager provider-neutral: it takes a `SandboxProvider`, and replaces
every direct `opensandbox.Sandbox.*` call + PVC build with seam calls. Egress
logic, touch cache, repo writes, TTL stay exactly as today.

**Test file:** `backend/tests/unit/test_sandbox_manager_provider.py`

```python
"""Unit: SandboxManager drives a SandboxProvider (no opensandbox import)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from cubebox.sandbox.manager import SandboxManager
from cubebox.sandbox.provider import SandboxCreateRequest


@pytest.mark.asyncio
async def test_get_or_create_no_record_calls_provider_create(monkeypatch) -> None:
    provider = MagicMock()
    created = MagicMock()
    created.id = "sbx-new"
    provider.create = AsyncMock(return_value=created)
    provider.connect = AsyncMock(return_value=None)

    # Repo returns no active record → create path.
    repo = MagicMock()
    repo.get_active_by_user = AsyncMock(return_value=None)
    repo.create = AsyncMock()
    monkeypatch.setattr(
        "cubebox.sandbox.manager.UserSandboxRepository", lambda *a, **k: repo
    )

    mgr = SandboxManager(session_factory=_fake_sf(), provider=provider)
    result = await mgr.get_or_create("u1", org_id="o1", workspace_id="w1")

    assert result is created
    provider.create.assert_awaited_once()
    req = provider.create.call_args.args[0]
    assert isinstance(req, SandboxCreateRequest)
    repo.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_reuse_path_uses_provider_connect(monkeypatch) -> None:
    provider = MagicMock()
    live = MagicMock()
    live.id = "sbx-old"
    provider.connect = AsyncMock(return_value=live)
    provider.create = AsyncMock()

    record = MagicMock()
    record.id = "row1"
    record.sandbox_id = "sbx-old"
    repo = MagicMock()
    repo.get_active_by_user = AsyncMock(return_value=record)
    repo.update_activity = AsyncMock()
    monkeypatch.setattr(
        "cubebox.sandbox.manager.UserSandboxRepository", lambda *a, **k: repo
    )

    mgr = SandboxManager(session_factory=_fake_sf(), provider=provider)
    result = await mgr.get_or_create("u1", org_id="o1", workspace_id="w1")

    assert result is live
    provider.connect.assert_awaited_once_with("sbx-old", workdir=mgr._workdir)
    provider.create.assert_not_awaited()
    repo.update_activity.assert_awaited_once()


def _fake_sf():
    """Async-context session factory whose session is a MagicMock."""
    session = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return lambda: cm
```

**Impl — edit `backend/cubebox/sandbox/manager.py`:**

- Remove the module imports `import opensandbox`, `ConnectionConfig`, `PVC`,
  `Volume`, `SandboxException`, and the `OpenSandbox` import. Keep `SandboxError`.
- `__init__(self, session_factory, *, provider: SandboxProvider)`: store
  `self._provider = provider`; keep reading the cross-provider config
  (`ttl`, `touch_interval`, `cleanup_interval`, `workdir`, `egress_exchange_host`)
  plus image/resource/volume **for building the neutral request**.
- Delete `_build_connection_config` and `_build_user_volume`.
- `get_or_create`: replace the reuse block's `opensandbox.Sandbox.connect` +
  `is_healthy()` + `OpenSandbox(...)` with:

```python
        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
            record = await repo.get_active_by_user(user_id)

            if record:
                backend = await self._provider.connect(
                    record.sandbox_id, workdir=self._workdir
                )
                if backend is not None:
                    await repo.update_activity(record.id)
                    if self._exchange_host:
                        await self._apply_egress(
                            session, backend, org_id=org_id, workspace_id=workspace_id,
                            user_id=user_id, sandbox_id=record.sandbox_id,
                        )
                    return backend
                await repo.mark_terminated(record.id)
                if self._exchange_host:
                    await EgressRefRepository(session).revoke_for_sandbox(record.sandbox_id)

            req = self._build_create_request(session, user_id, org_id, workspace_id)
            # _build_create_request resolves egress (network_policy) when enabled.
            req, injection = await self._prepare_create(session, user_id, org_id, workspace_id)
            backend = await self._provider.create(req)
            await repo.create(
                user_id=user_id, sandbox_id=backend.id,
                image=self._image, ttl_seconds=self._ttl,
            )
            if self._exchange_host:
                await self._apply_egress(
                    session, backend, org_id=org_id, workspace_id=workspace_id,
                    user_id=user_id, sandbox_id=backend.id,
                )
            return backend
```

  Replace the two-line `req`/`injection` sketch above with a single helper
  `_prepare_create(...) -> tuple[SandboxCreateRequest, injection | None]` that:
  - resolves egress injection when `self._exchange_host` (same
    `SandboxEnvResolver`/`SandboxEnvInjector` calls as today) and pulls
    `injection.network_policy`;
  - builds `volumes` from `self._volume_enabled` using
    `OpenSandboxProvider.build_volume_name(user_id, self._volume_pvc_prefix)`
    (the PVC claim name) — wrapped in a `SandboxVolume`;
  - returns a `SandboxCreateRequest(image=self._image, workdir=self._workdir,
    resource=SandboxResource(cpu, memory), network=SandboxNetwork(
    opensandbox_policy=injection.network_policy if injection else None),
    volumes=volumes, ttl_seconds=self._ttl, ready_timeout=self._ready_timeout,
    create_timeout=self._create_timeout)`.

  > Note on the volume name: it intentionally still uses the OpenSandbox PVC
  > naming. e2b ignores `volumes`, so passing it is harmless; the seam stays
  > provider-neutral.

- `cleanup_expired`: replace the inline connect+kill+close with
  `await self._provider.kill(record.sandbox_id)`.
- `release`, `touch`, `touch_active` are unchanged (DB-only).
- `init_sandbox_manager(session_factory)` → build the provider via the factory
  (Task 5) and pass it in. Signature stays the same for callers.

**Run:**

```bash
cd /home/chris/cubebox/.worktrees/feat/sandbox-e2b-backend/backend && \
  uv run pytest tests/unit/test_sandbox_manager_provider.py tests/unit/test_sandbox_manager.py -q
```

Expected: all pass. If `test_sandbox_manager.py` constructed the manager
without a provider, update it to pass a `MagicMock()` provider — that is the
intended interface change, not a regression.

---

## Task 4 — `E2BSandbox` driver: execute / files / env / errors (test-first)

The per-instance driver. Mock the e2b SDK client at the driver boundary (unit
mapping coverage, not a fake-server E2E).

**Test file:** `backend/tests/unit/test_e2b_sandbox.py`

```python
"""Unit: E2BSandbox maps the Sandbox interface onto the e2b SDK handle."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from cubebox.sandbox.base import SandboxError
from cubebox.sandbox.e2b import E2BSandbox


def _handle() -> MagicMock:
    h = MagicMock()
    h.sandbox_id = "e2b-1"
    h.commands.run = AsyncMock()
    h.files.write = AsyncMock()
    h.files.read = AsyncMock()
    h.set_timeout = AsyncMock()
    return h


@pytest.mark.asyncio
async def test_execute_combines_stdout_stderr_and_exit_code() -> None:
    h = _handle()
    res = MagicMock(stdout="out\n", stderr="err\n", exit_code=0)
    h.commands.run.return_value = res
    sbx = E2BSandbox(handle=h, workdir="/workspace")

    out = await sbx.execute("echo hi")

    assert out.output == "out\nerr"
    assert out.exit_code == 0
    kwargs = h.commands.run.call_args.kwargs
    assert kwargs["cwd"] == "/workspace"


@pytest.mark.asyncio
async def test_execute_merges_env_per_call_wins() -> None:
    h = _handle()
    h.commands.run.return_value = MagicMock(stdout="", stderr="", exit_code=0)
    sbx = E2BSandbox(handle=h, workdir="/workspace")
    sbx.set_run_env({"BASE": "b", "OVERRIDE": "run"})

    await sbx.execute("x", envs={"OVERRIDE": "call", "EXTRA": "e"})

    envs = h.commands.run.call_args.kwargs["envs"]
    assert envs == {"BASE": "b", "OVERRIDE": "call", "EXTRA": "e"}


@pytest.mark.asyncio
async def test_upload_writes_each_file() -> None:
    h = _handle()
    sbx = E2BSandbox(handle=h, workdir="/workspace")
    await sbx.upload([("/workspace/a.txt", b"A"), ("/workspace/b.txt", b"B")])
    assert h.files.write.await_count == 2
    assert h.files.write.await_args_list[0].args == ("/workspace/a.txt", b"A")


@pytest.mark.asyncio
async def test_download_reads_bytes() -> None:
    h = _handle()
    h.files.read.return_value = b"DATA"
    sbx = E2BSandbox(handle=h, workdir="/workspace")
    out = await sbx.download(["/workspace/a.txt"])
    assert out == [("/workspace/a.txt", b"DATA")]
    assert h.files.read.call_args.kwargs["format"] == "bytes"


@pytest.mark.asyncio
async def test_download_missing_maps_to_filenotfound() -> None:
    h = _handle()
    h.files.read.side_effect = FileNotFoundError("nope")
    sbx = E2BSandbox(handle=h, workdir="/workspace")
    with pytest.raises(FileNotFoundError):
        await sbx.download(["/workspace/missing.txt"])


@pytest.mark.asyncio
async def test_provider_error_translated_to_sandbox_error() -> None:
    h = _handle()

    class _E2BErr(Exception):
        pass

    h.commands.run.side_effect = _E2BErr("boom")
    sbx = E2BSandbox(handle=h, workdir="/workspace", error_types=(_E2BErr,))
    with pytest.raises(SandboxError):
        await sbx.execute("x")
```

**Impl file:** `backend/cubebox/sandbox/e2b.py`

```python
"""E2BSandbox — the `Sandbox` driver backed by an e2b AsyncSandbox handle."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from cubebox.sandbox.base import ExecuteResult, Sandbox, SandboxError


class E2BSandbox(Sandbox):
    """Wraps a live e2b ``AsyncSandbox`` handle."""

    def __init__(
        self,
        *,
        handle: object,
        workdir: str = "/workspace",
        error_types: tuple[type[Exception], ...] | None = None,
    ) -> None:
        self._handle = handle
        self._workdir = workdir
        self._run_env: dict[str, str] = {}
        # e2b raises its own exception classes; resolve them lazily so importing
        # this module never hard-requires the SDK at type-check time.
        if error_types is None:
            try:
                from e2b.exceptions import SandboxException as _E2BErr

                error_types = (_E2BErr,)
            except Exception:
                error_types = ()
        self._error_types = error_types

    @contextmanager
    def _as_sandbox_error(self) -> Iterator[None]:
        try:
            yield
        except FileNotFoundError:
            raise
        except self._error_types as exc:  # type: ignore[misc]
            raise SandboxError(str(exc)) from exc

    @property
    def id(self) -> str:
        return self._handle.sandbox_id  # type: ignore[attr-defined]

    @property
    def workdir(self) -> str:
        return self._workdir

    def set_run_env(self, env: dict[str, str]) -> None:
        self._run_env = env

    async def execute(
        self, command: str, *, timeout: int | None = None, envs: dict[str, str] | None = None
    ) -> ExecuteResult:
        merged = {**self._run_env, **(envs or {})}
        with self._as_sandbox_error():
            res = await self._handle.commands.run(  # type: ignore[attr-defined]
                command,
                cwd=self._workdir,
                envs=merged or None,
                timeout=timeout,
            )
        parts = [p for p in (res.stdout, res.stderr) if p]
        output = "\n".join(p.rstrip("\n") for p in parts)
        return ExecuteResult(output=output, exit_code=res.exit_code)

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        with self._as_sandbox_error():
            for path, content in files:
                await self._handle.files.write(path, content)  # type: ignore[attr-defined]

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        with self._as_sandbox_error():
            result: list[tuple[str, bytes]] = []
            for path in paths:
                try:
                    content = await self._handle.files.read(  # type: ignore[attr-defined]
                        path, format="bytes"
                    )
                except FileNotFoundError:
                    raise
                except Exception as exc:
                    if "not found" in str(exc).lower() or "404" in str(exc):
                        raise FileNotFoundError(path) from exc
                    raise
                result.append((path, content))
            return result

    async def close(self) -> None:
        # Lifetime is owned by the cleanup task (provider.kill), like OpenSandbox.
        return
```

> `get_browser_endpoint` is **not** overridden — the base default raises
> `NotImplementedError`, which is exactly the "browser unavailable on e2b until
> a custom Neko template exists" behavior the spec requires for v1.

**Run:**

```bash
cd /home/chris/cubebox/.worktrees/feat/sandbox-e2b-backend/backend && \
  uv run pytest tests/unit/test_e2b_sandbox.py -q
```

Expected: `6 passed`.

---

## Task 5 — `E2BProvider`: create/connect/kill/set_lifetime + network mapping (test-first)

The lifecycle seam for e2b. This is where `allow_internet_access` (top-level)
and `network` (separate) are mapped — the spec's central correctness point.

**Test file:** `backend/tests/unit/test_e2b_provider.py`

```python
"""Unit: E2BProvider maps neutral args → e2b Sandbox.create kwargs."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cubebox.sandbox.e2b_provider import E2BProvider
from cubebox.sandbox.provider import (
    SandboxCreateRequest,
    SandboxNetwork,
    SandboxResource,
    SandboxVolume,
)


def _req(network: SandboxNetwork, volumes=None) -> SandboxCreateRequest:
    return SandboxCreateRequest(
        image="my-template",
        workdir="/workspace",
        resource=SandboxResource(cpu="1", memory="1Gi"),
        network=network,
        volumes=volumes or [],
        ttl_seconds=1800,
        ready_timeout=300,
        create_timeout=300,
    )


@pytest.mark.asyncio
async def test_create_internet_off_top_level_kwarg() -> None:
    provider = E2BProvider(api_key="k", timeout_ceiling=3600)
    handle = MagicMock()
    handle.sandbox_id = "e2b-1"
    with patch("cubebox.sandbox.e2b_provider.AsyncSandbox.create",
               new=AsyncMock(return_value=handle)) as create:
        await provider.create(_req(SandboxNetwork(allow_internet=False)))
    kwargs = create.call_args.kwargs
    # internet switch is a TOP-LEVEL kwarg, never inside `network`.
    assert kwargs["allow_internet_access"] is False
    assert "allow_internet_access" not in (kwargs.get("network") or {})


@pytest.mark.asyncio
async def test_create_network_rules_under_network_kwarg() -> None:
    provider = E2BProvider(api_key="k", timeout_ceiling=3600)
    handle = MagicMock()
    handle.sandbox_id = "e2b-1"
    net = SandboxNetwork(
        allow_internet=True, allow_out=["*.github.com"], deny_out=["10.0.0.0/8"],
        allow_public_traffic=False,
    )
    with patch("cubebox.sandbox.e2b_provider.AsyncSandbox.create",
               new=AsyncMock(return_value=handle)) as create:
        await provider.create(_req(net))
    kwargs = create.call_args.kwargs
    assert kwargs["allow_internet_access"] is True
    assert kwargs["network"] == {
        "allow_out": ["*.github.com"],
        "deny_out": ["10.0.0.0/8"],
        "allow_public_traffic": False,
    }
    assert kwargs["template"] == "my-template"
    assert kwargs["timeout"] == 1800  # min(ttl, ceiling)


@pytest.mark.asyncio
async def test_create_ttl_capped_at_ceiling() -> None:
    provider = E2BProvider(api_key="k", timeout_ceiling=600)
    handle = MagicMock()
    handle.sandbox_id = "e2b-1"
    req = _req(SandboxNetwork())
    req.ttl_seconds = 5000
    with patch("cubebox.sandbox.e2b_provider.AsyncSandbox.create",
               new=AsyncMock(return_value=handle)) as create:
        await provider.create(req)
    assert create.call_args.kwargs["timeout"] == 600


@pytest.mark.asyncio
async def test_create_ignores_volumes() -> None:
    provider = E2BProvider(api_key="k", timeout_ceiling=3600)
    handle = MagicMock()
    handle.sandbox_id = "e2b-1"
    vol = SandboxVolume(name="v", claim_name="c", mount_path="/workspace")
    with patch("cubebox.sandbox.e2b_provider.AsyncSandbox.create",
               new=AsyncMock(return_value=handle)) as create:
        await provider.create(_req(SandboxNetwork(), volumes=[vol]))
    assert "volumes" not in create.call_args.kwargs


@pytest.mark.asyncio
async def test_connect_returns_driver() -> None:
    provider = E2BProvider(api_key="k", timeout_ceiling=3600)
    handle = MagicMock()
    handle.sandbox_id = "e2b-1"
    with patch("cubebox.sandbox.e2b_provider.AsyncSandbox.connect",
               new=AsyncMock(return_value=handle)):
        result = await provider.connect("e2b-1", workdir="/workspace")
    assert result is not None
    assert result.id == "e2b-1"


@pytest.mark.asyncio
async def test_connect_failure_returns_none() -> None:
    provider = E2BProvider(api_key="k", timeout_ceiling=3600)
    with patch("cubebox.sandbox.e2b_provider.AsyncSandbox.connect",
               new=AsyncMock(side_effect=RuntimeError("gone"))):
        result = await provider.connect("e2b-x", workdir="/workspace")
    assert result is None
```

**Impl file:** `backend/cubebox/sandbox/e2b_provider.py`

```python
"""E2BProvider — sandbox lifecycle seam backed by the e2b SDK."""

from __future__ import annotations

from e2b import AsyncSandbox

from cubebox.sandbox.base import Sandbox, SandboxError
from cubebox.sandbox.e2b import E2BSandbox
from cubebox.sandbox.provider import SandboxCreateRequest, SandboxNetwork, SandboxProvider


class E2BProvider(SandboxProvider):
    """Create/connect/kill via the e2b SDK.

    Two distinct create kwargs (see spec): ``allow_internet_access`` is the
    TOP-LEVEL master internet switch; ``network`` is the separate fine-grained
    allow/deny + public-traffic option. ``allow_internet_access`` must NOT be
    nested under ``network`` — doing so would silently fail to block egress.
    e2b has no PVC, so ``volumes`` is ignored.
    """

    def __init__(self, *, api_key: str, timeout_ceiling: int) -> None:
        self._api_key = api_key
        self._timeout_ceiling = timeout_ceiling

    @staticmethod
    def _network_kwarg(net: SandboxNetwork) -> dict[str, object] | None:
        """Map neutral rules to the e2b `network` option (NOT the internet switch)."""
        if not net.allow_out and not net.deny_out and net.allow_public_traffic:
            return None  # nothing to restrict; default behavior
        return {
            "allow_out": list(net.allow_out),
            "deny_out": list(net.deny_out),
            "allow_public_traffic": net.allow_public_traffic,
        }

    async def create(self, req: SandboxCreateRequest) -> Sandbox:
        network = self._network_kwarg(req.network)
        timeout = min(req.ttl_seconds, self._timeout_ceiling)
        kwargs: dict[str, object] = {
            "template": req.image,
            "timeout": timeout,
            "api_key": self._api_key,
            "allow_internet_access": req.network.allow_internet,
        }
        if network is not None:
            kwargs["network"] = network
        try:
            handle = await AsyncSandbox.create(**kwargs)
        except Exception as exc:
            raise SandboxError(str(exc)) from exc
        return E2BSandbox(handle=handle, workdir=req.workdir)

    async def connect(self, sandbox_id: str, *, workdir: str) -> Sandbox | None:
        try:
            handle = await AsyncSandbox.connect(sandbox_id, api_key=self._api_key)
        except Exception:
            return None  # unreachable/unhealthy → portable health signal
        return E2BSandbox(handle=handle, workdir=workdir)

    async def kill(self, sandbox_id: str) -> None:
        try:
            await AsyncSandbox.kill(sandbox_id, api_key=self._api_key)
        except Exception:
            pass  # already gone is fine

    async def set_lifetime(self, sandbox: Sandbox, seconds: int) -> None:
        capped = min(seconds, self._timeout_ceiling)
        handle = getattr(sandbox, "_handle", None)
        if handle is None:
            return
        try:
            await handle.set_timeout(capped)
        except Exception:
            pass
```

> If Task 0 recorded a different `create` signature (typed `SandboxNetworkOpts`
> instead of a dict, or a different `connect`/`kill` static-vs-instance shape),
> adapt the kwargs here and update the test expectations to match the installed
> SDK. The dict form above is the spec's documented shape.

**Run:**

```bash
cd /home/chris/cubebox/.worktrees/feat/sandbox-e2b-backend/backend && \
  uv run pytest tests/unit/test_e2b_provider.py -q
```

Expected: `6 passed`.

---

## Task 6 — Factory + config key + vault-backed API key + per-run injection (test-first)

`build_sandbox_provider(config)` reads `sandbox.provider` and returns the right
provider. For e2b it resolves the API key from the credential vault (system
scope, `org_id=NULL`), falling back to `sandbox.e2b.api_key` (env) for local dev.

**Test file:** `backend/tests/unit/test_sandbox_provider_factory.py`

```python
"""Unit: build_sandbox_provider selects provider by config and resolves the key."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from cubebox.sandbox.e2b_provider import E2BProvider
from cubebox.sandbox.factory import build_sandbox_provider
from cubebox.sandbox.opensandbox_provider import OpenSandboxProvider


def _cfg(values: dict[str, object]):
    cfg = MagicMock()
    cfg.get = lambda key, default=None: values.get(key, default)
    return cfg


@pytest.mark.asyncio
async def test_default_provider_is_opensandbox() -> None:
    cfg = _cfg({"sandbox.provider": "opensandbox", "sandbox.domain": "d"})
    provider = await build_sandbox_provider(cfg, resolve_e2b_key=AsyncMock())
    assert isinstance(provider, OpenSandboxProvider)


@pytest.mark.asyncio
async def test_e2b_provider_selected_and_key_resolved_from_vault() -> None:
    cfg = _cfg(
        {
            "sandbox.provider": "e2b",
            "sandbox.e2b.api_key": "env-fallback",
            "sandbox.e2b.timeout_ceiling": 3600,
        }
    )
    resolve = AsyncMock(return_value="vault-key")
    provider = await build_sandbox_provider(cfg, resolve_e2b_key=resolve)
    assert isinstance(provider, E2BProvider)
    assert provider._api_key == "vault-key"
    resolve.assert_awaited_once()


@pytest.mark.asyncio
async def test_e2b_falls_back_to_env_key_when_vault_empty() -> None:
    cfg = _cfg({"sandbox.provider": "e2b", "sandbox.e2b.api_key": "env-fallback"})
    resolve = AsyncMock(return_value=None)
    provider = await build_sandbox_provider(cfg, resolve_e2b_key=resolve)
    assert provider._api_key == "env-fallback"


@pytest.mark.asyncio
async def test_unknown_provider_raises() -> None:
    cfg = _cfg({"sandbox.provider": "nope"})
    with pytest.raises(ValueError):
        await build_sandbox_provider(cfg, resolve_e2b_key=AsyncMock())
```

**Impl file:** `backend/cubebox/sandbox/factory.py`

```python
"""Factory: select the active SandboxProvider from config."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from cubebox.sandbox.e2b_provider import E2BProvider
from cubebox.sandbox.opensandbox_provider import OpenSandboxProvider
from cubebox.sandbox.provider import SandboxProvider

# Vault credential kind + name for the e2b API key (system scope, org_id NULL).
E2B_CREDENTIAL_KIND = "sandbox_provider"
E2B_CREDENTIAL_NAME = "e2b"


async def build_sandbox_provider(
    config: object,
    *,
    resolve_e2b_key: Callable[[], Awaitable[str | None]],
) -> SandboxProvider:
    """Return the provider named by ``sandbox.provider`` (default opensandbox).

    ``resolve_e2b_key`` reads the e2b API key from the credential vault (system
    scope). When it returns None, fall back to the ``sandbox.e2b.api_key`` env
    config for local dev, consistent with how OpenSandbox reads its key today.
    """
    name = config.get("sandbox.provider", "opensandbox")  # type: ignore[attr-defined]
    if name == "opensandbox":
        return OpenSandboxProvider(
            domain=config.get("sandbox.domain", "localhost:8090"),  # type: ignore[attr-defined]
            api_key=config.get("sandbox.api_key", None),  # type: ignore[attr-defined]
            use_server_proxy=config.get("sandbox.use_server_proxy", False),  # type: ignore[attr-defined]
            request_timeout=config.get("sandbox.request_timeout", 60),  # type: ignore[attr-defined]
        )
    if name == "e2b":
        key = await resolve_e2b_key()
        if not key:
            key = config.get("sandbox.e2b.api_key", None)  # type: ignore[attr-defined]
        if not key:
            raise ValueError(
                "sandbox.provider=e2b but no e2b API key in the vault or sandbox.e2b.api_key"
            )
        return E2BProvider(
            api_key=key,
            timeout_ceiling=config.get("sandbox.e2b.timeout_ceiling", 3600),  # type: ignore[attr-defined]
        )
    raise ValueError(f"unknown sandbox.provider: {name!r}")
```

**Wire `init_sandbox_manager`** (edit `manager.py`): make it `async`, build the
provider via the factory, pass it to `SandboxManager`. The vault resolver reads
a system (`org_id=None`) credential by `(kind, name)`:

```python
async def init_sandbox_manager(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    encryption_backend: EncryptionBackend,
) -> SandboxManager:
    from cubebox.config import config
    from cubebox.repositories.credential import CredentialRepository
    from cubebox.sandbox.factory import (
        E2B_CREDENTIAL_KIND,
        E2B_CREDENTIAL_NAME,
        build_sandbox_provider,
    )

    async def _resolve_e2b_key() -> str | None:
        async with session_factory() as session:
            repo = CredentialRepository(session, org_id=None)
            cred = await repo.get_by_kind_name(
                kind=E2B_CREDENTIAL_KIND, name=E2B_CREDENTIAL_NAME
            )
            if cred is None:
                return None
            plaintext = await encryption_backend.decrypt(cred.value_encrypted)
            return plaintext.decode("utf-8")

    provider = await build_sandbox_provider(config, resolve_e2b_key=_resolve_e2b_key)
    global _sandbox_manager
    _sandbox_manager = SandboxManager(session_factory, provider=provider)
    return _sandbox_manager
```

**Edit `backend/cubebox/api/app.py`** (the init block, ~line 204):

```python
            manager = await init_sandbox_manager(
                async_session_maker,
                encryption_backend=_app.state.encryption_backend,
            )
```

**Run:**

```bash
cd /home/chris/cubebox/.worktrees/feat/sandbox-e2b-backend/backend && \
  uv run pytest tests/unit/test_sandbox_provider_factory.py -q
```

Expected: `4 passed`.

---

## Task 7 — Config: add `sandbox.provider` + `sandbox.e2b.*`

**Files:** `backend/config.yaml`, `backend/config.development.yaml`,
`backend/config.production.yaml`, `backend/config.test.yaml` (only those that
carry a `sandbox:` block — check each before editing).

Add under the `sandbox:` block in `config.yaml` (defaults preserve OpenSandbox):

```yaml
  sandbox:
    enabled: true
    # provider: which backend lifecycle to use. opensandbox (default) or e2b.
    provider: "opensandbox"
    # ... existing opensandbox-shaped keys stay here unchanged ...
    # e2b backend settings (used only when provider: e2b). The API key is read
    # from the credential vault (system scope, kind=sandbox_provider, name=e2b);
    # sandbox.e2b.api_key is a local-dev env fallback only.
    e2b:
      api_key: "@format {env[CUBEBOX_SANDBOX__E2B__API_KEY]}"
      # timeout_ceiling: e2b plan max sandbox lifetime (s). cubebox TTL is capped
      # at this when creating an e2b sandbox (1h Hobby = 3600, 24h Pro = 86400).
      timeout_ceiling: 3600
```

`sandbox.e2b.template` is **not** added in v1: the spec defers the custom Neko
template, and `sandbox.image` already supplies the template name to
`SandboxCreateRequest.image`. (When a real e2b template lands with #145's
browser work, add `sandbox.e2b.template` then.)

**Run (config loads + default provider builds):**

```bash
cd /home/chris/cubebox/.worktrees/feat/sandbox-e2b-backend/backend && \
  uv run python -c "from cubebox.config import config; print('provider =', config.get('sandbox.provider', 'opensandbox'))"
```

Expected: `provider = opensandbox`.

---

## Task 8 — Opt-in real-e2b E2E (gated on `E2B_API_KEY`, NOT a fake server)

A single marked test that hits the real e2b service. Skipped by default; never
blocks the suite. No fake server (per the "no fake E2E for unsimulatable
systems" rule).

**Register the marker** in `backend/pyproject.toml` under
`[tool.pytest.ini_options] markers` (via editing the existing markers list):
add `"e2b: opt-in tests that hit the real e2b service (need E2B_API_KEY)"`.

**Test file:** `backend/tests/e2e/test_e2b_live.py`

```python
"""Opt-in E2E against the real e2b service. Skipped unless E2B_API_KEY is set.

Run with:  E2B_API_KEY=... uv run pytest -m e2b tests/e2e/test_e2b_live.py
"""

from __future__ import annotations

import os

import pytest

from cubebox.sandbox.e2b_provider import E2BProvider
from cubebox.sandbox.provider import (
    SandboxCreateRequest,
    SandboxNetwork,
    SandboxResource,
)

pytestmark = [
    pytest.mark.e2b,
    pytest.mark.skipif(
        not os.environ.get("E2B_API_KEY"), reason="E2B_API_KEY not set; opt-in only"
    ),
]


@pytest.mark.asyncio
async def test_e2b_create_execute_files_kill() -> None:
    provider = E2BProvider(
        api_key=os.environ["E2B_API_KEY"],
        timeout_ceiling=int(os.environ.get("E2B_TEMPLATE_CEILING", "3600")),
    )
    template = os.environ.get("E2B_TEMPLATE", "base")
    req = SandboxCreateRequest(
        image=template,
        workdir="/home/user",
        resource=SandboxResource(cpu="1", memory="1Gi"),
        network=SandboxNetwork(),
        volumes=[],
        ttl_seconds=120,
        ready_timeout=60,
        create_timeout=120,
    )
    sandbox = await provider.create(req)
    sandbox_id = sandbox.id
    try:
        result = await sandbox.execute("echo hello-e2b")
        assert "hello-e2b" in result.output
        assert result.exit_code == 0

        await sandbox.upload([("/home/user/note.txt", b"persisted")])
        downloaded = await sandbox.download(["/home/user/note.txt"])
        assert downloaded == [("/home/user/note.txt", b"persisted")]

        with pytest.raises(FileNotFoundError):
            await sandbox.download(["/home/user/missing.txt"])
    finally:
        await provider.kill(sandbox_id)
```

**Run (default suite — should SKIP):**

```bash
cd /home/chris/cubebox/.worktrees/feat/sandbox-e2b-backend/backend && \
  uv run pytest tests/e2e/test_e2b_live.py -q
```

Expected: `1 skipped` (no key present).

**Run (opt-in, only when a real key is available — operator step, not CI default):**

```bash
E2B_API_KEY=<real-key> uv run pytest -m e2b tests/e2e/test_e2b_live.py -q
```

Expected: `1 passed`.

---

## Task 9 — Full sweep: pre-PR verification

**OpenSandbox regression safety net** (the seam must not change default-provider
behavior) + the new unit suites + types.

```bash
cd /home/chris/cubebox/.worktrees/feat/sandbox-e2b-backend/backend && \
  uv run pytest tests/unit -q -k "sandbox or e2b or opensandbox" && \
  uv run mypy cubebox/sandbox && \
  uv run ruff check cubebox/sandbox tests/unit/test_e2b_sandbox.py \
    tests/unit/test_e2b_provider.py tests/unit/test_sandbox_provider_factory.py \
    tests/unit/test_opensandbox_provider.py tests/unit/test_sandbox_provider_types.py \
    tests/unit/test_sandbox_manager_provider.py
```

Expected: pytest reports the e2b E2E `skipped` and everything else `passed`;
mypy `Success: no issues found`; ruff `All checks passed!`.

If `backend/tests/e2e/test_opensandbox.py` requires a live OpenSandbox data
plane, run it only where that data plane exists (it stays the regression net for
the manager refactor); otherwise rely on `test_sandbox_manager*.py` +
`test_opensandbox_provider.py` for the seam-equivalence proof.

---

## Done criteria

- [ ] `e2b` added via `uv add`; import + `create` signature confirmed (Task 0).
- [ ] `SandboxProvider` ABC + neutral dataclasses exist; manager no longer
      imports `opensandbox` (greppable: `grep -n "import opensandbox" cubebox/sandbox/manager.py` → no hits).
- [ ] `OpenSandboxProvider`, `E2BProvider`, `E2BSandbox`, factory all unit-tested.
- [ ] `allow_internet_access` passed as a TOP-LEVEL e2b kwarg; `allow_out`/
      `deny_out`/`allow_public_traffic` under `network` — asserted by tests.
- [ ] e2b API key resolved from the vault (system scope) with env fallback.
- [ ] `sandbox.provider` defaults to `opensandbox`; nothing changes for existing
      deployments.
- [ ] e2b E2E is marked `@pytest.mark.e2b`, skipped without `E2B_API_KEY`,
      passes with a real key.
- [ ] mypy strict + ruff clean on `cubebox/sandbox`.

## Deferred (explicitly out of v1 — do NOT implement here)

- e2b custom Neko template → browser live view on e2b (`get_browser_endpoint`
  stays `NotImplementedError`).
- e2b pause/resume into #145's state machine (`beta_pause`/`auto_pause`).
- Egress-exchange secret swap on e2b (env injected directly; e2b is trusted).
- Per-run / per-workspace provider override (provider is process-global in v1).
- e2b `update_network()` on a running sandbox.
