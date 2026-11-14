# Multi-provider sandbox framework and CubePod integration

- **Status:** Draft
- **Date:** 2026-11-13
- **Branch / worktree:** `feat/2026-11-13-sandbox-provider-framework` / `.worktrees/feat/2026-11-13-sandbox-provider-framework`

## Goal

Decouple CubePlex's sandbox orchestration, routes, agent middleware, and panel
proxy from OpenSandbox internals by introducing a pluggable control-plane
`SandboxProvider` contract, a complete data-plane `Sandbox` interface, and a
9-dimension `SandboxCapabilities` degradation model so full-featured runtimes
(`OpenSandbox`, `CubePod`) and partial-capability runtimes (`LocalSandbox`,
future `E2B` / `Daytona` / plain `Docker`) run without provider branches or
runtime errors.

## Context — why this is changing

Today `Sandbox` (`backend/cubeplex/sandbox/base.py`) only abstracts a small
subset of an already-connected sandbox (`execute`, `start`, `poll`, `kill`,
`upload`, `download`, `pause`). Everything around that handle is wired directly
to `opensandbox` and assumes a full CubePlex-built Kubernetes/Docker environment:

1. **Control-plane coupling in `SandboxManager` (`sandbox/manager.py`, 2,042 lines).**
   `SandboxManager` mixes two separate jobs: CubePlex domain state (`UserSandbox`
   CAS transitions, `EgressRef` minting/renewal, in-use leases, activity touch
   throttling, idle/paused reapers) and OpenSandbox SDK calls
   (`ConnectionConfig`, `Volume`/`PVC`, `opensandbox.Sandbox.create`,
   `connect`, `resume`, `pause`, `kill`, `get_info`, `SandboxManager.create`,
   `ProviderApiError`). Adding a second backend inside `manager.py` duplicates
   every lifecycle method with `if provider == ...` branches.
2. **Private SDK access in HTTP routes (`SLF001`).**
   `Sandbox` has no directory-listing, bounded text-read, or byte-stream read
   methods. Three route modules import `OpenSandbox` directly, guard with
   `isinstance(sandbox, OpenSandbox)`, and reach into `sandbox._sandbox.files`:
   - `backend/cubeplex/api/routes/v1/ws_sandbox.py` (`/files`, `/files/content`,
     `/files/download`)
   - `backend/cubeplex/api/routes/v1/background_tasks.py`
     (`/background-tasks/{task_id}/output`, plus manual
     `sandbox._sandbox.close` teardown because `OpenSandbox.close()` is a no-op)
   - `backend/cubeplex/api/routes/v1/sandbox_share.py`
     (`/public/sandbox/dl/{nonce}/{filename}` for Office Online Viewer, which
     also maps `FileNotFoundError` to `503` instead of `404`)
3. **Hardcoded panel proxy upstream (`api/routes/sandbox_panel.py`).**
   `panel_http` and `panel_ws` hardcode `opensandbox-server`'s
   `/sandboxes/{id}/proxy/{port}/{path}` URL shape and header rules.
4. **SDK model leak in pure policy evaluation (`sandbox_policy/rules.py`).**
   `build_network_policy()` imports and returns
   `opensandbox.models.sandboxes.{NetworkPolicy, NetworkRule}` even though the
   module is otherwise pure domain logic.
5. **Startup validation hardcoded to one provider (`api/app.py`).**
   `validate_sandbox_config()` unconditionally requires OpenSandbox settings
   (`sandbox.domain`, `sandbox.image`, `sandbox.api_key`).
6. **Duplicated POSIX guest bootstrap glue.**
   Atomic `/run/cubeplex/sandbox-env.sh` publishing, `ttyd` startup via
   `setpriv`, workspace mount-point `chown`, and `/sandbox-panel/{token}/` URL
   signing live on `OpenSandbox` (`sandbox/opensandbox.py`), forcing any new
   container/MicroVM driver to copy the same shell commands.
7. **Archive sync branching in `LazySandbox` (`sandbox/lazy.py`).**
   `_sync_skills()` manually uploads a delta `.tar.gz` and runs a shell
   extraction command. Backends with native single-RTT archive sync (CubePod
   `/files/archive-sync`) would require `if/else` branches in `lazy.py` unless
   archive sync is part of the `Sandbox` base contract with a default fallback.
8. **Implicit full-capability assumptions break partial-capability providers (e.g. E2B, Local).**
   - `LazySandbox.supports_background()` hardcodes `return True` before creation
     (`lazy.py:508`), even if the active provider only supports foreground exec.
   - Browser live-view (`ws_browser.py`) and terminal (`ws_sandbox.py`) assume
     `/usr/local/bin/start-browser.sh` (Neko) and `/usr/bin/ttyd` exist inside
     every sandbox; on stock external templates (such as E2B), script failure
     surfaces as a retryable `503 Service Unavailable` instead of `501`, and
     `SandboxStatusOut` exposes no capability flags for the frontend to hide
     unsupported panels.
   - `SandboxManager._apply_egress()` unconditionally injects `cpodref_`
     placeholders whenever `egress_exchange` is configured, which breaks
     external SaaS sandboxes (E2B) that cannot reach the cluster-internal mTLS
     exchange listener.
   - `SANDBOX_PROMPT_TEMPLATE` (`prompts/sandbox.py`) unconditionally tells the
     LLM that `{workdir}` is a persistent volume across restarts, and global
     `run_user="cubeplex"` (`1000:1000`) fails on stock templates whose default
     user is `user`.

Meanwhile, `user_sandboxes.provider` (`models/user_sandbox.py`) and
`sandbox_commands.provider` (`models/sandbox_command.py`) already exist in
PostgreSQL, so per-row provider dispatch requires **no schema migration**.

## Approaches considered

| Approach | Summary | Why not / why |
|---|---|---|
| **A. Branch on `provider` inside `manager.py` and route handlers** | Keep existing classes; add `if provider == "cubepod"` in `manager.py`, `sandbox_panel.py`, `ws_sandbox.py`, `background_tasks.py`, `sandbox_share.py`, and `lazy.py` (prototype attempted in closed PR #771). | Every new provider edits 8+ files, balloons `manager.py` past 2,500 lines, and leaves `SLF001` private-attribute leaks and partial-capability crashes in place. Rejected. |
| **B. Expand `Sandbox` data-plane ABC only; keep control-plane factory branches in `manager.py`** | Add filesystem methods to `Sandbox` so routes are clean, but keep `create`/`connect`/`reconcile`/`kill` branching inside `SandboxManager`. | Fixes routes, but leaves `SandboxManager` coupled to every provider SDK's connection config, volume spec, error types, and panel URL rules. Rejected. |
| **C. Three-layer split (`SandboxProvider` + `Sandbox` + `PosixSandboxMixin`) with explicit `SandboxCapabilities` graceful degradation** (chosen) | Separate domain orchestration (`SandboxManager`) from provider wire calls (`SandboxProvider`), promote filesystem streaming and `sync_archive` onto `Sandbox`, share guest POSIX helpers via `PosixSandboxMixin`, and gate optional subsystems (background, reconnect, PVC, pause, panels, egress proxy, network policy, rootfs reset) via `SandboxCapabilities`. | Zero `isinstance` or `if provider == ...` in `manager.py`, `lazy.py`, `sandbox_panel.py`, or any API route. Both rich providers (`CubePod`, `OpenSandbox`) and minimal/SaaS providers (`Local`, future `E2B`) run cleanly without errors or follow-up refactoring. |

**Chosen: C**, delivered as two sequential, single-concern PRs:

1. **PR 1 (Framework refactor + capability degradation, zero regression on existing behavior):**
   Extract `SandboxProvider`, `ProviderRegistry`, `PosixSandboxMixin`, the
   9-field `SandboxCapabilities` model + graceful degradation gates, unified
   `Sandbox` filesystem/archive methods, and neutral `SandboxNetworkPolicy`;
   move `OpenSandbox` and `LocalSandbox` onto the new contracts; remove all
   direct `opensandbox` imports from routes, `manager.py`, `app.py`, and
   `sandbox_policy/rules.py`.
2. **PR 2 (CubePod provider + capability-gated lifecycle endpoint + docs):**
   Once the upstream CubePod prerequisites (§7.1) land and release as
   `cubepod>=0.4.1` on PyPI, add `cubepod>=0.4.1` via `uv add`, implement
   `sandbox/providers/cubepod.py`, add the capability-gated workspace
   `reset-rootfs` endpoint, and ship matching English + 简体中文 deployment
   docs.

---

## Design — what literally happens

### 1. Package layout

```text
backend/cubeplex/sandbox/
├── __init__.py             # Public exports (Sandbox, SandboxError, SandboxCapabilities, ...)
├── base.py                 # Sandbox ABC, SandboxCapabilities, FileEntry, errors, default sync_archive
├── posix.py                # PosixSandboxMixin (atomic env file, ttyd, browser, chown, panel URL)
├── provider.py             # SandboxProvider ABC, SandboxCreateSpec, ProviderInstanceState, PanelUpstream
├── registry.py             # ProviderRegistry (startup validation, default + per-row lookup, shutdown aclose)
├── lazy.py                 # LazySandbox + _sync_skills (uses sandbox.sync_archive & default_capabilities)
├── manager.py              # Pure CubePlex orchestrator (DB CAS, EgressRef vault, leases, reapers)
├── panel_token.py          # HS256 panel JWT claims (sid, port, prv)
└── providers/
    ├── __init__.py
    ├── opensandbox.py      # OpenSandboxProvider + OpenSandbox
    ├── local.py            # LocalSandboxProvider + LocalSandbox (dev/unit tests)
    └── cubepod.py          # CubePodProvider + CubePodSandbox (PR 2, wraps cubepod.AsyncCubePod)
```

### 2. Capability matrix & graceful degradation contract

Every `SandboxProvider` declares a static `SandboxCapabilities` descriptor, and
every connected `Sandbox` exposes `.capabilities` (`LazySandbox` delegates to
`self._sandbox.capabilities` once initialized, or
`self._manager.default_capabilities` before creation — never hardcoding `True`):

```python
@dataclass(frozen=True, slots=True)
class SandboxCapabilities:
    # Execution
    background: bool = False          # detached start / poll / kill
    reconnect: bool = False           # cross-worker attach to running command & log cursor
    # Storage & Lifecycle
    persistent_volume: bool = False   # workdir survives terminate -> revive (PVC / volume_key)
    pause: bool = False               # provider suspend & resume
    reset_rootfs: bool = False        # wipe persistent rootfs upper while preserving workdir
    # Interactive Panels
    terminal: bool = False            # web terminal panel (ttyd / PTY stream)
    browser: bool = False             # GUI browser live-view / takeover panel (Neko / desktop)
    # Security & Network
    egress_proxy: bool = False        # sandbox can reach CubePlex mTLS cpodref_ exchange
    network_policy: bool = False      # provider enforces egress allow/deny FQDN rules
```

| Capability | `CubePod` | `OpenSandbox` | `Local` | Future `E2B` (stock cloud) | System behavior when `False` (zero errors) |
|---|:---:|:---:|:---:|:---:|---|
| `background` | `True` | `True` | `True` | `True` | `SandboxMiddleware` mounts foreground-only `execute` (same shape as subagents) and omits `monitor` and `kill_execute` tools; `build_sandbox_prompt()` (`prompts/sandbox.py`) omits background-handoff, `notify_on_complete`, `monitor`, and `kill_execute` instructions so prompt instructions match mounted tools. |
| `reconnect` | `True` | `True` | `False` | `False` / version-dependent | In-run background works; `connect_command_instance` is not called for remote cancel (`remote_cancel_supported=False`). |
| `persistent_volume` | `True` | `True` | `True` | `False` | `build_sandbox_prompt()` (`prompts/sandbox.py`) renders ephemeral-workdir wording for both `**Persistence:**` and cross-session `Workspace Organization` rules (provider-constant); `_sync_skills` auto-repushes on revive because manifest lives in-sandbox; `sandbox_share` maps missing files on revived sandboxes to `404`. |
| `pause` | `True` | `True` | `False` | `True` (`beta_pause`) | `SandboxManager.pause_idle()` skips `pause()` and terminates idle sandboxes directly (existing fallback). |
| `terminal` | `True` | `True` | `False` | `False` (unless custom template) | `SandboxStatusOut.capabilities.terminal=False` lets UI hide Terminal tab; `GET /ws/{ws}/sandbox/terminal` fails fast *before* `get_or_create` with `501 Not Implemented` (`SandboxCapabilityUnsupportedError`), never `503`. |
| `browser` | `True` | `True` | `False` | `False` (unless E2B Desktop) | `SandboxStatusOut.capabilities.browser=False` lets UI hide Browser button; `GET /ws/{ws}/browser/live-view` fails fast *before* `get_or_create` with `501 Not Implemented`, never `503`. |
| `egress_proxy` | `True` | `True` | `False` | `False` | Plain env vars (`is_secret=False`) still inject via `set_run_env()`; secret vault entries (`is_secret=True`) are **never** injected as plaintext and do not inject unreachable `cpodref_` placeholders — `sandbox_config` reports `status="skipped_provider_unsupported"` so the agent gets an explicit explanation. |
| `network_policy` | `True` | `True` | `False` | `False` | Default-allow requires no action; if org admin configures `network_default_action="deny"` or explicit `deny` rules, `GET/PUT /admin/sandbox-policy` appends a clear warning in `SandboxPolicyOut.warnings` instead of failing `create()`. |
| `reset_rootfs` | `True` | `False` | `False` | `False` | `POST /ws/{ws}/sandbox/reset-rootfs` returns `501 Not Implemented`. |

#### Exposing capabilities on workspace status (`SandboxStatusOut`)

`SandboxStatusOut` (`api/schemas/sandbox_policy.py`) adds an additive field:

```python
class SandboxCapabilitiesOut(BaseModel):
    background: bool
    reconnect: bool
    persistent_volume: bool
    pause: bool
    reset_rootfs: bool
    terminal: bool
    browser: bool
    egress_proxy: bool
    network_policy: bool

class SandboxStatusOut(BaseModel):
    status: SandboxStatusValue
    provider: str
    capabilities: SandboxCapabilitiesOut
    default_image: str | None
    last_activity_at: str | None
    browser_url: str | None
```

When `status == "absent"` (no `UserSandbox` row yet), `GET /ws/{ws}/sandbox/status`
and `GET /ws/{ws}/topics/{topic_id}/sandbox` populate `provider` and
`capabilities` from `manager.default_provider` so the frontend knows what panels
and actions exist before any sandbox is booted. When a row exists, `provider`
and `capabilities` come from `registry.get(row.provider)`.

### 3. Neutral network policy types (`sandbox_policy/rules.py`)

Replace `from opensandbox.models.sandboxes import NetworkPolicy, NetworkRule`
in `backend/cubeplex/sandbox_policy/rules.py` with CubePlex-owned frozen
dataclasses:

```python
@dataclass(frozen=True, slots=True)
class SandboxNetworkRule:
    action: Literal["allow", "deny"]
    target: str  # canonicalized host or "*.domain"

@dataclass(frozen=True, slots=True)
class SandboxNetworkPolicy:
    default_action: Literal["allow", "deny"]
    egress: tuple[SandboxNetworkRule, ...] = ()
```

`build_network_policy()` keeps its exact specificity-sorting and
`force_allow_hosts` semantics (only prepending `exchange_host` when
`provider.capabilities.egress_proxy` is `True`). Each provider with
`capabilities.network_policy == True` translates `SandboxNetworkPolicy` into its
wire payload inside `provider.create()`.

### 4. Data-plane contract (`sandbox/base.py` & `sandbox/posix.py`)

#### 4.1 First-class filesystem, capability error & deterministic `close()`

Add standard filesystem primitives and error types to `Sandbox` (`base.py`):

```python
class SandboxCapabilityUnsupportedError(SandboxError):
    """Requested operation is not supported by this sandbox provider."""

    def __init__(self, capability: str, provider: str) -> None:
        self.capability = capability
        self.provider = provider
        super().__init__(f"provider {provider!r} does not support capability {capability!r}")

class SandboxFileTooLargeError(SandboxError):
    """File exceeds caller-supplied max_bytes limit."""

@dataclass(frozen=True, slots=True)
class FileEntry:
    path: str
    name: str
    is_dir: bool
    size: int
    modified_at: datetime  # tz-aware UTC
```

Methods on `Sandbox`:

- `async def list_dir(self, path: str) -> list[FileEntry]`
  Lists direct children (`depth=1`) at `path`. Missing directory raises
  `FileNotFoundError`; transport/runtime failures raise `SandboxError`.
- `async def stat_file(self, path: str) -> FileEntry`
  Returns metadata for `path`, or raises `FileNotFoundError`.
- `async def read_text(self, path: str, *, max_bytes: int | None = None) -> str`
  Reads UTF-8 text from `path`. Checks size / byte count against `max_bytes`
  and raises `SandboxFileTooLargeError` when exceeded, `FileNotFoundError` on
  404.
- `def stream_file(self, path: str) -> AbstractAsyncContextManager[AsyncIterator[bytes]]`
  Async context manager yielding raw byte chunks and deterministically closing
  the underlying HTTP response stream when the generator finishes or aborts.
  Raises `FileNotFoundError` before yielding if `path` does not exist.
- `async def close(self) -> None`
  Releases client transports owned by this `Sandbox` wrapper. `OpenSandbox.close()`
  calls `await self._sandbox.close()`, fixing the current leak workaround where
  `background_tasks.py` reached into `sandbox._sandbox.close`.
- `async def renew(self, ttl_seconds: int) -> None`
  Extends provider-side TTL on an already-connected `Sandbox` handle (delegating
  to provider TTL renewal on `OpenSandbox` and `CubePodSandbox`). Preserved on
  `Sandbox` because `BackgroundTaskCoordinator`
  (`services/background_task_coordinator.py`) calls `await sandbox.renew(ttl)`
  directly on observer handles yielded by
  `connect_command_instance() -> provider.connect_observer()`.

Route impact:

- `ws_sandbox.py` (`/files`, `/files/content`, `/files/download`),
  `background_tasks.py` (`/background-tasks/{task_id}/output`), and
  `sandbox_share.py` (`/public/sandbox/dl/{nonce}/{filename}`) call
  `sandbox.list_dir()`, `sandbox.read_text(..., max_bytes=MAX_PREVIEW_BYTES)`,
  and `sandbox.stream_file()` directly, mapping `FileNotFoundError -> 404`,
  `SandboxFileTooLargeError -> 413`, `SandboxCapabilityUnsupportedError -> 501`,
  and `SandboxError -> 503`.
- For streaming responses (`ws_sandbox.py` `/files/download` and
  `sandbox_share.py` `/public/sandbox/dl/{nonce}/{filename}`), wrap `sandbox`
  creation/attachment and `sandbox.stream_file(path)` inside
  `contextlib.AsyncExitStack`, closed by a generator `finally` plus
  `BackgroundTask(stack.aclose)` (matching `background_tasks.py`). Do **not**
  use `async with sandbox.stream_file(path)` inside the route handler body,
  because FastAPI exits the context manager before Starlette iterates
  `StreamingResponse` chunks; this also fixes existing `sandbox.close()`
  transport leaks on those download routes.
- All imports of `OpenSandbox`, `ProviderApiError`, and `# noqa: SLF001`
  disappear from `api/routes/`.
- `LocalSandbox` implements `list_dir`, `stat_file`, `read_text`, and
  `stream_file` with `pathlib.Path`, so local development and integration tests
  exercise file routes natively.

#### 4.2 Built-in `sync_archive` with tar-upload fallback

Add `sync_archive` to `Sandbox` (`base.py`):

```python
async def sync_archive(
    self,
    tarball: bytes | None,
    *,
    dest: str,
    repush_names: Sequence[str] = (),
    remove_paths: Sequence[str] = (),
) -> None:
```

- **Default implementation on `Sandbox` (`base.py`):** Uses `upload([(SKILLS_DELTA_TGZ_PATH, tarball)])`
  (when `tarball` is non-empty) followed by `execute(build_extract_and_remove_cmd(...))`
  — works on `OpenSandbox`, `LocalSandbox`, and any future `E2B` driver out of
  the box.
- **`CubePodSandbox` override (`providers/cubepod.py`):** Calls
  `await self._client.archive_sync(self.id, tarball or b"", dest=dest, uid=self._run_uid, gid=self._run_gid, remove_paths=[*repush_names, *remove_paths])`
  in a single HTTP request to `podd` without spawning a shell or writing a
  temporary `.tgz` into `/tmp`. Passing **dest-relative** skill directory names
  for both `repush_names` and `remove_paths` is required because `podd` deletes
  `remove_paths` under `dest` *before* extracting tar entries (preserving
  skill-sync invariant F9 so updated skills do not leave stale
  `<skill>/<old_version>/` directories behind) and silently skips paths
  starting with `/`. Empty `tarball` (`b""`) on uninstall-only syncs is handled
  natively by `podd` (see §7.1).
- **`lazy.py` (`_sync_skills`):** Calls `await sandbox.sync_archive(...)`
  unconditionally — zero capability flags or `if/else` branches.

#### 4.3 Shared `PosixSandboxMixin` & provider-owned `RunIdentity` (`sandbox/posix.py`)

Different providers use different guest user models: CubePlex's own image has
`cubeplex` (`1000:1000`), whereas stock external templates (e.g. E2B) use `user`
(resolved by username rather than numeric UID) and may not pre-create
`/workspace`.

- Each `SandboxProvider` resolves its effective `RunIdentity(user, uid, gid)`
  and `workdir`, defaulting to global `sandbox.{run_user, run_uid, run_gid, workdir}`
  while allowing provider-specific overrides (e.g. `sandbox.<provider>.run_user`).
- `PosixSandboxMixin(Sandbox)` centralizes:
  - `set_run_env(env)`
  - `_write_terminal_env()` — atomic base64 + `mktemp` + `mv -f` into
    `/run/cubeplex/sandbox-env.sh`
  - `start_terminal()` — checks `self.capabilities.terminal` (raising
    `SandboxCapabilityUnsupportedError` when `False`), writes terminal env, and
    starts `/usr/bin/ttyd` under `setpriv` / `runuser`
  - `start_browser()` — checks `self.capabilities.browser` (raising
    `SandboxCapabilityUnsupportedError` when `False`), launches the browser
    stack, and installs CDP tab-follow
  - `ensure_workspace_owner()` — runs `mkdir -p {workdir}` + non-recursive
    `chown` when `uid`/`user` is set (so stock images without `/workspace` get a
    writable directory automatically)
  - `_panel_endpoint(port, expires_in)` — mints `/sandbox-panel/{token}/` with
    `provider=self.provider_name` embedded in the signed JWT claims

---

### 5. Control-plane contract (`sandbox/provider.py` & `sandbox/registry.py`)

#### 5.1 Normalized control-plane types

```python
class ProviderInstanceState(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    TRANSITIONAL = "transitional"  # creating / pausing / resuming
    FAILED = "failed"
    TERMINATED = "terminated"      # includes "Succeed", "Stopped", "Killed"

@dataclass(frozen=True, slots=True)
class RunIdentity:
    user: str | None = "cubeplex"
    uid: int | None = 1000
    gid: int | None = 1000

@dataclass(frozen=True, slots=True)
class SandboxCreateSpec:
    volume_key: str | None         # None when volume disabled or persistent_volume=False
    volume_mount_path: str
    storage: str | None            # e.g. "10Gi" from EffectivePolicy
    image: str
    workdir: str
    ttl_seconds: int
    create_timeout_seconds: int
    ready_timeout_seconds: int
    resource: dict[str, str]       # {"cpu": ..., "memory": ...}
    network_policy: SandboxNetworkPolicy | None
    metadata: dict[str, str]       # org_id, workspace_id, scope_type, scope_id, user_sandbox_id

@dataclass(frozen=True, slots=True)
class PanelUpstream:
    http_url: str
    http_headers: dict[str, str]
    ws_url: str
    ws_headers: dict[str, str]
    ws_connect_host: str | None = None
    ws_connect_port: int | None = None
    ws_server_hostname: str | None = None
```

#### 5.2 `SandboxProvider` ABC

```python
class SandboxProvider(ABC):
    name: ClassVar[str]
    capabilities: ClassVar[SandboxCapabilities]

    @property
    def default_workdir(self) -> str: ...

    @property
    def default_image(self) -> str: ...
        """Default container image / template ID when org policy leaves default_image unset."""

    @abstractmethod
    def validate_config(self) -> None:
        """Fail fast at FastAPI startup if required settings are missing or placeholders."""

    @abstractmethod
    async def create(
        self,
        spec: SandboxCreateSpec,
        *,
        on_created: Callable[[str], Awaitable[None]] | None = None,
    ) -> Sandbox:
        """Create a sandbox, invoke ``on_created(sandbox_id)`` as soon as the
        provider ID exists (before readiness wait, so a crash mid-ready still
        records ``sandbox_id`` for reaper cleanup), wait until ready, and
        return a connected ``Sandbox`` bound to standard request timeouts."""

    @abstractmethod
    async def connect(
        self,
        sandbox_id: str,
        *,
        check_health: bool = True,
    ) -> Sandbox | None:
        """Attach to an existing running sandbox. When ``check_health=True``,
        returns ``None`` if unreachable or unhealthy so ``SandboxManager`` can
        terminate and revive the row."""

    async def connect_observer(
        self, sandbox_id: str, *, timeout_seconds: int = 30
    ) -> Sandbox:
        """Short-timeout read/poll attachment to a specific ``sandbox_id`` for
        background-task observation. Raises ``SandboxCapabilityUnsupportedError``
        when ``capabilities.reconnect`` is False. Callers (such as
        ``SandboxManager.connect_command_instance``) inspect control-plane state
        first so only control-plane 404/terminated/failed proves instance gone;
        data-plane connect errors raise ``SandboxError``."""

    @abstractmethod
    async def resume(self, sandbox_id: str, *, timeout_seconds: int) -> Sandbox:
        """Resume a paused sandbox and return a connected ``Sandbox``."""

    @abstractmethod
    async def inspect_state(self, sandbox_id: str) -> ProviderInstanceState | None:
        """Return normalized provider state, or ``None`` when the provider
        reports 404 / NOT_FOUND (used by ``reconcile_transients``)."""

    @abstractmethod
    async def pause(self, sandbox_id: str) -> None:
        """Initiate provider-side pause."""

    @abstractmethod
    async def terminate(self, sandbox_id: str) -> None:
        """Idempotently destroy ``sandbox_id``. 404 / already-gone succeeds."""

    @abstractmethod
    async def renew_ttl(self, sandbox_id: str, ttl_seconds: int) -> None:
        """Best-effort extend provider-side expiration."""

    def build_panel_upstream(
        self,
        *,
        sandbox_id: str,
        port: int,
        path: str,
        query: str,
    ) -> PanelUpstream:
        """Build internal HTTP/WS target URLs and auth headers for ``sandbox_panel``."""
        raise SandboxCapabilityUnsupportedError("panel", self.name)

    async def reset_rootfs(self, sandbox_id: str) -> None:
        raise SandboxCapabilityUnsupportedError("reset_rootfs", self.name)

    async def aclose(self) -> None:
        """Close long-lived control-plane HTTP pools on app shutdown."""
        return None
```

#### 5.3 `ProviderRegistry`, per-row resolution & egress/env split

`ProviderRegistry` (`sandbox/registry.py`) holds instantiated providers keyed by
name (`"opensandbox"`, `"cubepod"`, `"local"`), plus `default_name` from
`config.get("sandbox.provider", "opensandbox")`:

- **Startup (`app.py`):** `validate_sandbox_config()` checks `sandbox.enabled`,
  validates `get_command_default_timeout_seconds()`, resolves
  `registry.default()`, and calls `provider.validate_config()`.
- **Shutdown (`app.py`):** Lifespan shutdown awaits `registry.aclose()` so
  shared `httpx.AsyncClient` pools (such as `AsyncCubePod`) close cleanly.
- **New vs. existing DB rows & default image (`manager.py`, `admin_sandbox_policy.py`, `ws_sandbox.py`, `ws_topics.py`):**
  - `SandboxManager` and admin/workspace policy routes obtain fallback
    `default_image` from `registry.default().default_image` instead of reading
    `config.get("sandbox.image")` directly (`sandbox.image` on OpenSandbox,
    `sandbox.cubepod.default_template` on CubePod, `"local"` on Local).
  - `UserSandboxRepository.reserve()` stamps `record.provider = default_provider.name`.
  - When reviving a `terminated` / `failed` row in `_provision_new_container`,
    `promote_to_running` updates `provider = default_provider.name` so a revived
    workspace cleanly adopts the active default provider.
  - For every live/paused/transient row (`_connect_existing`, `_resume_record`,
    `pause_idle`, `reconcile_transients`, `_kill_record`, `touch` / `touch_active`
    `renew_ttl`, and `connect_command_instance`), `SandboxManager` resolves
    `self._registry.get(record.provider)`.
- **Safe env & egress split in `_apply_egress` (`manager.py`):**
  - When `provider.capabilities.egress_proxy` is `True` and `self._exchange_host`
    is set: identical to today — mints/renews `EgressRef` bindings and injects
    both plain env vars and `cpodref_` placeholders via `backend.set_run_env(...)`.
  - When `provider.capabilities.egress_proxy` is `False` (or `self._exchange_host`
    is empty): resolves `SandboxEnvResolver`, decrypts **only** `is_secret=False`
    entries, injects those plain env vars via `backend.set_run_env(plain_env)`,
    and skips `is_secret=True` entries without minting unreachable `EgressRef`
    rows (logging a warning once per attach when secret entries were skipped).
    `load_agent_view()` (`services/sandbox_runtime_config.py`) marks secret
    items `status="skipped_provider_unsupported"` when the active provider lacks
    `egress_proxy`, keeping `FORBIDDEN_KEY_FRAGMENTS` invariants untouched.

---

### 6. Panel reverse proxy (`panel_token.py` & `sandbox_panel.py`)

`PanelClaims` (`sandbox/panel_token.py`) gains an optional `provider` field:

```python
@dataclass(frozen=True, slots=True)
class PanelClaims:
    sandbox_id: str
    port: int
    provider: str | None = None
```

- `sign_panel_token(..., provider: str)` writes `"prv": provider` into the HS256
  JWT payload.
- `verify_panel_token()` reads `payload.get("prv")` (`None` for pre-upgrade
  tokens, which resolve to `registry.default()`).
- `sandbox_panel.py` (`panel_http` and `panel_ws`) resolves
  `provider = get_provider_registry().get(claims.provider)` and calls
  `provider.build_panel_upstream(...)`. HTTP vs WebSocket header handling is
  explicitly separated because `httpx` overrides `Host` from `http_headers`,
  whereas `websockets==14.2` derives `Host` from `ws_url` and **appends**
  `additional_headers` — passing `"Host"` in `additional_headers` emits two
  `Host` headers and triggers `400 Bad Request: too many Host headers` from
  Go's `net/http` server:
  - **`panel_http`:** Strips client `_HOP_BY_HOP` headers, forwards to
    `upstream.http_url`, and overlays `upstream.http_headers`.
  - **`panel_ws`:** Connects to `upstream.ws_url` with
    `additional_headers=upstream.ws_headers or None`, plus
    `host=upstream.ws_connect_host` / `port=upstream.ws_connect_port` (and
    `server_hostname=upstream.ws_server_hostname` when using `wss://`) if set.
  - **`OpenSandboxProvider.build_panel_upstream`:**
    `http_url="http://{domain_host}/sandboxes/{sandbox_id}/proxy/{port}/{path}"`,
    `http_headers={"accept-encoding": "identity"}`,
    `ws_url="ws://{domain_host}/sandboxes/{sandbox_id}/proxy/{port}/{path}"`,
    `ws_headers={}`.
  - **`CubePodProvider.build_panel_upstream`:**
    Uses `cubepod-gateway`'s built-in virtual-host router (which auto-resumes
    paused sandboxes and records activity on subdomain hits with zero extra
    token-mint round-trip):
    - `http_url = f"{base_url}/{path}"`,
      `http_headers = {"Host": f"{port}-{sandbox_id}.cubepod.local", "accept-encoding": "identity"}`
      (+ `"Authorization": f"Bearer {api_key}"` when set).
    - `ws_url = f"{ws_scheme}://{port}-{sandbox_id}.cubepod.local/{path}"`
      (so `websockets` emits the single `Host` header natively from `ws_url`),
      `ws_headers = {"Authorization": f"Bearer {api_key}"}` when set (never
      `"Host"`), `ws_connect_host = gateway_host`, `ws_connect_port = gateway_port`
      (plus `ws_server_hostname = gateway_host` for `wss://`).

---

### 7. CubePod provider (`sandbox/providers/cubepod.py` — PR 2)

#### 7.1 Upstream CubePod prerequisites (`cubepod>=0.4.1`)

Capabilities that are incomplete or unexposed in `cubepod` `v0.4.0` are
implemented upstream in `cubeplexai/cubepod` and released as `cubepod>=0.4.1` on
PyPI **before** CubePlex PR 2 merges, rather than disabling capabilities or
adding CubePlex-side workarounds:

1. **`volumeKey`-backed persistent PVCs across `delete -> create` (`persistent_volume=True`):**
   Currently `cubepod-gateway` stores `extension.volumeKey` only in annotations/memory,
   while `BuildBlockPVCs` derives PVC names `<sandboxID>-rootfs-pvc` /
   `<sandboxID>-workspace-pvc` from an ephemeral `cpod-<nano>` ID. Update
   `cubepod-gateway` + controller so providing `extension.volumeKey` binds a
   deterministic workspace PVC that survives normal `DELETE /sandboxes/{id}`
   (with opt-in `?deleteVolumes=true` on gateway + `AsyncCubePod.delete(..., delete_volumes=False)`),
   preserving `/workspace` across `terminate -> revive`.
2. **End-to-end `NetworkPolicy` propagation & rule fidelity (`network_policy=True`):**
   Update `cubepod-gateway` so `extension.networkPolicy` copies onto
   `PodBoxSpec.NetworkPolicy`, is preserved across `pause`, `connect`,
   `reset-rootfs`, and auto-resume `Reconcile` calls, and enforces
   `SandboxNetworkPolicy` (`defaultAction` + ordered allow/deny host rules)
   instead of silently ignoring policy rules.
3. **Empty-body `archive-sync` on uninstall-only syncs:**
   Currently `podd` rejects empty request bodies (`if req.body.is_empty()`)
   with `400 empty_archive_body`. Update `POST /v1/files/archive-sync` in `podd`
   (and gateway fallback) to accept an empty body when `remove_paths` is
   non-empty (deleting the `dest`-relative paths and returning
   `{"status": "ok", "extracted": 0}`), and align comma-separated `remove_paths`
   handling in gateway local fallback so single-RTT uninstall-only skill sync works
   without uploading a dummy tar.
4. **Release `cubepod>=0.4.1` to PyPI:**
   Publish the updated Python SDK via CubePod's PyPI OIDC workflow so CubePlex
   PR 2 uses standard `uv add "cubepod>=0.4.1"` without private git credentials
   in Docker or CI.

#### 7.2 Official SDK dependency & configuration

Add `cubepod>=0.4.1` to `backend/pyproject.toml` via `uv add "cubepod>=0.4.1"`.
Configuration in `backend/config.yaml`:

```yaml
  sandbox:
    enabled: true
    provider: "opensandbox" # "opensandbox" | "cubepod" | "local"
    # ... existing shared timeouts, ttl, pause_on_idle, workdir, run_uid/gid, resource, volume ...
    cubepod:
      base_url: ""                       # ENV: CUBEPLEX_SANDBOX__CUBEPOD__BASE_URL (e.g. http://cubepod-gateway:8080)
      api_key: ""                        # ENV: CUBEPLEX_SANDBOX__CUBEPOD__API_KEY
      default_template: "cubeplex-ubuntu24-desktop" # ENV: CUBEPLEX_SANDBOX__CUBEPOD__DEFAULT_TEMPLATE
      warm_pool: ""                      # ENV: CUBEPLEX_SANDBOX__CUBEPOD__WARM_POOL (optional PodWarmPool name)
```

`CubePodProvider.validate_config()` verifies `sandbox.cubepod.base_url` and
`sandbox.cubepod.default_template` (and rejects placeholder `api_key` values
when set). `CubePodProvider.default_image` returns `sandbox.cubepod.default_template`.

#### 7.3 Mapping CubePlex invariants onto `AsyncCubePod`

| CubePlex operation | `CubePodProvider` / `CubePodSandbox` call (`cubepod>=0.4.1`) | Notes & invariants |
|---|---|---|
| `create(spec)` | `info = await client.create(spec.image or self.default_image, volume_key=spec.volume_key, metadata=spec.metadata, network_policy=..., timeout_seconds=spec.ttl_seconds, warm_pool_ref=..., resource=spec.resource)` | Uses `info.sandbox_id` (not `info.id`) for `on_created(info.sandbox_id)` and readiness polling; `spec.volume_key` uses `build_sandbox_pvc_name(...)`, binding persistent `/workspace` across `terminate -> revive` (§7.1). `SandboxNetworkPolicy` maps onto CubePod's `NetworkPolicySpec`. |
| `connect(id, check_health=True)` | `info = await client.get(id)` (or `client.connect(id)` when paused) | Normalizes `info.state.lower()`; verifies `"running"`, returns `None` on 404/failed/terminated so `SandboxManager` revives cleanly. |
| `connect_observer(id)` | `info = await client.get(id)` → yields `CubePodSandbox` | Enables cross-worker background command polling, log tailing, cancellation, and `sandbox.renew(ttl)` keepalives on CubePod (`capabilities.reconnect = True`). |
| `resume(id)` / `pause(id)` | `await client.connect(id)` / `await client.pause(id)` | Preserves both persistent RootFS `upperdir` and `/workspace` across suspend/resume. |
| `inspect_state(id)` | `await client.get(id)` | Normalizes `info.state.lower()` (`running -> RUNNING`, `paused -> PAUSED`, `creating/pausing/resuming -> TRANSITIONAL`, `failed -> FAILED`, `terminated/stopped/killed/succeed -> TERMINATED`), HTTP 404 -> `None`. |
| `terminate(id)` | `await client.delete(id)` | Treats HTTP 404 as already terminated; retains `volumeKey` workspace PVC (§7.1). |
| `renew_ttl(id, ttl)` / `Sandbox.renew(ttl)` | `await client.renew_timeout(id, ttl)` | Non-fatal best-effort TTL extension on `touch` and observer keepalive. |
| `execute` / `start` / `poll` / `kill` | `client.execute`, `client.start_command`, `client.get_command_status`, `client.poll_command`, `client.kill_command` | Runs as `uid=run_uid, gid=run_gid` (`1000:1000`) by default, root (`0:0`) when `as_root=True`; merges `_run_env` (`cpodref_` placeholders). |
| `upload` / `download` | `client.write_file(..., uid=run_uid, gid=run_gid)` / `client.read_file` | `podd` writes directly with `1000:1000` ownership — no follow-up shell `chown` round-trip needed. |
| `list_dir` / `stat_file` / `read_text` / `stream_file` | `client.list_files`, `client.read_text_file(..., max_bytes=...)`, `client.stream_file` | Maps `CubePodFileTooLargeError -> SandboxFileTooLargeError` and HTTP 404 -> `FileNotFoundError`. |
| `sync_archive` | `client.archive_sync(id, tarball or b"", dest=dest, uid=run_uid, gid=run_gid, remove_paths=[*repush_names, *remove_paths])` | Single-RTT streaming `tar.gz` extraction + stale/repush skill removal inside `podd` (dest-relative paths, including empty-body uninstall-only syncs per §7.1). |

#### 7.4 Capability-gated workspace lifecycle route (`ws_sandbox.py` — PR 2)

Scope is strictly workspace-level (`/api/v1/ws/{workspace_id}/sandbox/...`,
guarded by `require_member` + `_resolve_sandbox_scope`; no org-admin handler
mixing):

- **`POST /api/v1/ws/{workspace_id}/sandbox/reset-rootfs`**
  - Request query/body: optional `conversation_id: str | None = None`.
  - Resolves `(scope_type, scope_id, owner_user_id)` and looks up the
    `UserSandbox` row (`404` if absent or `status in ("terminated", "failed")`).
  - Checks `provider.capabilities.reset_rootfs`; raises
    `SandboxCapabilityUnsupportedError` -> `501 Not Implemented` when `False`.
  - Returns `409 Conflict` when `record.status in ("provisioning", "pausing", "resuming", "kill_pending")`
    (safe to call when `record.status` is `"running"` or `"paused"`, since
    CubePod `reset-rootfs` reconciles `DesiredState=Running` and auto-resumes).
  - Calls `await manager.reset_sandbox_rootfs(record.id)`: fences managed
    background tasks (`request_environment_stop`), calls
    `provider.reset_rootfs(record.sandbox_id)` (wiping the persistent RootFS
    `upperdir` while keeping `/workspace` intact), updates DB status `"paused" -> "running"`
    if needed + touches activity, and re-runs guest bootstrap
    (`ensure_workspace_owner()`, `_apply_egress()`, `_write_terminal_env()`) on
    the fresh RootFS because resetting RootFS clears `/run/cubeplex/sandbox-env.sh`
    and restarts guest daemons.
  - Response: `{"ok": True, "user_sandbox_id": record.id, "sandbox_id": ...}`.

---

## Edge cases & invariants preserved

- **Prompt-cache & tool/prompt consistency (`backend/docs/prompt-cache-discipline.md`):**
  `build_sandbox_prompt(workdir, capabilities)` (`prompts/sandbox.py`) selects
  persistence and cross-session workspace wording from `capabilities.persistent_volume`,
  and background-handoff/`notify_on_complete`/`monitor`/`kill_execute` instructions
  from `capabilities.background`, matching `SandboxMiddleware`'s mounted tools.
  Both are static per provider across normal operation, keeping prompt prefixes
  cache-stable and tool instructions consistent.
- **Zero secret leakage across Egress:** `EgressRef` rows remain strictly bound
  to `sandbox_id`. Providers without `egress_proxy` never receive plaintext
  secrets and never receive broken `cpodref_` placeholders.
- **Provisioning race & mid-create cancel fence:** `provider.create()` invokes
  `on_created(sandbox_id)` before waiting on readiness, keeping
  `UserSandboxRepository.set_sandbox_id()`'s `kill_pending` /
  `SandboxCleanupRequestedError` CAS fence intact for every provider.
- **No leaked HTTP transports in reaper loops:** `OpenSandbox.close()` closes
  the underlying SDK client; `CubePodProvider` shares one pooled `AsyncCubePod`
  client closed at app shutdown. `pause_idle`, `reconcile_transients`, and
  `_kill_record` never leak per-tick sockets.
- **Rolling provider switchover:** Because `UserSandbox.provider` and
  `SandboxCommand.provider` are persisted per row, changing `sandbox.provider`
  in config does not misroute `terminate`, `reconcile_transients`, or panel
  tokens for already-running sandboxes.

## Out of scope

- Multi-provider selection per workspace or per org in the admin UI (one active
  default `sandbox.provider` per deployment; `record.provider` exists for clean
  abstraction and safe drain across config switches).
- Sandbox CoW `fork` (`SandboxCapabilities.fork`, `provider.fork()`, and
  `POST /ws/{ws}/sandbox/fork`) — deferred until a product workflow consumes
  it (today conversation fork shares `"user"` scope and topic/group upgrades
  rebind the existing row via `UserSandboxRepository.rekey`).
- Shipping a concrete `E2BProvider` in this feature cycle (this cycle lands the
  framework + capability degradation in PR 1 and `CubePodProvider` in PR 2 so a
  future `E2BProvider` is a single ~200-line file under `sandbox/providers/`).
- Frontend UI buttons for `reset-rootfs` (backend workspace API and
  `SandboxStatusOut.capabilities` land here; frontend hiding/buttons belong to a
  separate frontend PR).

## Success criteria

1. **Zero provider leaks outside `sandbox/providers/`:**
   `grep -rn "opensandbox\|OpenSandbox\|cubepod\|CubePod" backend/cubeplex/`
   matches **only** `backend/cubeplex/sandbox/providers/`, `registry.py`
   (registration), `utils/log.py` (logger noise filter), and config comments.
2. **Zero private SDK access (`SLF001`):**
   `ws_sandbox.py`, `background_tasks.py`, and `sandbox_share.py` contain zero
   `# noqa: SLF001` or `isinstance(sandbox, ...)` checks.
3. **Graceful degradation for partial-capability providers (verified via `LocalSandboxProvider` & capability-matrix contract tests):**
   - `capabilities.browser=False` / `terminal=False` returns `501` before
     provisioning and surfaces `False` in `SandboxStatusOut.capabilities`.
   - `capabilities.background=False` mounts foreground-only `execute`, omits
     `monitor` / `kill_execute`, and omits background instructions from the
     system prompt.
   - `capabilities.egress_proxy=False` injects plain envs, skips secret
     `cpodref_` bindings without leaking secrets, and marks them
     `skipped_provider_unsupported` in `sandbox_config`.
   - `capabilities.network_policy=False` surfaces admin policy `warnings` when
     `deny` rules are configured without failing sandbox creation.
4. **Existing test suite passes unchanged in PR 1:**
   All unit, integration, and e2e tests for `SandboxManager`, pause/resume
   reconciler, background tasks, skill sync, egress refs, and panel proxy pass.
5. **CubePod full parity + `reset-rootfs` in PR 2:**
   With `sandbox.provider = "cubepod"` backed by `cubepod>=0.4.1`, foreground &
   background commands, cross-worker observer, file listing/preview/streaming,
   single-RTT skill `archive_sync` (including uninstall-only), panel HTTP+WS
   proxy, `pause`/`resume`, `terminate -> revive` persistence, `network_policy`
   enforcement, and `reset-rootfs` pass contract and integration tests;
   bilingual (English + 简体中文) deployment docs ship in lockstep.
