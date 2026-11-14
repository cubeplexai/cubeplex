# Multi-Provider Sandbox Framework & CubePod Integration — Implementation Plan

- **Status:** Proposed; ready for review
- **Date:** 2026-11-13
- **Spec:** [Multi-provider sandbox framework and CubePod integration](../specs/2026-11-13-sandbox-provider-framework-design.md)
- **Branch / worktree:** `feat/2026-11-13-sandbox-provider-framework` / `.worktrees/feat/2026-11-13-sandbox-provider-framework`

## Overview

- **Goal:** Extract a clean control-plane (`SandboxProvider`) + data-plane (`Sandbox` + `PosixSandboxMixin`) + `SandboxCapabilities` graceful-degradation framework so business routes and `SandboxManager` have zero provider-specific branches, then plug in `CubePodProvider` (`cubepod>=0.4.0`).
- **Architecture:** `SandboxManager` retains pure CubePlex domain orchestration (`UserSandbox` CAS transitions, `EgressRef` vault lifecycle, leases, reapers) and delegates wire calls via `ProviderRegistry` (resolving `registry.default()` for new/revived rows and `registry.get(record.provider)` for active/paused/transient rows). Optional capabilities (`background`, `reconnect`, `persistent_volume`, `pause`, `reset_rootfs`, `fork`, `terminal`, `browser`, `egress_proxy`, `network_policy`) degrade cleanly at middleware, route, and policy boundaries without runtime errors.
- **Tech stack:** Python 3.13, FastAPI, SQLModel / AsyncSession (zero Alembic migrations — `user_sandboxes.provider` and `sandbox_commands.provider` already exist), `opensandbox>=1.1`, `cubepod>=0.4.0`, `httpx`, `websockets`, mypy strict.
- **PR slicing (One concern per PR):**
  - **PR 1 (Units 1–4):** Pure multi-provider framework refactor + capability degradation (`OpenSandboxProvider` & `LocalSandboxProvider`).
  - **PR 2 (Units 5–6):** `CubePodProvider` (`cubepod>=0.4.0`), capability-gated `/reset-rootfs` & `/fork` workspace routes, and English + 简体中文 deployment docs.

---

## Phase 1 (PR 1): Multi-Provider Sandbox Framework & Graceful Degradation

### Unit 1: Neutral Network Policy, `Sandbox` Data-Plane Extensions & `PosixSandboxMixin`

**Files:**
- Modify: `backend/cubeplex/sandbox_policy/rules.py`
- Modify: `backend/cubeplex/sandbox/base.py`
- Create: `backend/cubeplex/sandbox/posix.py`
- Modify: `backend/cubeplex/sandbox/panel_token.py`
- Modify: `backend/cubeplex/sandbox/__init__.py`

**Interfaces:**
- `sandbox_policy/rules.py`:
  - `SandboxNetworkRule(action: Literal["allow", "deny"], target: str)`
  - `SandboxNetworkPolicy(default_action: Literal["allow", "deny"], egress: tuple[SandboxNetworkRule, ...] = ())`
  - `build_network_policy(...) -> SandboxNetworkPolicy` (zero `opensandbox` imports).
- `sandbox/base.py`:
  - `SandboxCapabilities` frozen dataclass (`background`, `reconnect`, `persistent_volume`, `pause`, `reset_rootfs`, `fork`, `terminal`, `browser`, `egress_proxy`, `network_policy`).
  - `SandboxCapabilityUnsupportedError(capability: str, provider: str)` and `SandboxFileTooLargeError(SandboxError)`.
  - `FileEntry(path: str, name: str, is_dir: bool, size: int, modified_at: datetime)`.
  - `Sandbox.capabilities: SandboxCapabilities` property (`supports_background()`, `supports_background_reconnect()`, `supports_pause()` delegate to `self.capabilities`).
  - `Sandbox.list_dir(path) -> list[FileEntry]`, `stat_file(path) -> FileEntry`, `read_text(path, *, max_bytes=None) -> str`, `stream_file(path) -> AbstractAsyncContextManager[AsyncIterator[bytes]]`.
  - `Sandbox.sync_archive(tarball, *, dest, repush_names=(), remove_paths=()) -> None` (default implementation uses `upload` + `execute(build_extract_and_remove_cmd(...))`).
- `sandbox/panel_token.py`:
  - `PanelClaims(sandbox_id: str, port: int, provider: str | None = None)`
  - `sign_panel_token(*, sandbox_id: str, port: int, secret: str, ttl: timedelta, provider: str | None = None) -> str` (embeds `"prv"` claim when set; `verify_panel_token` reads `payload.get("prv")`).
- `sandbox/posix.py`:
  - `PosixSandboxMixin(Sandbox)`: shared `set_run_env`, `_write_terminal_env`, `start_terminal` (guarded by `capabilities.terminal`), `start_browser` (guarded by `capabilities.browser`), `ensure_workspace_owner` (`mkdir -p` + non-recursive `chown`), and `_panel_endpoint` (passes `provider=self.provider_name`).

**Core logic:**
- `PosixSandboxMixin` checks `self.capabilities.terminal` / `self.capabilities.browser` before running shell commands so providers without Neko or `ttyd` raise `SandboxCapabilityUnsupportedError` deterministically rather than failing shell execution with exit code 127.
- `sync_archive` on `Sandbox` preserves byte-for-byte parity with the existing tar upload + extract command in `lazy.py`.

**Tests:**
- `backend/tests/unit/sandbox/test_base_and_posix.py`: unit tests verifying `SandboxCapabilities` delegation, `PosixSandboxMixin` atomic env-file command construction, `mkdir -p` + `chown` behavior across uid/username/none modes, `SandboxCapabilityUnsupportedError` when `terminal`/`browser` are `False`, default `sync_archive` fallback, and `panel_token` round-trip with and without `"prv"`.
- Update `backend/tests/unit/sandbox_policy/test_rules.py` for `SandboxNetworkPolicy`.

---

### Unit 2: Control-Plane `SandboxProvider`, `ProviderRegistry`, `OpenSandboxProvider` & `LocalSandboxProvider`

**Files:**
- Create: `backend/cubeplex/sandbox/provider.py`
- Create: `backend/cubeplex/sandbox/registry.py`
- Create: `backend/cubeplex/sandbox/providers/__init__.py`
- Create: `backend/cubeplex/sandbox/providers/opensandbox.py` (replaces `sandbox/opensandbox.py`)
- Create: `backend/cubeplex/sandbox/providers/local.py` (replaces `sandbox/local.py`)
- Delete: `backend/cubeplex/sandbox/opensandbox.py`, `backend/cubeplex/sandbox/local.py`

**Interfaces:**
- `sandbox/provider.py`:
  - `ProviderInstanceState(StrEnum)`: `RUNNING`, `PAUSED`, `TRANSITIONAL`, `FAILED`, `TERMINATED`.
  - `RunIdentity(user: str | None, uid: int | None, gid: int | None)`.
  - `SandboxCreateSpec`, `PanelUpstream`, and `SandboxProvider` ABC (`name`, `capabilities`, `default_workdir`, `validate_config`, `create(spec, *, on_created=None)`, `connect(sandbox_id, *, check_health=True)`, `connect_observer(sandbox_id)`, `resume(sandbox_id, *, timeout_seconds)`, `inspect_state(sandbox_id)`, `pause(sandbox_id)`, `terminate(sandbox_id)`, `renew_ttl(sandbox_id, ttl_seconds)`, `build_panel_upstream(...)`, `reset_rootfs(sandbox_id)`, `fork(...)`, `aclose()`).
- `sandbox/registry.py`:
  - `ProviderRegistry`: registers built-in providers (`opensandbox`, `local`), resolves `default()` from `sandbox.provider` (default `"opensandbox"`), resolves `get(name: str | None)` (falling back to `default()` when `None`), and awaits `aclose()` across registered providers.
- `sandbox/providers/opensandbox.py`:
  - `OpenSandbox(PosixSandboxMixin)` implementing `list_dir`, `stat_file`, `read_text` (enforcing `max_bytes` via `get_file_info` + byte check -> `SandboxFileTooLargeError`), `stream_file` (wrapping `files.read_bytes_stream`, translating 404 `ProviderApiError` to `FileNotFoundError`), and `close()` (`await self._sandbox.close()`).
  - `OpenSandboxProvider(SandboxProvider)` with `capabilities = SandboxCapabilities(background=True, reconnect=True, persistent_volume=True, pause=True, terminal=True, browser=True, egress_proxy=True, network_policy=True)`. Translates `SandboxCreateSpec` (`volume_key` -> `Volume/PVC`, `SandboxNetworkPolicy` -> `opensandbox` `NetworkPolicy`), calls `on_created(raw.id)` before `check_ready`, and rebinds standard `request_timeout` after readiness.
- `sandbox/providers/local.py`:
  - `LocalSandbox` + `LocalSandboxProvider` (`capabilities = SandboxCapabilities(background=True, persistent_volume=True)`), adding `pathlib`-backed `list_dir`, `stat_file`, `read_text`, and `stream_file`.

**Core logic:**
- `OpenSandboxProvider.inspect_state()` maps `"Paused" -> PAUSED`, `"Running" -> RUNNING`, `"Failed" -> FAILED`, `("Terminated", "Succeed") -> TERMINATED`, other non-404 states -> `TRANSITIONAL`, and HTTP 404 -> `None`, always closing the probe client in `finally`.
- `OpenSandboxProvider.terminate()` treats HTTP 404 / `"not found"` as idempotent success.

**Tests:**
- `backend/tests/unit/sandbox/test_opensandbox_provider.py` & `backend/tests/unit/sandbox/test_local_provider.py`: contract tests covering `create` (`on_created` ordering before readiness), `inspect_state` normalization (including `"Succeed"` and `404 -> None`), idempotent `terminate`, `list_dir`/`read_text`/`stream_file` (`404` & `SandboxFileTooLargeError`), `close()` transport release, and `build_panel_upstream`.

---

### Unit 3: Pure Orchestrator `SandboxManager` & `UserSandboxRepository` Provider Stamping

**Files:**
- Modify: `backend/cubeplex/repositories/user_sandbox.py`
- Modify: `backend/cubeplex/sandbox/manager.py`
- Modify: `backend/cubeplex/sandbox/lazy.py`
- Modify: `backend/cubeplex/api/app.py`

**Interfaces:**
- `UserSandboxRepository.reserve(..., provider: str = "opensandbox")` and `promote_to_running(..., provider: str | None = None)`.
- `SandboxManager`:
  - `default_provider: SandboxProvider` and `default_capabilities: SandboxCapabilities` properties.
  - Zero `opensandbox` imports; accepts optional `registry: ProviderRegistry | None = None` for unit testing with fake/local providers.

**Core logic:**
- `_provision_new_container`: resolves `provider = self._registry.default()`, stamps `provider.name` on `reserve` and `promote_to_running`, passes `volume_key = build_sandbox_pvc_name(...)` iff `self._volume_enabled and provider.capabilities.persistent_volume`, passes `force_allow_hosts=[self._exchange_host]` iff `self._exchange_host and provider.capabilities.egress_proxy`, and passes `on_created=lambda sid: repo.set_sandbox_id(record.id, sid)`.
- `_apply_egress`:
  - If `self._exchange_host and provider.capabilities.egress_proxy`: existing `EgressRef` generation & memoization path.
  - Else: resolves non-secret `SandboxEnv` entries (`is_secret=False`), decrypts and injects them via `backend.set_run_env(plain_env)`, and logs a warning if any `is_secret=True` entries were skipped because `egress_proxy` is unsupported.
- `_connect_existing`, `_resume_record`, `_await_resumed_by_winner`, `pause_idle`, `reconcile_transients`, `_kill_record`, `_renew_provider_ttl`, and `connect_command_instance`: resolve `provider = self._registry.get(record.provider)` and call `provider.*` methods, ensuring every temporary control-plane handle is closed cleanly.
- `LazySandbox`:
  - `capabilities` returns `self._sandbox.capabilities` when initialized else `self._manager.default_capabilities`.
  - `_sync_skills` delegates extraction + stale removal to `await sandbox.sync_archive(...)`.
  - Adds `list_dir`, `stat_file`, `read_text`, and `stream_file` forwarding methods.
- `app.py`: `validate_sandbox_config()` delegates provider-specific env checks to `get_provider_registry().default().validate_config()`; lifespan shutdown awaits `get_provider_registry().aclose()`.

**Tests:**
- Update existing `SandboxManager` unit/e2e suites (`backend/tests/unit/sandbox/`, `backend/tests/e2e/sandbox/`) plus add multi-provider coexistence & `egress_proxy=False` plain-vs-secret env injection tests in `backend/tests/unit/sandbox/test_manager_provider_dispatch.py`.

---

### Unit 4: Provider-Agnostic Routes, Panel Proxy, Agent Middleware & Policy Diagnostics

**Files:**
- Modify: `backend/cubeplex/api/schemas/sandbox_policy.py`
- Modify: `backend/cubeplex/api/routes/v1/ws_sandbox.py`
- Modify: `backend/cubeplex/api/routes/v1/ws_topics.py`
- Modify: `backend/cubeplex/api/routes/v1/ws_browser.py`
- Modify: `backend/cubeplex/api/routes/v1/background_tasks.py`
- Modify: `backend/cubeplex/api/routes/v1/sandbox_share.py`
- Modify: `backend/cubeplex/api/routes/v1/admin_sandbox_policy.py` (append `network_policy` capability warning when `deny` rules exist on a provider with `capabilities.network_policy == False`)
- Modify: `backend/cubeplex/api/routes/sandbox_panel.py`
- Modify: `backend/cubeplex/services/sandbox_runtime_config.py`
- Modify: `backend/cubeplex/services/background_tasks.py` (`remote_cancel_supported` checks `registry.get(command.provider).capabilities.reconnect`)
- Modify: `backend/cubeplex/middleware/sandbox.py` & `backend/cubeplex/prompts/sandbox.py`

**Interfaces & Core logic:**
- `SandboxStatusOut` adds `provider: str` and `capabilities: SandboxCapabilitiesOut` (populated in both `ws_sandbox.py` and `ws_topics.py`, including when `status == "absent"`).
- `ws_browser.py` (`/live-view`) and `ws_sandbox.py` (`/terminal`): check active/default provider `capabilities.browser` / `capabilities.terminal` **before** calling `manager.get_or_create()`, returning `501 Not Implemented` immediately when unsupported instead of booting an unusable sandbox or returning `503`.
- `ws_sandbox.py`, `background_tasks.py`, and `sandbox_share.py`: replace all `OpenSandbox` imports and `sandbox._sandbox.files.*` calls with `sandbox.list_dir()`, `sandbox.read_text()`, `sandbox.stream_file()`, and `stack.push_async_callback(sandbox.close)`. `sandbox_share.py` maps `FileNotFoundError -> 404`.
- `sandbox_panel.py`: resolves `provider = get_provider_registry().get(claims.provider)`, calls `provider.build_panel_upstream(...)`, strips client `_HOP_BY_HOP` headers first, then applies `upstream.headers` on both HTTP and WebSocket connections.
- `SandboxMiddleware`: when `sandbox.capabilities.background` is `False`, registers `_make_foreground_execute_tool` for the main agent too and omits `monitor` and `kill_execute`.
- `prompts/sandbox.py`: renders persistent vs. ephemeral `{workdir}` paragraph from `sandbox.capabilities.persistent_volume` (static per provider, preserving prompt-cache prefix stability).
- `services/sandbox_runtime_config.py`: marks `is_secret=True` env items `status="skipped_provider_unsupported"` when active provider `capabilities.egress_proxy` is `False` (or `exchange_host` is unset).

**Tests:**
- `backend/tests/unit/sandbox/test_capability_degradation.py`: verifies `background=False` tool list, `persistent_volume=False` prompt stability, `egress_proxy=False` `sandbox_config` agent view (`FORBIDDEN_KEY_FRAGMENTS` clean), and `network_policy=False` admin policy warning.
- `backend/tests/e2e/sandbox/test_sandbox_routes_provider_agnostic.py`: exercises `/status`, `/files`, `/files/content` (including `413`), `/files/download`, `/public/sandbox/dl/{nonce}`, `/browser/live-view` (`501`), and `/sandbox/terminal` (`501`) backed by `LocalSandboxProvider` with zero mocks of internal route helpers.

---

## Phase 2 (PR 2): CubePod Provider (`cubepod>=0.4.0`), Lifecycle Endpoints & Docs

### Unit 5: `CubePodProvider` & `CubePodSandbox` (`cubepod>=0.4.0`)

**Files:**
- Modify: `backend/pyproject.toml` & `backend/uv.lock` (`uv add "cubepod>=0.4.0"`)
- Modify: `backend/config.yaml` (add `sandbox.provider` and `sandbox.cubepod.*` settings)
- Create: `backend/cubeplex/sandbox/providers/cubepod.py`
- Modify: `backend/cubeplex/sandbox/registry.py` (register `"cubepod"`)

**Interfaces & Core logic:**
- `CubePodProvider(SandboxProvider)` with all 10 `SandboxCapabilities` fields `True`, owning one shared `cubepod.AsyncCubePod` client (`trust_env=False`, closed in `aclose()`).
- `validate_config()` checks `sandbox.cubepod.base_url` and `sandbox.cubepod.default_template` (and rejects placeholder `api_key`).
- `create(spec, on_created=...)` maps `spec.volume_key`, `spec.resource`, `spec.ttl_seconds`, `spec.metadata`, `warm_pool_ref`, and `spec.network_policy` (`{"defaultAction": ..., "egress": [...]}`) onto `AsyncCubePod.create(...)`, awaits `on_created(info.id)`, polls `get(info.id)` until `running` within `ready_timeout_seconds` if needed, and returns `CubePodSandbox`.
- `CubePodSandbox(PosixSandboxMixin)` delegates `execute`, `start`/`observe`/`poll`/`kill` (byte-offset `cursor`), `upload` (`write_file` with `uid`/`gid` — no extra `chown` command), `download`, `list_dir`, `stat_file`, `read_text` (`CubePodFileTooLargeError -> SandboxFileTooLargeError`), `stream_file`, and single-RTT `sync_archive` (`AsyncCubePod.archive_sync`).
- `build_panel_upstream(...)` targets `{base_url}/{path}` (and `ws://`/`wss://`) with `Host: {port}-{sandbox_id}.cubepod.local`, `accept-encoding: identity`, and `Authorization: Bearer {api_key}` when configured.

**Tests:**
- `backend/tests/unit/sandbox/test_cubepod_provider.py`: transport-level contract tests (`httpx.MockTransport` injected into real `AsyncCubePod`) covering lifecycle (`create`, `connect`, `resume`, `pause`, `inspect_state`, `terminate`, `reset_rootfs`, `fork`), commands/cursor logs, filesystem/413/404, single-RTT `sync_archive`, and `cubepod.local` panel upstream HTTP+WS headers.

---

### Unit 6: Capability-Gated `/reset-rootfs` & `/fork` Workspace Endpoints + Bilingual Docs

**Files:**
- Modify: `backend/cubeplex/sandbox/manager.py` (`reset_sandbox_rootfs`, `fork_sandbox`)
- Modify: `backend/cubeplex/api/routes/v1/ws_sandbox.py` (`POST /reset-rootfs`, `POST /fork`)
- Modify: `docs/site/docs/deployment/backend-config.md`
- Modify: `docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/deployment/backend-config.md`
- Modify: `deploy/docker-compose/.env.example` & `deploy/kubernetes/charts/cubeplex/values.yaml` (document optional `sandbox.provider` & `sandbox.cubepod.*` keys)

**Interfaces:**
- `POST /api/v1/ws/{workspace_id}/sandbox/reset-rootfs?conversation_id=...` -> `SandboxResetRootfsResponse(ok: bool, user_sandbox_id: str, sandbox_id: str)` (`501` if `not capabilities.reset_rootfs`, `404` if no active sandbox).
- `POST /api/v1/ws/{workspace_id}/sandbox/fork?conversation_id=...` with body `SandboxForkRequest(target_conversation_id: str)` -> `SandboxForkResponse(ok: bool, user_sandbox_id: str, sandbox_id: str)` (`501` if `not capabilities.fork`, `400` if same scope, `409` if target scope already has an active sandbox).

**Core logic:**
- `manager.reset_sandbox_rootfs(user_sandbox_id)`: fences active managed tasks (`request_environment_stop`), calls `provider.reset_rootfs(record.sandbox_id)`, updates activity, and re-applies egress env.
- `manager.fork_sandbox(...)`: reserves the target scope's `UserSandbox` row (`status="provisioning"`), computes `target_volume_key = build_sandbox_pvc_name(...)`, calls `provider.fork(source_sandbox_id, target_volume_key=..., metadata=...)`, records `set_sandbox_id` + `promote_to_running`, and mints fresh `EgressRef` rows for the forked `sandbox_id`.
- Update English and 简体中文 `backend-config.md` in lockstep with `sandbox.provider` and `sandbox.cubepod.*` configuration tables and examples.

**Tests:**
- `backend/tests/e2e/sandbox/test_sandbox_lifecycle_extensions.py`: e2e tests verifying `501` on providers without `reset_rootfs`/`fork` (`opensandbox`/`local`), `200` + DB state + isolated `EgressRef` minting on `cubepod`, `409` conflict when target conversation already has an active sandbox, and cross-conversation RBAC isolation (`404` when caller lacks access to source or target conversation).
