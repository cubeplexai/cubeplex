# Sandbox e2b Backend — Design Spec

**Issue:** #146 · **Date:** 2026-05-27 · **Status:** Draft

## Problem & motivation

Sandboxes (remote execution environments for agents) currently target a single
provider: OpenSandbox. The lifecycle controller (`SandboxManager`) imports the
`opensandbox` SDK directly and hardcodes the `OpenSandbox` driver. There is a
clean `Sandbox` driver interface (`backend/cubebox/sandbox/base.py`), but no
provider-selection seam above it — nothing lets an operator pick a different
provider by config.

We want **e2b** (e2b.dev) as a second, fully pluggable backend so the platform
isn't tied to one sandbox vendor. e2b is a widely-used hosted code-execution
service with its own Python SDK, custom templates, persistence (pause/resume),
and egress firewall — a good fit for deployments that don't run OpenSandbox's
Kubernetes data plane.

This spec is about the **backend driver + the provider-selection seam**. It
deliberately depends on, but does not re-specify, the ownership model (#144)
and pause/resume state machine (#145).

## Goals

- Add an `E2BSandbox` driver implementing the existing `Sandbox` interface:
  create, execute, file read/write, lifecycle (close/kill), and (where the
  provider allows) network egress rules + port exposure for the browser live
  view.
- Introduce a **provider-selection seam** so the active backend is chosen by
  config and injected per run — no caller-side code references a concrete
  driver.
- Store the e2b API key via the existing credential vault pattern, not plaintext
  config.
- Reuse the #144 ownership dimension (`(workspace_id, user_id)`) and the #145
  pause/resume abstraction; document where e2b's capabilities differ from
  OpenSandbox so those specs can degrade gracefully.

## Non-goals

- Implementing #144 (ownership/admin policy) or #145 (pause/resume state
  machine) themselves. This spec consumes their abstractions and flags gaps;
  it does not build them.
- Re-architecting the egress key-injection design
  (`2026-05-25-egress-key-injection-design.md`). e2b's egress model is mapped
  here only at the interface level.
- A frontend provider picker. Backend selection is config-driven for v1.
- Migrating existing OpenSandbox deployments. The project hasn't shipped; we
  cut over cleanly with no compat shims.

## Current state

### The driver interface (already exists)

`backend/cubebox/sandbox/base.py` defines `Sandbox(ABC)`. A new backend must
satisfy exactly this contract:

**Abstract (must implement):**

- `id: str` (property) — unique instance identifier.
- `workdir: str` (property) — default working directory for commands.
- `async execute(command, *, timeout=None, envs=None) -> ExecuteResult` —
  run a shell command; returns combined stdout+stderr (`output`) and
  `exit_code`. `envs` are per-call env overrides merged over run-level env
  (per-call wins).
- `async upload(files: list[tuple[str, bytes]]) -> None` — write `(abs_path,
  bytes)` pairs into the sandbox.
- `async download(paths: list[str]) -> list[tuple[str, bytes]]` — read bytes
  back; raise `FileNotFoundError` on a missing path.
- `async close() -> None` — release resources (OpenSandbox makes this a no-op;
  the cleanup task is what actually kills sandboxes).

**Overridable (default provided):**

- `set_run_env(env: dict[str, str]) -> None` — attach run-level env injected
  into every subsequent `execute`. Default no-op; OpenSandbox overrides.
- `start_browser() -> None` — runs the in-image `/usr/local/bin/start-browser.sh`.
- `get_browser_endpoint(*, expires_in=3600) -> BrowserEndpoint` — return an
  iframe-embeddable URL (+ optional headers) for the Neko browser on
  `BROWSER_PORT = 8080`. Default raises `NotImplementedError`.
- `has_synced` / `mark_synced` — in-memory skill-sync dedupe (free for all
  subclasses).
- `file_read(...)` — default downloads bytes + dispatches the parser registry.

**Errors:** every driver translates provider-specific exceptions into
`SandboxError` so layers above the driver never import the vendor SDK.

Existing implementations: `OpenSandbox` (`opensandbox.py`), `LocalSandbox`
(`local.py`, dev only), `LazySandbox` (`lazy.py`, a deferral proxy that wraps
whatever the manager returns).

### The missing seam: SandboxManager is OpenSandbox-specific

`backend/cubebox/sandbox/manager.py` is **not** provider-neutral. It:

- imports `opensandbox`, `opensandbox.config.ConnectionConfig`,
  `opensandbox.models.sandboxes.{PVC, Volume}`, `RunCommandOpts` indirectly;
- calls `opensandbox.Sandbox.create / .connect / .is_healthy / .kill`
  directly (`get_or_create`, `cleanup_expired`);
- constructs `OpenSandbox(...)` directly;
- reads OpenSandbox-shaped config keys (`sandbox.domain`, `sandbox.image`,
  `sandbox.use_server_proxy`, PVC volume prefix, `resource.cpu/memory`);
- builds Kubernetes PVC volumes (`_build_user_volume`) — an OpenSandbox concept.

Per-run wiring: `backend/cubebox/streams/run_manager.py` (~line 1730) builds a
`LazySandbox(manager=get_sandbox_manager(), user_id, org_id, workspace_id, ...)`,
and `LazySandbox._ensure()` calls `manager.get_or_create(...)`. The manager
singleton is created once at startup in `api/app.py` (`init_sandbox_manager`).

**Implication:** the clean per-driver interface exists, but provider *lifecycle*
(create/connect/health/kill) lives inline in `SandboxManager`. To add e2b we
must first **extract a provider seam** that the manager calls instead of the
`opensandbox.*` module functions. This is the central design decision below.

### Config today

`config.yaml` → `sandbox:` block: `enabled, domain, image, api_key,
use_server_proxy, ready_timeout, request_timeout, create_timeout, ttl,
touch_interval, cleanup_interval, workdir, resource.{cpu,memory}, volume.*,
egress_exchange_host`. All OpenSandbox-shaped; there is no `provider` key.

## e2b research

e2b ships an async Python SDK (`from e2b import AsyncSandbox` / `Sandbox`).
Capability mapping against the cubebox `Sandbox` interface:

| cubebox need | e2b API | Gap / notes |
|---|---|---|
| Create sandbox | `Sandbox.create(template=..., timeout=, metadata=, envs=, allow_internet_access=, network=)` | Clean fit. `template` replaces OpenSandbox `image`. `timeout` is the sandbox lifetime (max 1h Hobby / 24h Pro). `allow_internet_access` and `network` are **two separate top-level kwargs** — see the egress row. |
| Reconnect by id | `Sandbox.connect(sandbox_id)` — auto-resumes if paused | Maps to the reuse path. No explicit `is_healthy()`; treat a failing `connect`/first command as unhealthy → recreate. |
| `execute(command, timeout, envs)` | `sandbox.commands.run(cmd, envs=, timeout=, cwd=, background=)` → returns `exit_code`, `stdout`, `stderr` | Clean fit. Combine stdout+stderr into `ExecuteResult.output`; `cwd=workdir`; merge run-level + per-call envs (per-call wins), same as OpenSandbox. |
| `upload([(path, bytes)])` | `sandbox.files.write(path, content)` (str or bytes; creates dirs) | Clean fit, per-file loop. |
| `download([paths])` | `sandbox.files.read(path, format="bytes")` | Clean fit. Map e2b not-found error → `FileNotFoundError`. |
| `set_run_env(env)` | no run-level env store; pass `envs=` per `commands.run` | Driver holds `_run_env` and merges per call (mirror OpenSandbox). |
| `close()` / kill | `sandbox.kill()` (also `Sandbox.kill(sandbox_id)`) | Fit. `close()` stays a no-op; cleanup task calls kill, as today. |
| Lifetime / TTL | `sandbox.set_timeout(seconds)` | e2b enforces its own max lifetime; cubebox TTL must stay **≤** the e2b ceiling, and `touch` should call `set_timeout` to extend. |
| Port exposure (browser live view, port 8080) | `host = sandbox.get_host(8080)` → `https://{host}` | Fit, but **e2b URLs are public by default**. Restrict by setting `allow_public_traffic: False` inside the `network` create option — `Sandbox.create(network={"allow_public_traffic": False})` (`allow_public_traffic` is a `network` field, **not** a top-level kwarg) — then send the `e2b-traffic-access-token` header (`sandbox.traffic_access_token`) on every request. Maps to `BrowserEndpoint(url, headers)` — non-empty headers means the frontend needs the same-origin injecting proxy the interface already anticipates. |
| Network egress rules (#144) | Two distinct create kwargs: **(1)** `allow_internet_access: bool = True` is a **top-level** create kwarg — the master internet on/off switch (`False` is equivalent to `network` `deny_out=[0.0.0.0/0]`). **(2)** `network=SandboxNetworkOpts` is a separate top-level kwarg holding the fine-grained rules: `network={"allow_out": [...], "deny_out": [...], "allow_public_traffic": bool}` (IP/CIDR + `*.domain` wildcards). `allow_internet_access` does **not** live inside `network`. `update_network()` mutates the `network` rules on a running sandbox; per-host request transforms (`network.rules`) are private beta. | Strong fit for #144 domain allowlists. **Differs** from OpenSandbox's `network_policy` + egress-exchange model — see Capability gaps. |
| Pause / resume (#145) | `sandbox.beta_pause()`; resume by `Sandbox.connect()` (auto-resumes); `beta_create(auto_pause=...)` for idle auto-suspend | Beta. Preserves filesystem **and** memory. Known bug: repeated pause/resume can drop later file changes (e2b-dev/E2B #884). Treat as best-effort for #145. |
| Custom image | e2b "templates" built from a Dockerfile via the `e2b template build` CLI, referenced by template name/id at create | **Structurally different** from OpenSandbox image refs. Browser/Neko stack + `start-browser.sh` must be baked into a custom e2b template, or the browser live view is unavailable on e2b. |
| Auth | `api_key` (env `E2B_API_KEY` or `create(api_key=...)`) | Single API key per e2b account. Store in the credential vault. |
| Volumes / PVC | (none equivalent) | e2b has no Kubernetes PVC. Per-user persistent disk = pause/resume + reconnect, not a mounted volume. OpenSandbox `volume.*` config does not apply. |

Sources are listed in **References**.

## Proposed design

### 1. Provider interface (final shape)

Keep the per-instance `Sandbox` ABC **unchanged** — it is already provider
neutral and both drivers satisfy it. Introduce a new **provider** abstraction
for *lifecycle* (the part currently inlined in `SandboxManager`):

```
class SandboxProvider(ABC):
    async def create(self, *, image, workdir, resource, network, volumes,
                     timeout) -> Sandbox: ...
    async def connect(self, sandbox_id, *, workdir) -> Sandbox | None: ...
        # returns a live Sandbox, or None if unreachable/unhealthy
    async def kill(self, sandbox_id) -> None: ...
    async def set_lifetime(self, sandbox, seconds) -> None: ...
        # extend remaining lifetime (TTL touch); no-op where unsupported
```

- The argument types (`resource`, `network`, `volumes`, `image`) become a small
  set of provider-neutral dataclasses so the manager never passes
  OpenSandbox/e2b-shaped objects. Each provider maps them to its own SDK call
  and ignores fields it can't honor (e.g. e2b ignores `volumes`).
- `SandboxManager` is refactored to depend on a `SandboxProvider`, not on the
  `opensandbox` module. Health-check becomes "did `connect` return a live
  sandbox"; PVC logic moves behind `OpenSandboxProvider`.
- Drivers (`OpenSandbox`, `E2BSandbox`) stay as the `Sandbox` impls the provider
  returns. `LazySandbox` is unchanged — it already wraps "whatever the manager
  returns."

This is the smallest change that removes the hardcoding while preserving the
existing per-instance interface and all manager behavior (reuse, touch, egress
refs, cleanup).

### 2. e2b backend mapping

- `E2BProvider(SandboxProvider)` — wraps the e2b SDK; `create` →
  `Sandbox.create(template=image, timeout=, envs=, allow_internet_access=,
  network=...)`. The internet on/off toggle and the egress rules are **two
  separate top-level kwargs**:
  - `allow_internet_access=<bool>` (top-level) — the master internet switch.
    `False` blocks all outbound (≡ `network` `deny_out=[0.0.0.0/0]`). Putting
    this inside `network` would **not** disable outbound, so it must stay at
    the top level.
  - `network={"allow_public_traffic": False, "allow_out": [...],
    "deny_out": [...]}` (top-level) — the fine-grained allow/deny egress rules
    plus the public-URL gate. `allow_public_traffic` is a `network` field
    (a top-level `allow_public_traffic=` would raise an unexpected-argument
    error or be silently dropped, leaving live-view URLs public — see
    References).

  `connect` → `Sandbox.connect(id)`; `kill` → `Sandbox.kill(id)`;
  `set_lifetime` → `sandbox.set_timeout`.
- `E2BSandbox(Sandbox)` — wraps an e2b sandbox handle; `execute` →
  `commands.run`; `upload`/`download` → `files.write`/`files.read`;
  `set_run_env` merges per call; `get_browser_endpoint` → `get_host(8080)` +
  traffic-access-token header; translates e2b exceptions to `SandboxError`.

### 3. Factory + config-driven selection + per-run injection

- New config key `sandbox.provider: opensandbox | e2b` (default `opensandbox`).
  Provider-specific settings live under `sandbox.opensandbox.*` and
  `sandbox.e2b.*` (template, timeout ceiling, `allow_internet_access` — which the
  provider passes as the top-level create kwarg — and `allow_public_traffic` —
  which the provider maps into the SDK's `network` option, never a top-level
  kwarg).
  Existing
  flat keys that are truly cross-provider (`ttl`, `touch_interval`, `workdir`,
  `cleanup_interval`) stay at `sandbox.*`.
- A `build_sandbox_provider(config) -> SandboxProvider` factory reads
  `sandbox.provider` and returns the matching provider. `init_sandbox_manager`
  calls it once at startup; the manager holds the provider.
- **Per-run injection** is unchanged at the call site: `run_manager` builds a
  `LazySandbox(manager=get_sandbox_manager(), ...)`. The active provider is an
  attribute of the manager singleton, selected by config — callers never name a
  driver. (Per-run provider override is an open question, not v1.)

### 4. Credential storage

The e2b API key is a system-level secret. Follow the credential-vault pattern
(see `project_credential_vault_design`): store it as a vault entry with
`org_id = NULL` (system scope), a new credential kind (e.g. `sandbox_provider`)
keyed by provider name, resolved at provider construction. Config carries only a
*reference*, never the key. `sandbox.e2b.api_key` may keep an env-var fallback
for local dev, consistent with how OpenSandbox `api_key` is read today, but the
production path is the vault.

## Capability gaps & how to degrade

- **Custom image / browser live view.** The Neko browser stack and
  `start-browser.sh` are baked into the OpenSandbox image. On e2b they must be
  baked into a custom e2b *template*. Until such a template exists,
  `E2BSandbox.get_browser_endpoint` should raise `NotImplementedError` (the
  base default) so the browser panel cleanly reports "unavailable on this
  backend" rather than failing mid-request.
- **Volumes / per-user persistent disk.** No e2b PVC equivalent. The
  `OpenSandboxProvider` keeps PVC support; `E2BProvider` ignores `volumes`.
  Persistence on e2b comes from pause/resume + reconnect (#145), not a mount.
- **Network egress (#144).** OpenSandbox uses a `network_policy` set at create
  + the egress-exchange placeholder model. e2b splits this across **two**
  top-level create kwargs: `allow_internet_access` (the master internet on/off
  switch) and `network` (the `allow_out` / `deny_out` / `allow_public_traffic`
  rules), plus `update_network()` on a running sandbox. #144's neutral "domain
  allowlist / egress rules" must map to *both* providers; each translates the
  neutral rules into its own shape — OpenSandbox into `network_policy`, e2b into
  the top-level `allow_internet_access` toggle plus the `network` option. e2b's
  per-host request transforms (header injection) are
  private beta — the egress-exchange secret-swap model is **not** portable to
  e2b in v1; e2b runs without the exchange (env injected directly), which is
  acceptable because e2b is a trusted hosted service.
- **Pause/resume (#145).** e2b `beta_pause`/`connect` covers it but is beta and
  has a known repeated-resume file-loss bug. #145's state machine should treat
  pause/resume as a per-provider *capability flag*; e2b advertises it as
  best-effort. OpenSandbox's capability here is tracked by #145 separately.
- **Health check.** No e2b `is_healthy()`. The provider seam's `connect`
  returning `None` (or a failing first command) is the portable health signal.

## v1 scope

In:

- `SandboxProvider` seam + neutral arg dataclasses; refactor `SandboxManager`
  off direct `opensandbox.*` use onto the seam (`OpenSandboxProvider`).
- `E2BProvider` + `E2BSandbox`: create, connect, execute, upload, download,
  set_run_env, kill, set_lifetime, `SandboxError` translation.
- Config `sandbox.provider` + `sandbox.e2b.*`; factory; vault-backed API key.
- e2b network passthrough at create: the top-level `allow_internet_access` bool
  toggle plus the `network` allow/deny rules (consume #144's neutral rules if
  available; otherwise just the `allow_internet_access` bool).

Out (deferred):

- e2b custom template with Neko → browser live view on e2b.
- e2b pause/resume wired into #145's state machine (land with #145).
- Egress-exchange secret swap on e2b.
- Per-run/per-workspace provider override.

## Testing strategy

E2E-first per project policy, but e2b is a third-party hosted service requiring
a real API key and billing — it has no local test mode. So:

- **Unit (always-run, no network):** provider factory selects the right
  provider per config; neutral-arg → e2b-SDK argument mapping; e2b exception →
  `SandboxError` translation; stdout+stderr→`output` and exit-code mapping;
  not-found → `FileNotFoundError`; run-level/per-call env merge precedence.
  Mock the e2b SDK client at the driver boundary (this is unit-level mapping
  coverage, **not** a fake-server E2E — consistent with the "no fake E2E for
  unsimulatable systems" rule).
- **E2E (opt-in, gated on a real `E2B_API_KEY`):** a marked test
  (`@pytest.mark.e2b`, skipped when the key is absent) creates a real e2b
  sandbox, runs `echo`, writes+reads a file, and kills it. Runs locally / in a
  manually-triggered CI lane with the key present; never blocks the default
  suite.
- **OpenSandbox regression:** the existing OpenSandbox E2E path must still pass
  after the manager refactor — that's the safety net proving the seam didn't
  change behavior for the default provider.

## Open Questions

1. **Provider scope.** Is the active provider always process-global (one
   provider per deployment), or do we eventually need per-org / per-workspace
   provider selection? v1 assumes global; per-run override is deferred — confirm
   that's acceptable.
2. **#144 dependency ordering.** This spec consumes #144's neutral network-rule
   shape and `(workspace_id, user_id)` ownership. Should e2b land *after* #144
   merges, or land against the current per-user model and absorb #144's neutral
   types when they exist?
3. **Egress-exchange on e2b.** Confirm it's acceptable to inject env secrets
   directly into e2b (trusted hosted service) and skip the exchange-placeholder
   swap, rather than building an e2b request-transform equivalent.
4. **Browser live view priority.** Is shipping a custom e2b template with the
   Neko stack in-scope soon, or is "browser unavailable on e2b" acceptable
   indefinitely for v1?
5. **e2b lifetime ceiling vs cubebox TTL.** cubebox `ttl` (default 1800s) is
   under e2b's 1h Hobby ceiling, but long sessions + `touch` could approach it.
   Do we cap cubebox TTL at the e2b plan ceiling, or rely on pause/resume to
   extend beyond it?
6. **Self-hosted vs cloud e2b.** Target e2b cloud only, or also support
   self-hosted e2b (`domain`/base-URL override)? Affects whether `sandbox.e2b`
   needs a base-URL key.
7. **Exact `network` option type.** The v2.15.0 sync SDK signature is
   `create(..., allow_internet_access: bool = True, network: Optional[
   SandboxNetworkOpts] = None, ...)` — `allow_internet_access` top-level,
   `network` a separate top-level `SandboxNetworkOpts`. Docs show `network` as a
   dict (`{"allow_public_traffic": False, "allow_out": [...], "deny_out": [...]}`),
   but a typed `SandboxNetworkOpts` may be preferred. Pin the e2b SDK version
   and confirm the precise `network` field shape (dict vs typed) and the
   public-URL header name (`e2b-traffic-access-token`) against that version
   before implementing `E2BProvider.create`.

## References

- Current code: `backend/cubebox/sandbox/base.py`,
  `backend/cubebox/sandbox/opensandbox.py`,
  `backend/cubebox/sandbox/manager.py`, `backend/cubebox/sandbox/lazy.py`,
  `backend/cubebox/middleware/sandbox.py`,
  `backend/cubebox/streams/run_manager.py` (~L1730).
- Related specs/plans: `docs/dev/specs/2026-05-25-egress-key-injection-design.md`,
  `docs/dev/plans/2026-05-25-sandbox-env-vault.md`.
- Issues: #144 (ownership + admin policy), #145 (pause/resume), #146 (this).
- e2b docs:
  - Sandbox lifecycle / create / set_timeout / kill — https://e2b.dev/docs/sandbox
  - Commands — https://e2b.dev/docs/commands
  - Filesystem — https://e2b.dev/docs/filesystem
  - Internet access / egress rules / public-URL `network` option — the
    internet toggle `allow_internet_access` is a top-level create kwarg, while
    the allow/deny rules and `allow_public_traffic` live inside the separate
    top-level `network` option (`network={"allow_public_traffic": False, ...}`)
    / update_network — https://e2b.dev/docs/sandbox/internet-access
  - Persistence (pause/resume, beta) — https://e2b.dev/docs/sandbox/persistence
  - Python SDK reference (AsyncSandbox) — https://e2b.dev/docs/sdk-reference/python-sdk/v2.0.1/sandbox_async
  - Python SDK `Sandbox.create` signature (`allow_internet_access` top-level,
    `network: Optional[SandboxNetworkOpts]` separate) —
    https://e2b.dev/docs/sdk-reference/python-sdk/v2.15.0/sandbox_sync
  - get_host / port exposure / public URLs — https://e2b.dev/docs/sdk-reference/js-sdk/v1.13.1/sandbox
  - Repeated-resume file-loss bug — https://github.com/e2b-dev/E2B/issues/884
