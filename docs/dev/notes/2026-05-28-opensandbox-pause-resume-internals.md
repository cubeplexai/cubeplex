# OpenSandbox Pause/Resume — SDK internals & gotchas

Read of the installed `opensandbox` SDK (`backend/.venv/.../opensandbox`) to ground the
`#145` sandbox pause/resume implementation. This note captures the **traps** the
implementer must respect. **Extend this file as new findings surface during
implementation.**

References (all paths relative to the installed SDK):

- `sandbox.py` — async `Sandbox` (instance `pause()`, classmethods `connect()` and
  `resume()`).
- `services/sandbox.py` — `SandboxService` protocol (`pause_sandbox`, `resume_sandbox`,
  `get_sandbox_endpoint`, `renew_sandbox_expiration`, `kill_sandbox`, `get_info`,
  `create_snapshot`, …).
- `adapters/sandboxes_adapter.py` — concrete HTTP impl (`pause_sandbox` →
  `post_sandboxes_sandbox_id_pause`, `resume_sandbox` →
  `post_sandboxes_sandbox_id_resume`).
- `api/lifecycle/api/sandboxes/post_sandboxes_sandbox_id_pause.py` — REST call to
  `POST /sandboxes/{id}/pause`; documented response codes 202 / 401 / 403 / 404 / 409 /
  500.
- `api/lifecycle/api/sandboxes/post_sandboxes_sandbox_id_resume.py` — REST call to
  `POST /sandboxes/{id}/resume`; same response shape.
- `api/lifecycle/models/sandbox_status.py` — wire shape of `SandboxStatus` returned by
  `get_info()`.
- `models/sandboxes.py` — `SandboxState` constants class (local enum-like).
- `manager.py` — top-level `Manager.pause_sandbox(id)` / `Manager.resume_sandbox(id)`
  wrappers; same async-initiate semantics.
- Sync mirror lives under `opensandbox/sync/…` (same shapes).

## Gotchas to respect in the cubebox implementation

### G1. Pause is asynchronous on the server (HTTP 202 Accepted)

`Sandbox.pause()` → `services.pause_sandbox` → HTTP `POST /sandboxes/{id}/pause`. The
server returns **202 Accepted** and does the actual state transition
`Running → Pausing → Paused` **asynchronously**.

The SDK does **not** poll for the terminal `Paused` state. So a successful
`await sandbox.pause()` only means "pause initiated".

**Implication for cubebox:** after we call `pause()`, do NOT immediately mark the row
`paused`. We have two viable options; pick one explicitly:

- **(A) Mark `pausing` and let the reconciler advance to `paused`** when
  `get_info().status.state == "Paused"`. The reconciler (already planned per spec
  OQ-3) is the source of truth.
- **(B) Poll `get_info().status.state` in a bounded loop** right after `pause()`
  returns, advancing the row when it reads `Paused` (and fail / kill on timeout).

The plan should pick one. **Recommendation: (A)** — we have a reconciler anyway, and
A keeps the pause path non-blocking.

### G2. `Sandbox.resume(...)` re-resolves endpoints and returns a NEW instance

`Sandbox.resume(sandbox_id, …)`:

1. Calls `services.resume_sandbox(id)` (HTTP 202, async).
2. Calls `services.get_sandbox_endpoint(id, DEFAULT_EXECD_PORT, …)` and the same for
   `DEFAULT_EGRESS_PORT`. **Endpoints can change** across pause/resume — this is
   intentional and documented.
3. Constructs a **new `Sandbox` instance** with fresh service adapters
   (filesystem/command/health/metrics/egress) bound to the new endpoints.
4. Calls `check_ready(resume_timeout, polling_interval)` to wait for execd health
   (default `resume_timeout=30s`, polling 200ms) — unless `skip_health_check=True`.

**Implication for cubebox:**

- The old `Sandbox` handle held by `LazySandbox` / connector cache **must be replaced**
  by the new instance returned by `Sandbox.resume(...)`. Never reuse a stored handle
  after a pause. Any cached execd/egress URL, filesystem/command/egress service must be
  re-bound to the new instance.
- `resume_timeout=30s` is the SDK default; we may need to raise this for large or cold
  sandboxes — make it a config knob (`opensandbox.resume_timeout_seconds`, default
  `30`).
- 200 ms health poll is fine.

### G3. `Resuming` state exists in the API but is missing from the local enum class

`api/lifecycle/models/sandbox_status.py` documents these states for the wire format:

> Pending / Running / Pausing / Paused / **Resuming** / Stopping / Terminated / Failed

But the local helper class `models/sandboxes.py::SandboxState` only declares constants
for `PENDING / RUNNING / PAUSING / PAUSED / STOPPING / TERMINATED / FAILED / UNKNOWN`
— **`Resuming` is NOT a constant**. So `SandboxState.PAUSED == "Paused"` works, but
there is no `SandboxState.RESUMING`.

**Implication:** when our reconciler reads `get_info().status.state`, treat it as a
string and accept `"Resuming"` (and any future unknown state) gracefully. Do not
`assert state in SandboxState.values()` — the SDK's own docstring warns that new states
may appear.

### G4. Pause/resume REST error codes — 409 means the state forbids the call

Both endpoints document these responses: `202 / 401 / 403 / 404 / 409 / 500`. A `409`
typically means the request conflicts with current state (e.g. pause on something
already `Paused`/`Pausing`, or resume on `Running`).

**Implication:** the `pause_idle()` / resume paths must be tolerant of `409`:

- `pause` on an already-`Paused` row → treat as success (reconcile the row to
  `paused`), do NOT propagate the error.
- `resume` on an already-`Running` row → likewise, reconcile and use the live handle.
- `pause` / `resume` on `Pausing` / `Resuming` → wait (or hand to reconciler) rather
  than retry tight.

### G5. `get_info().status` is the source of truth for reconciliation

`SandboxStatus` (wire) has `state: str` plus optional `reason`, `message`,
`last_transition_at`. The reconciler we agreed on (OQ-3) should:

1. Periodically scan rows in transient DB states (`pausing` / `resuming` / `running`
   with a stale `last_provider_check`).
2. Call `services.get_info(sandbox_id)` (via the existing service adapter).
3. Map provider state → DB row:
   - `Paused` → `paused`.
   - `Running` (when DB says `pausing`) → revert to `running` (pause failed).
   - `Running` (when DB says `resuming`) → finalize `running`.
   - `Failed` → mark `failed` with `reason` / `message` copied.
   - `Terminated` → terminal `terminated`.
   - `Resuming` / `Pausing` → leave as-is, check again next sweep.
4. Always update `last_provider_check`.

Recommended scan period: **30 s** (longer than the pause-API 202 → terminal latency
expected on the order of seconds). Bound staleness with `claim_timeout` already in the
plan.

### G6. TTL semantics across pause are NOT documented

`renew_sandbox_expiration(sandbox_id, new_expiration_time)` exists, but the SDK
documentation does NOT say whether the server's TTL countdown **continues during the
paused state** or **pauses with the sandbox**. The expiry semantics matter for our
`paused_ttl=24min` policy (OQ-2 default).

**Implication:** the reconciler / reaper must NOT rely on the server's own TTL
expiring paused sandboxes. We enforce `paused_ttl` from our own DB clock:

- On entry to `paused`, stamp `paused_at = now()`.
- The paused-TTL reaper kills any row where `now() - paused_at > paused_ttl`,
  regardless of what the server's TTL says.
- Open follow-up: verify empirically whether the server bills/keeps paused storage
  past the create-time TTL; either confirms our reaper is necessary or that it is also
  defensible.

### G7. Egress rules survive pause/resume — verify, do not reapply blindly

Egress rules are set server-side via `patch_egress_rules(rules)` / `network_policy=`
at create. The SDK's resume path re-resolves the egress endpoint, but does **not**
reapply rules. The expectation is the rules persist on the server. Our resume-on-reuse
should NOT blindly re-PATCH egress rules (avoid losing user state by overwriting). It
should:

- Either skip the egress patch step on resume (trust persistence), OR
- `GET` the policy first and only reconcile if it differs from what cubebox expected.

**Recommendation:** trust persistence in v1, log if a future `get_egress_policy()`
returns unexpected drift; revisit if egress drift becomes a real bug.

### G8. `pause()` does NOT close local resources; `close()`/`__aexit__` does

The SDK's `pause()` only initiates the server-side pause. The httpx transport, the
connection config, and any locally cached service adapters are **not** torn down. If
cubebox decides to drop the in-process `Sandbox` handle once paused, it must:

- Either call `await sandbox.close()` after `pause()` to free the transport,
- Or keep the handle alive (some memory cost) and reuse `resume(...)` semantics.

The plan currently treats the old handle as dead after pause; pair `pause()` with
`close()` to avoid leaking HTTP clients on the manager side.

### G9. `Sandbox.create(snapshot_id=...)` exists

The async `Sandbox.create()` accepts an optional `snapshot_id` to boot from a
persistent snapshot, and `Sandbox.create_snapshot()` produces one. This is the
"templates" path. We have explicitly **scoped this out** of #145 (per OQ-6 decision).
It is documented here only so the implementer does not "discover" it and quietly add
snapshot machinery; treat it as out of scope.

### G10. Sync and async APIs both exist

The sync mirror under `opensandbox/sync/` has identical pause/resume semantics
(`Sandbox.pause` / `Sandbox.resume` classmethod / `services/sandbox.pause_sandbox` /
`services/sandbox.resume_sandbox`). cubebox uses the **async** API (`opensandbox.*`,
not `opensandbox.sync.*`). Make sure no example or test accidentally imports from
`opensandbox.sync`.

## Open follow-ups (extend in this note as found)

- Empirically measure pause latency and resume latency on a representative sandbox; use
  to tune `idle_ttl` (default 30 min, OQ-1) and `resume_timeout`.
- Verify whether the server TTL pauses during `Paused`.
- Confirm 409 error body content so we can disambiguate "wrong state" from other
  conflicts safely.
- Confirm egress rules actually persist across pause/resume in OpenSandbox
  (not just claimed) before relying on G7.
- Confirm whether `get_info()` on a sandbox the server has GC'd returns a usable
  state or a 404 — affects reconciler's terminal-detection logic.
