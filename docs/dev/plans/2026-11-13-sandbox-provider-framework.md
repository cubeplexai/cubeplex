# Multi-Provider Sandbox Framework & CubePod Integration — Implementation Plan

- **Status:** Proposed; ready for review
- **Date:** 2026-11-13
- **Spec:** [Multi-provider sandbox framework and CubePod integration](../specs/2026-11-13-sandbox-provider-framework-design.md)
- **Branch / worktree:** `feat/2026-11-13-sandbox-provider-framework` / `.worktrees/feat/2026-11-13-sandbox-provider-framework`

## Overview

- **Goal:** Extract a clean control-plane (`SandboxProvider`) + data-plane (`Sandbox` + `PosixSandboxMixin`) + `SandboxCapabilities` graceful-degradation framework so business routes and `SandboxManager` have zero provider-specific branches, then plug in `CubePodProvider` (`cubepod>=0.4.1`).
- **Architecture:** `SandboxManager` retains pure CubePlex domain orchestration (`UserSandbox` CAS transitions, `EgressRef` vault lifecycle, leases, reapers) and delegates wire calls via `ProviderRegistry` (resolving `registry.default()` for new/revived rows and `registry.get(record.provider)` for active/paused/transient rows). Optional capabilities (`background`, `reconnect`, `persistent_volume`, `pause`, `reset_rootfs`, `terminal`, `browser`, `egress_proxy`, `network_policy`) degrade cleanly at middleware, route, and policy boundaries without runtime errors.
- **Tech stack:** Python 3.13, FastAPI, SQLModel / AsyncSession (zero Alembic migrations — `user_sandboxes.provider` and `sandbox_commands.provider` already exist), `opensandbox>=1.1`, `cubepod>=0.4.1`, `httpx`, `websockets`, mypy strict.
- **PR slicing (One concern per PR):**
  - **PR 1 (Units 1–4):** Pure multi-provider framework refactor + capability degradation (`OpenSandboxProvider` & `LocalSandboxProvider`).
  - **PR 2 (Units 5–6):** Upstream `cubepod>=0.4.1` prerequisite release on PyPI, then `CubePodProvider`, capability-gated `/reset-rootfs` workspace route, and English + 简体中文 deployment docs.

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
  - `SandboxCapabilities` frozen dataclass (`background`, `reconnect`, `persistent_volume`, `pause`, `reset_rootfs`, `terminal`, `browser`, `egress_proxy`, `network_policy`).
  - `SandboxCapabilityUnsupportedError(capability: str, provider: str)` and `SandboxFileTooLargeError(SandboxError)`.
  - `FileEntry(path: str, name: str, is_dir: bool, size: int, modified_at: datetime)`.
  - `Sandbox.capabilities: SandboxCapabilities` property (`supports_background()`, `supports_background_reconnect()`, `supports_pause()` delegate to `self.capabilities`), plus `Sandbox.renew(ttl_seconds: int) -> None` (preserved for `BackgroundTaskCoordinator` keepalives on observer handles).
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
  - `ProviderInstanceState(StrEnum)`: `RUNNING`, `PAUSED`, `TRANSITIONAL`, `FAILED`, `TERMINATED` (includes `"Succeed"`, `"Stopped"`, `"Killed"`).
  - `RunIdentity(user: str | None, uid: int | None, gid: int | None)`.
  - `PanelUpstream(http_url, http_headers, ws_url, ws_headers, ws_connect_host=None, ws_connect_port=None, ws_server_hostname=None)`.
  - `SandboxCreateSpec` and `SandboxProvider` ABC (`name`, `capabilities`, `default_workdir`, `default_image`, `validate_config`, `create(spec, *, on_created=None)`, `connect(sandbox_id, *, check_health=True)`, `connect_observer(sandbox_id)`, `resume(sandbox_id, *, timeout_seconds)`, `inspect_state(sandbox_id)`, `pause(sandbox_id)`, `terminate(sandbox_id)`, `renew_ttl(sandbox_id, ttl_seconds)`, `build_panel_upstream(...)`, `reset_rootfs(sandbox_id)`, `aclose()`).
- `sandbox/registry.py`:
  - `ProviderRegistry`: registers built-in providers (`opensandbox`, `local`), resolves `default()` from `sandbox.provider` (default `"opensandbox"`), resolves `get(name: str | None)` (falling back to `default()` when `None`), and awaits `aclose()` across registered providers.
- `sandbox/providers/opensandbox.py`:
  - `OpenSandbox(PosixSandboxMixin)` implementing `list_dir`, `stat_file`, `read_text` (enforcing `max_bytes` via `get_file_info` + byte check -> `SandboxFileTooLargeError`), `stream_file` (wrapping `files.read_bytes_stream`, translating 404 `ProviderApiError` to `FileNotFoundError`), `renew(ttl_seconds)`, and `close()` (`await self._sandbox.close()`).
  - `OpenSandboxProvider(SandboxProvider)` with `capabilities = SandboxCapabilities(background=True, reconnect=True, persistent_volume=True, pause=True, terminal=True, browser=True, egress_proxy=True, network_policy=True)` and `default_image = sandbox.image`. Translates `SandboxCreateSpec` (`volume_key` -> `Volume/PVC`, `SandboxNetworkPolicy` -> `opensandbox` `NetworkPolicy`), calls `on_created(raw.id)` before `check_ready`, and rebinds standard `request_timeout` after readiness.
- `sandbox/providers/local.py`:
  - `LocalSandbox` + `LocalSandboxProvider` (`capabilities = SandboxCapabilities(background=True, persistent_volume=True)`, `default_image = "local"`), adding `pathlib`-backed `list_dir`, `stat_file`, `read_text`, and `stream_file`.

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
- `_provision_new_container`: resolves `provider = self._registry.default()`, uses `provider.default_image` as fallback `default_image` in `SandboxPolicyResolver` (instead of `config.get("sandbox.image")`), stamps `provider.name` on `reserve` and `promote_to_running`, passes `volume_key = build_sandbox_pvc_name(...)` iff `self._volume_enabled and provider.capabilities.persistent_volume`, passes `force_allow_hosts=[self._exchange_host]` iff `self._exchange_host and provider.capabilities.egress_proxy`, and passes `on_created=lambda sid: repo.set_sandbox_id(record.id, sid)`.
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
- Modify: `backend/cubeplex/api/routes/v1/admin_sandbox_policy.py` (use `get_provider_registry().default().default_image` in `_default_image()`, and append `network_policy` capability warning when `deny` rules exist on a provider with `capabilities.network_policy == False`)
- Modify: `backend/cubeplex/api/routes/sandbox_panel.py`
- Modify: `backend/cubeplex/services/sandbox_runtime_config.py`
- Modify: `backend/cubeplex/services/background_task_query.py` (`TaskProjection.remote_cancel_supported` and `reconnect_supported` check `get_provider_registry().get(self.command.provider).capabilities.reconnect` instead of `self.command.provider != "local"`)
- Modify: `backend/cubeplex/middleware/sandbox.py` & `backend/cubeplex/prompts/sandbox.py`

**Interfaces & Core logic:**
- `SandboxStatusOut` adds `provider: str` and `capabilities: SandboxCapabilitiesOut` (populated in both `ws_sandbox.py` and `ws_topics.py`, including when `status == "absent"`, using `default_provider.default_image`).
- `ws_browser.py` (`/live-view`) and `ws_sandbox.py` (`/terminal`): check active/default provider `capabilities.browser` / `capabilities.terminal` **before** calling `manager.get_or_create()`, returning `501 Not Implemented` immediately when unsupported instead of booting an unusable sandbox or returning `503`.
- `ws_sandbox.py` (`/files`, `/files/content`, `/files/download`), `background_tasks.py`, and `sandbox_share.py` (`/public/sandbox/dl/{nonce}/{filename}`): replace all `OpenSandbox` imports and `sandbox._sandbox.files.*` calls with `sandbox.list_dir()`, `sandbox.read_text()`, `sandbox.stream_file()`, and wrap `sandbox` + `sandbox.stream_file(path)` in `contextlib.AsyncExitStack` closed via generator `finally` + `BackgroundTask(stack.aclose)` so `StreamingResponse` never closes prematurely or leaks `sandbox.close()` transports. `sandbox_share.py` maps `FileNotFoundError -> 404`.
- `sandbox_panel.py`: resolves `provider = get_provider_registry().get(claims.provider)` and calls `provider.build_panel_upstream(...)`; `panel_http` strips `_HOP_BY_HOP` headers then forwards to `upstream.http_url` with `upstream.http_headers`; `panel_ws` connects to `upstream.ws_url` with `additional_headers=upstream.ws_headers or None` and optional `host=upstream.ws_connect_host`, `port=upstream.ws_connect_port`, and `server_hostname=upstream.ws_server_hostname` (never putting `Host` in `additional_headers`, avoiding `websockets==14.2` duplicate `Host` header `400` responses from Go `net/http`).
- `SandboxMiddleware`: when `sandbox.capabilities.background` is `False`, registers `_make_foreground_execute_tool` for the main agent too and omits `monitor` and `kill_execute`.
- `prompts/sandbox.py`: add `build_sandbox_prompt(workdir: str, capabilities: SandboxCapabilities) -> str` gating persistence + cross-session `Workspace Organization` wording on `capabilities.persistent_volume` and gating background-handoff/`notify_on_complete`/`monitor`/`kill_execute` instructions on `capabilities.background` so prompt instructions match mounted tools (while keeping per-provider prompt prefixes deterministic and cache-stable).
- `services/sandbox_runtime_config.py`: marks `is_secret=True` env items `status="skipped_provider_unsupported"` when active provider `capabilities.egress_proxy` is `False` (or `exchange_host` is unset).

**Tests:**
- `backend/tests/unit/sandbox/test_capability_degradation.py`: verifies `background=False` tool list + foreground-only prompt, `persistent_volume=False` ephemeral prompt stability, `egress_proxy=False` `sandbox_config` agent view (`FORBIDDEN_KEY_FRAGMENTS` clean), `network_policy=False` admin policy warning, and `TaskProjection` `reconnect=False` (`remote_cancel_supported` & `reconnect_supported`).
- `backend/tests/e2e/sandbox/test_sandbox_routes_provider_agnostic.py`: exercises `/status`, `/files`, `/files/content` (including `413`), `/files/download`, `/public/sandbox/dl/{nonce}`, `/browser/live-view` (`501`), and `/sandbox/terminal` (`501`) backed by `LocalSandboxProvider` with zero mocks of internal route helpers.

---

## Phase 2 (PR 2): Upstream CubePod `v0.4.1`, `CubePodProvider` & `/reset-rootfs`

**Prerequisite (in `cubeplexai/cubepod` before PR 2 merges, released as `cubepod>=0.4.1` on PyPI):**
1. Wire `extension.volumeKey` in `cubepod-gateway` & controller so workspace PVCs bind deterministically across `DELETE /sandboxes/{id}` (retain by default; `?deleteVolumes=true` opt-out) -> `POST /sandboxes`, enabling true `persistent_volume=True` across `terminate -> revive`.
2. Propagate `extension.networkPolicy` onto `PodBoxSpec.NetworkPolicy`, preserve it across `pause`, `connect`, `reset-rootfs`, and auto-resume, and enforce ordered `SandboxNetworkPolicy` allow/deny host rules (`network_policy=True`).
3. Allow empty request bodies on `POST /v1/files/archive-sync` in `podd` (and gateway local fallback) when `remove_paths` is non-empty, aligning comma-separated `remove_paths` support so uninstall-only skill sync works in 1 RTT.
4. Publish `cubepod>=0.4.1` to PyPI via CubePod's OIDC workflow.

### Unit 5: `CubePodProvider` & `CubePodSandbox` (`cubepod>=0.4.1`)

**Files:**
- Modify: `backend/pyproject.toml` & `backend/uv.lock` (`uv add "cubepod>=0.4.1"` from PyPI)
- Modify: `backend/config.yaml` (add `sandbox.provider` and `sandbox.cubepod.*` settings)
- Create: `backend/cubeplex/sandbox/providers/cubepod.py`
- Modify: `backend/cubeplex/sandbox/registry.py` (register `"cubepod"`)

**Interfaces & Core logic:**
- `CubePodProvider(SandboxProvider)` with all 9 `SandboxCapabilities` fields `True` and `default_image = sandbox.cubepod.default_template`, owning one shared `cubepod.AsyncCubePod` client (`trust_env=False`, closed in `aclose()`).
- `validate_config()` checks `sandbox.cubepod.base_url` and `sandbox.cubepod.default_template` (and rejects placeholder `api_key`).
- `create(spec, on_created=...)` maps `spec.image or self.default_image`, `spec.volume_key`, `spec.resource`, `spec.ttl_seconds`, `spec.metadata`, `warm_pool_ref`, and `spec.network_policy` (CubePod `NetworkPolicySpec` schema) onto `AsyncCubePod.create(...)`, awaits `on_created(info.sandbox_id)` (never `info.id`), normalizes `info.state.lower()`, polls `get(info.sandbox_id)` until `"running"` within `ready_timeout_seconds` if needed, and returns `CubePodSandbox`.
- `inspect_state(id)` normalizes PascalCase CubePod `info.state.lower()` (`"running" -> RUNNING`, `"paused" -> PAUSED`, `("creating", "pausing", "resuming") -> TRANSITIONAL`, `"failed" -> FAILED`, `("terminated", "stopped", "killed", "succeed") -> TERMINATED`, HTTP 404 -> `None`).
- `CubePodSandbox(PosixSandboxMixin)` delegates `execute`, `start`/`observe`/`poll`/`kill` (byte-offset `cursor`), `renew(ttl)` (`client.renew_timeout`), `upload` (`write_file` with `uid`/`gid` — no extra `chown` command), `download`, `list_dir`, `stat_file`, `read_text` (`CubePodFileTooLargeError -> SandboxFileTooLargeError`), `stream_file`, and single-RTT `sync_archive` (`AsyncCubePod.archive_sync(self.id, tarball or b"", dest=dest, uid=..., gid=..., remove_paths=[*repush_names, *remove_paths])` using dest-relative skill directory names).
- `build_panel_upstream(...)` sets `http_url=f"{base_url}/{path}"` + `http_headers={"Host": f"{port}-{sandbox_id}.cubepod.local", "accept-encoding": "identity", ...auth}`, `ws_url=f"{ws_scheme}://{port}-{sandbox_id}.cubepod.local/{path}"`, `ws_headers={...auth}` (no `"Host"` key), and `ws_connect_host` / `ws_connect_port` / `ws_server_hostname`.

**Tests:**
- `backend/tests/unit/sandbox/test_cubepod_provider.py`: transport-level contract tests (`httpx.MockTransport` injected into real `AsyncCubePod`) covering lifecycle (`create` using `sandbox_id`, `connect`, `resume`, `pause`, `inspect_state` PascalCase mapping including `Stopped`/`Killed`/`Succeed`, `terminate`, `renew`, `reset_rootfs`), commands/cursor logs, filesystem/413/404, single-RTT `sync_archive` (update + uninstall-only), and `cubepod.local` panel upstream HTTP vs WS fields (confirming zero `"Host"` key in `ws_headers`).

---

### Unit 6: Capability-Gated `/reset-rootfs` Workspace Endpoint + Bilingual Docs

**Files:**
- Modify: `backend/cubeplex/sandbox/manager.py` (`reset_sandbox_rootfs`)
- Modify: `backend/cubeplex/api/routes/v1/ws_sandbox.py` (`POST /reset-rootfs`)
- Modify: `docs/site/docs/deployment/backend-config.md`
- Modify: `docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/deployment/backend-config.md`
- Modify: `deploy/docker-compose/.env.example` & `deploy/kubernetes/charts/cubeplex/values.yaml` (document optional `sandbox.provider` & `sandbox.cubepod.*` keys)

**Interfaces:**
- `POST /api/v1/ws/{workspace_id}/sandbox/reset-rootfs?conversation_id=...` -> `SandboxResetRootfsResponse(ok: bool, user_sandbox_id: str, sandbox_id: str)` (`404` if no sandbox row or `terminated`/`failed`, `409` if `provisioning`/`pausing`/`resuming`/`kill_pending`, `501` if `not capabilities.reset_rootfs`).

**Core logic:**
- `manager.reset_sandbox_rootfs(user_sandbox_id)`: accepts `"running"` or `"paused"` (CubePod `reset-rootfs` reconciles `DesiredState=Running`, auto-resuming paused sandboxes), fences active managed tasks (`request_environment_stop`), calls `provider.reset_rootfs(record.sandbox_id)`, promotes `"paused" -> "running"` if needed and updates activity, and re-runs guest bootstrap (`ensure_workspace_owner()`, `_apply_egress()`, `_write_terminal_env()`) on the fresh RootFS.
- Update English and 简体中文 `backend-config.md` in lockstep with `sandbox.provider` and `sandbox.cubepod.*` configuration tables and examples.

**Tests:**
- `backend/tests/e2e/sandbox/test_sandbox_lifecycle_extensions.py`: e2e tests verifying `501` on providers without `reset_rootfs` (`opensandbox`/`local`), `404` when absent/`terminated`, `409` during transient states, and `200` + `"paused" -> "running"` transition + fresh `/run/cubeplex/sandbox-env.sh` re-bootstrap on `cubepod`.
