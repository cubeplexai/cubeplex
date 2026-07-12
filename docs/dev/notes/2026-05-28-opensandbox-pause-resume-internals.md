# OpenSandbox Pause/Resume — SDK internals & gotchas

Read of the installed `opensandbox` SDK (`backend/.venv/.../opensandbox`) plus
**empirical probes against `39.99.248.80:18080` (the OpenSandbox the dev env
points at, per `backend/.env`)** to ground the `#145` sandbox pause/resume
implementation. This note captures the **traps** the implementer must respect.
**Extend this file as new findings surface during implementation.**

SDK files referenced (paths relative to installed SDK):

- `sandbox.py` — `Sandbox.pause()` (instance), `Sandbox.resume()` (classmethod),
  `Sandbox.connect()`, `Sandbox.create()`.
- `services/sandbox.py` — `Sandboxes` protocol. Note: protocol method is
  `get_sandbox_info`; `Sandbox.get_info()` is the instance convenience wrapper.
- `adapters/sandboxes_adapter.py` — concrete HTTP impl.
- `api/lifecycle/api/sandboxes/post_sandboxes_sandbox_id_{pause,resume}.py` —
  raw REST; documented codes 202 / 401 / 403 / 404 / 409 / 500.
- `api/lifecycle/models/sandbox_status.py` — wire `SandboxStatus`: `state: str`
  + optional `reason`, `message`, `last_transition_at`.
- `models/sandboxes.py::SandboxState` — local string constants.
- `manager.py` — wraps `Sandbox.{pause,resume}_sandbox`; cubeplex uses
  `Sandbox.pause()` / `Sandbox.resume(id)` directly, not `Manager`.
- Sync mirror under `opensandbox/sync/…` — cubeplex uses async only.

## Gotchas to respect in the cubeplex implementation

### G1. ✅ Pause is asynchronous on the server (HTTP 202 Accepted)

`Sandbox.pause()` returns 202 with empty body. Wire docstring: "Poll GET
/sandboxes/{sandboxId} to track state transition through Pausing and eventually
Paused." The SDK does not poll. So `await sandbox.pause()` only means "pause
initiated."

**Implication:** mark the row `pausing` and let the reconciler advance it to
`paused` when `get_info().status.state == "Paused"`. Do NOT mark `paused`
synchronously.

### G2. ✅ `Sandbox.resume(...)` re-resolves endpoints and returns a NEW instance

`Sandbox.resume(id)`:

1. POST `/sandboxes/{id}/resume` (202, async).
2. Re-resolves execd + egress endpoints — they CAN change across pause/resume.
3. Builds a **new `Sandbox` instance** with fresh service adapters.
4. Optionally calls `check_ready(resume_timeout=30s, polling=200ms)` unless
   `skip_health_check=True`.

**Implication:** any cached `Sandbox` handle is dead after pause. Replace with
the new instance from `Sandbox.resume(...)`. Expose `resume_timeout` as a config
knob (`opensandbox.resume_timeout_seconds`, default 30). If the sandbox is
already Running, the server returns 409 (see G4) — treat as no-op.

### G3. 🟡 `Resuming` state exists in the wire format but not the local constants class

`api/lifecycle/models/sandbox_status.py` docstring documents these wire states:

> Pending / Running / Pausing / Paused / **Resuming** / Stopping / Terminated /
> Failed

But the local helper class `models/sandboxes.py::SandboxState` only declares
constants for `PENDING / RUNNING / PAUSING / PAUSED / STOPPING / TERMINATED /
FAILED / UNKNOWN` — **`RESUMING` is NOT a constant**. So `SandboxState.PAUSED
== "Paused"` works, but there is no `SandboxState.RESUMING`.

**Refinement (from empirical probe):** the real server emits **additional**
states beyond the SDK-documented list. We observed:

- `"Succeed"` — emitted when a sandbox completes (likely tail-of-entrypoint exit
  on a Kubernetes Pod backend). This is NOT in either the wire docstring or the
  `SandboxState` constants.
- Error code prefix `KUBERNETES::` in 409/404 bodies (`KUBERNETES::INVALID_STATE`,
  `KUBERNETES::SANDBOX_NOT_FOUND`).

**Implication:** when our reconciler reads `get_info().status.state`, treat it
as a free-form string. Accept `"Resuming"`, `"Succeed"`, and any future unknown
state gracefully. Do **not** `assert state in SandboxState.values()`. Map
non-Running terminal-ish states (`Succeed`, `Terminated`, `Failed`) to the same
"sandbox is gone, mark row terminated/failed and unlink" branch.

### G4. ✅ Pause/resume REST error codes — 409 means the state forbids the call

Documented responses (both endpoints): `202 / 401 / 403 / 404 / 409 / 500`.

**Empirical 409 body shape (resume called on a non-Paused sandbox):**

```json
{
  "code": "KUBERNETES::INVALID_STATE",
  "message": "Cannot resume sandbox in phase Succeed, expected Paused"
}
```

**Empirical 404 body shape (get on missing id):**

```json
{
  "code": "KUBERNETES::SANDBOX_NOT_FOUND",
  "message": "Sandbox '00000000-…' not found"
}
```

The SDK wraps these into `SandboxApiException` (a subclass of `SandboxException`)
with the message embedded — we **don't** get a structured `code` back from the
SDK exception. To distinguish "wrong state" from other failures, cubeplex can
match the substring `INVALID_STATE` / `SANDBOX_NOT_FOUND` in the exception's
`.message`, or just treat both as benign (already-paused / already-gone).

**Implication:** the `pause_idle()` / resume paths must be tolerant of 409 and 404:

- `pause` on an already-`Paused` row → treat as success (reconcile the row to
  `paused`), do NOT propagate.
- `resume` on an already-`Running` row → reconcile to `running` and reuse the
  live handle (need fresh `Sandbox.connect(...)`).
- 404 on either call → sandbox is gone; mark row `terminated` and unlink.

**Empirical surprise (see G11 below):** the OpenSandbox we probed returns
**202 for both pause attempts** (idempotent) even when state is `Running` —
i.e. you cannot rely on 409 to detect "already paused", because this server
accepts the call and silently ignores. The reconciler is the only reliable
truth.

### G5. ✅ `get_info().status` is the source of truth for reconciliation

Reconciler should periodically (~30 s) scan transient DB states (`pausing` /
`resuming` / stale `running`), call `services.get_sandbox_info(sandbox_id)`,
and map provider state → DB row:

- `Paused` → `paused`
- `Running` after `pausing` → revert to `running` (pause failed / no-op, see G11)
- `Running` after `resuming` → finalize `running`
- `Failed` / `Succeed` / `Terminated` / `Stopping` → `terminated` / `failed`
- `Resuming` / `Pausing` → leave as-is, recheck next sweep
- Always update `last_provider_check`

**Hard timeout** on `pausing` state required (recommend 2 min) — see G11.

### G6. 🟡 TTL semantics across pause — empirically the TTL keeps counting

`renew_sandbox_expiration(sandbox_id, new_expiration_time)` exists.

**Empirical observation:** `expires_at` in `get_info()` did **not** change
across pause attempts (it stayed at the same UTC timestamp set at create
time). The TTL countdown therefore appears to run on the provisioning wall
clock, not on cpu-time, and is **not** automatically extended by pause.

A 5-min-TTL sandbox transitioned to `Succeed` (terminal) within ~5 minutes of
creation **regardless of pause attempts**. So the server-side TTL is *not*
paused-aware.

**Implication:** cubeplex MUST:

- Enforce our `paused_ttl` from our own DB clock (`paused_at` stamp + reaper).
- On pause, optionally also call `renew_sandbox_expiration` to extend the
  server-side TTL to cover our `paused_ttl` window — otherwise the server can
  kill the paused sandbox out from under us before our reaper runs.
- The reaper kills any row where `now() - paused_at > paused_ttl` regardless
  of what the server's TTL says.

### G7. ✅ Egress rules survive pause/resume — verify, do not reapply blindly

Egress rules are set server-side via `patch_egress_rules(rules)` /
`network_policy=` at create. `Sandbox.resume(...)` re-resolves the egress
endpoint, but does **not** reapply rules. Expectation: rules persist on the
server. Our resume-on-reuse should NOT blindly re-PATCH egress rules.

**Recommendation:** trust persistence in v1, log if a future
`get_egress_policy()` returns unexpected drift. (We could not verify
empirically because the v1 test image had no custom egress rules — revisit
when we adopt one.)

### G8. ✅ `pause()` does NOT close local resources; `close()`/`__aexit__` does

`Sandbox.pause()` only initiates the server-side pause. The httpx transport,
the connection config, and any locally cached service adapters are **not**
torn down (verified by reading `sandbox.py:313-324` and `close():342-361`).
If cubeplex drops the in-process `Sandbox` handle once paused, it must:

- Either call `await sandbox.close()` after `pause()` to free the transport,
- Or keep the handle alive and reuse `resume(...)` semantics (which returns a
  brand-new instance regardless — see G2 — so the old handle is dead anyway).

Pair `pause()` with `close()` to avoid leaking HTTP clients.

### G9. ✅ `Sandbox.create(snapshot_id=...)` exists

`Sandbox.create()` accepts an optional `snapshot_id` to boot from a persistent
snapshot, and `Sandbox.create_snapshot()` produces one. This is the "templates"
path. We have explicitly **scoped this out** of #145 (per OQ-6 decision).

### G10. ✅ Sync and async APIs both exist

Sync mirror under `opensandbox/sync/`. cubeplex uses the **async** API
(`opensandbox.Sandbox` from `opensandbox/sandbox.py`). Don't accidentally import
from `opensandbox.sync`.

### G11. 🔴 (NEW) The probed OpenSandbox silently no-ops pause for our default image

**Empirical** (`39.99.248.80:18080` with image
`hub.sensedeal.vip/library/cubeplex-sandbox:24.04-20260525`):

- `POST /sandboxes/{id}/pause` returns 202 with empty body — as documented.
- The sandbox status stays at `Running` for the full 90 s polling window —
  it **never reaches `Paused`**.
- A second `POST /sandboxes/{id}/pause` also returns 202 (idempotent, no 409).
- A subsequent `POST /sandboxes/{id}/resume` on the still-Running sandbox
  returns 409 with body `KUBERNETES::INVALID_STATE` and message
  "Cannot resume sandbox in phase Succeed, expected Paused".

**Interpretation:** the Kubernetes backend of this OpenSandbox build does not
support pause for this Pod-based runtime. The server accepts the call to keep
the API contract but never actually pauses.

**Implication for #145 — important:**

- Do **not** assume pause will succeed on every deployment. The reconciler must
  treat "pausing → still Running after `pause_grace_seconds`" as a soft failure
  and fall back to `kill`.
- Add a config flag `opensandbox.pause_supported` (default `true`) so operators
  can disable pause entirely on backends that don't support it. When disabled,
  `pause_idle()` becomes a kill.
- The E2E test for Task 7 cannot rely on the live OpenSandbox the dev env
  points at — it must either (a) target a backend known to support pause, or
  (b) use the SDK-level fake we already have in tests, or (c) be marked as
  "live-pause-capable required" and skipped in CI unless an env var is set.
- The `pause_grace_seconds` config knob should default to ~120 s, after which
  the reconciler kills.

## Open follow-ups (empirical findings appended 2026-05-28)

OpenSandbox endpoint: `http://39.99.248.80:18080`, API key from
`backend/.env CUBEPLEX_SANDBOX__API_KEY`. Reachable (HTTP 200 on
`GET /sandboxes`).

### Pause/resume latency (measured)

| Step | Latency |
|---|---|
| `Sandbox.create()` (skip_health_check=True, image preheated) | ~10 s |
| `POST /sandboxes/{id}/pause` HTTP round-trip | ~75 ms |
| `pause → Paused` terminal state | **NEVER reached in 90 s** (this deployment) |
| `POST /sandboxes/{id}/resume` HTTP round-trip on a non-Paused sandbox | 409 immediately |

### Paused TTL counting

`expires_at` from `get_info()` does **not** change across pause attempts.
The server-side TTL keeps running on wall-clock time. We **must** either
call `renew_sandbox_expiration` on pause or accept that the server may
terminate the sandbox out from under us before our `paused_ttl` elapses.
See G6.

### 409 body shape

Confirmed:

```json
{"code":"KUBERNETES::INVALID_STATE",
 "message":"Cannot resume sandbox in phase Succeed, expected Paused"}
```

Codes seen: `KUBERNETES::INVALID_STATE`, `KUBERNETES::SANDBOX_NOT_FOUND`.

### Egress persistence

Not empirically verified — the test image had no custom egress rules.
Revisit when we adopt a workspace egress policy. See G7.

### 404 on GC'd sandbox

After `kill()`, `get_info()` raises `SandboxApiException` with message
`Sandbox '<id>' not found` (HTTP 404, body `KUBERNETES::SANDBOX_NOT_FOUND`).
The reconciler should map this exception to "row is `terminated`, unlink."

### The big one: pause is a no-op on this deployment

See G11. This is the most important finding for the plan: do not assume
pause works on every backend. The reconciler must time-out the `pausing`
state and the plan must include a `pause_supported` config flag.
