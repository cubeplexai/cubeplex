# Sandbox pause/resume — design

**Date:** 2026-05-27
**Issue:** [#145](https://github.com/cubeplex/cubeplex/issues/145)
**Related:** #146 (e2b as a second provider), #144 (sandbox ownership → (workspace_id, user_id))

## Problem & motivation

A sandbox is a remote container per (user, workspace). Today the only way to stop one
consuming compute is `cleanup_expired`, which **kills** it after its idle TTL. Killing
throws away everything not on the persistent PVC: running processes, the started Neko
browser, in-memory state, anything written outside `/workspace`. The next request pays a
full cold create (image pull, pod schedule, skill re-sync) — minutes in the worst case.

We want a middle state between "running" (burning CPU/RAM) and "gone" (re-create from
scratch): **pause** a sandbox to free/freeze its compute while preserving its state, then
**resume** it back to a usable state in roughly a second, with files and session intact.
Optionally pause automatically when a sandbox goes idle, instead of killing it.

The OpenSandbox SDK already exposes `pause()` / `resume()` / `create_snapshot()`. e2b
(issue #146) exposes the same shape. We want one provider-agnostic abstraction so the
lifecycle logic is written once.

## Goals

- Add `paused` as a first-class sandbox lifecycle state, distinct from `running` and
  `terminated`.
- Provider-interface methods to pause and resume a sandbox, behind the existing `Sandbox`
  abstraction so OpenSandbox today and e2b later both satisfy it.
- On resume, return a sandbox that is immediately usable: filesystem preserved, the agent
  can `execute`/`file_read` without a re-create, egress placeholders and (if running) the
  browser live-view endpoint reconstructed.
- Replace the "kill on idle TTL" reaper behaviour with "pause on idle" as the default for
  capable providers, with a longer separate "kill paused after N days" reaper.
- Graceful capability gap: a provider that can't pause natively falls back to today's
  behaviour (keep running, or kill) without breaking the state machine.

## Non-goals

- Snapshots as a named, listable, fork-from artifact (`create_snapshot`). Pause/resume is
  per-sandbox in-place suspend; snapshot-as-template is a later feature. We note where it
  fits but don't build it here.
- Cross-node migration / cloning of a paused sandbox.
- Live-migration of an in-flight agent turn. Pause only happens between turns (idle), never
  mid-execution.
- Frontend UI for manual pause/resume. v1 is backend lifecycle only; the API surface is
  defined so a UI can be added later.
- Preserving the **open browser tabs** across resume (already not preserved across restart
  today — Chromium starts at `about:blank`; see browser deployment note).

## Current state

### Lifecycle and state model today

`UserSandbox` (`backend/cubeplex/models/user_sandbox.py`) tracks one row per
(user_id, workspace_id) with a free-text `status` column (`max_length=20`), only ever set
to `"running"` or `"terminated"`. Fields relevant here: `sandbox_id` (provider id, unique),
`status`, `image`, `volumes_config`, `last_activity_at`, `ttl_seconds`.

`UserSandboxRepository` (`backend/cubeplex/repositories/user_sandbox.py`):
- `get_active_by_user` filters `status == "running"`.
- `list_expired` / `list_expired_system` find `running` rows past
  `last_activity_at + ttl_seconds`.
- `mark_terminated` is the only state transition.

`SandboxManager` (`backend/cubeplex/sandbox/manager.py`):
- `get_or_create` — reuse the running row if `is_healthy()`, else `mark_terminated` +
  create new. Re-applies egress (`_apply_egress`) on both paths; network policy is only
  settable at create.
- `touch` / `touch_active` / `release` — bump `last_activity_at`; never kill.
- `cleanup_expired` — background reaper (`sandbox_cleanup_loop`, 60s) that **kills** every
  expired running sandbox and marks it terminated, revoking egress refs.

`LazySandbox` (`backend/cubeplex/sandbox/lazy.py`) defers create to first tool use and
transparently re-creates on failure. It calls `manager.touch` before each op.

`OpenSandbox` driver (`backend/cubeplex/sandbox/opensandbox.py`) wraps the SDK sandbox.
`get_browser_endpoint` builds the signed live-view URL from the provider proxy. There is
**no** pause/resume on the `Sandbox` base class (`backend/cubeplex/sandbox/base.py`) today.

### What survives a kill today vs. what we lose

- **Survives:** anything under `/workspace`, because that is the per-user NFS PVC
  (browser-deployment note: cookies/logins persist there).
- **Lost:** running processes, the Neko browser stack, anything outside `/workspace`,
  skill files synced to `/.skills/...` (re-synced lazily on next create).

### Provider capability — OpenSandbox SDK

See [internals note](../notes/2026-05-28-opensandbox-pause-resume-internals.md) for
documented gotchas G1–G10 (async 202 pause, endpoint re-resolution, missing `Resuming`
constant, 409 semantics, TTL semantics, egress persistence, sync vs. async APIs, etc.).

The installed SDK (`backend/.venv/.../opensandbox/sandbox.py`) already supports the full
lifecycle:
- `Sandbox.pause()` → `pause_sandbox(id)`; transitions to `Paused`, suspends all processes.
- `Sandbox.resume(sandbox_id, ...)` (classmethod) → `resume_sandbox(id)`, then
  **re-resolves the execd + egress endpoints** (which may change across pause/resume),
  rebuilds service adapters, and waits for readiness (`resume_timeout`, default 30s).
- `Sandbox.create_snapshot(name)` → persistent snapshot (not used in v1).
- `SandboxState` constants: `Pending / Running / Pausing / Paused / Stopping / Terminated /
  Failed / Unknown`, with documented transitions (Running→Pausing→Paused→Running).

The SDK's own docstring confirms the resume endpoint can change — this is exactly the
endpoint-reconstruction concern below.

## Industry / provider research

How other providers and the underlying tech implement suspend/resume:

- **e2b** (the planned second provider, #146): pause saves **both filesystem and memory**
  (running processes, loaded variables). Pause ≈ 4s per 1 GiB RAM; resume ≈ 1s. Paused
  sandboxes persist up to 30 days, then data is deleted. `connect`-by-id auto-resumes a
  paused sandbox. Auto-pause on idle is opt-in (`auto_pause=True` + `timeout`). A known e2b
  bug: a server bound to a port before pause may not be correctly listed as running after
  resume — i.e. resume restores memory but **port/process re-binding is not guaranteed
  perfect**, which is why we re-probe rather than trust state blindly.
  ([e2b persistence docs](https://e2b.dev/docs/sandbox/persistence),
  [e2b auto-pause issue #875](https://github.com/e2b-dev/e2b/issues/875),
  [e2b resume-not-persisting issue #884](https://github.com/e2b-dev/E2B/issues/884))

- **Firecracker microVM snapshots** (what e2b is built on): snapshot captures guest memory,
  vCPU, KVM and device state. Resume skips the entire Linux init/runtime startup — kernel
  resumes at the exact instruction pointer. Memory is restored via copy-on-write `MAP_PRIVATE`
  with on-demand page faulting, so resume is fast and pages load lazily. This is why "resume
  ≈ 1s" is achievable.
  ([Firecracker snapshot docs](https://github.com/firecracker-microvm/firecracker/blob/main/docs/snapshotting/snapshot-support.md),
  [page-fault handling](https://github.com/firecracker-microvm/firecracker/blob/main/docs/snapshotting/handling-page-faults-on-snapshot-resume.md))

- **CRIU (container checkpoint/restore)**: the general-purpose Linux mechanism for
  freezing a process tree to disk and restoring it. Powerful but notoriously fiddly —
  open TCP connections, external resources, and device state are the hard cases.
  ([CRIU](https://criu.org/Main_Page))

- **gVisor checkpoint/restore** (`runsc checkpoint` / `restore`): saves kernel state to an
  image dir; restore happens into a *new* container (the original stops). `--background`
  starts the app as soon as kernel state loads and streams the rest in lazily. Active TCP
  connections are a known limitation.
  ([gVisor C/R docs](https://gvisor.dev/docs/user_guide/checkpoint_restore/),
  [active-TCP issue #113](https://github.com/google/gvisor/issues/113))

- **Modal memory snapshots**: same CRIU-style approach for sub-second cold starts —
  checkpoint a warmed process, restore on demand.
  ([Modal memory snapshots](https://modal.com/blog/mem-snapshots))

**Takeaways that shape our design.** (1) Resume restores memory but the network identity
(endpoint URL, in-guest port bindings) is the unreliable part across every implementation —
so we always re-resolve endpoints and health-probe, never trust the pre-pause endpoint.
(2) Idle-auto-pause is the standard cost lever; we adopt "pause on idle" as the default.
(3) Paused state is not free forever — there is a max retention (e2b: 30 days), after which
the provider deletes it; we mirror that with a paused-TTL reaper.

## Proposed design

### Sandbox state machine

cubeplex-side states stored in `UserSandbox.status`:

```
   create
     │
     ▼
  running ──pause──▶ pausing ──▶ paused ──resume──▶ resuming ──▶ running
     │                                │                            │
     │                                └────(paused-TTL reaper)──┐   │
     └──────────────(idle-kill / explicit kill / failure)───────┴───┴──▶ terminated
```

- `running` — usable; agent can execute. The **only** state `get_or_create`'s in-use
  acquisition will hand back for direct use (after a health check). (unchanged)
- `pausing` — pause claimed, provider suspend in flight. Transient; not acquirable by
  `get_or_create` or any reaper, so a concurrent path can never use a sandbox that is being
  suspended. Reconcile if stuck.
- `paused` — compute frozen, state preserved. Not returned by `get_active_by_user` as
  directly usable, but **resumable**.
- `resuming` — resume in flight; provider re-resolving endpoints + readiness.
- `terminated` — gone; only path back is a fresh create. (unchanged)
- `failed` — provider reported a critical error; treated like terminated for reuse but
  kept distinct for diagnostics.

`pausing` and `resuming` are short-lived "in-flight" guards so two concurrent requests
don't both drive a transition. They map to the provider's `Pausing` / (implicit) resuming
states. We persist them so a crash mid-transition is reconcilable by the reaper.

Critically, the pause flow **claims `pausing` before** it calls the provider, not after.
The reaper does one atomic `running` + unleased → `pausing` UPDATE (conditioned on the
current row still being `running` with no live lease) and only proceeds to call the provider
`pause()` if that UPDATE changed a row. So the row never sits in `running` while the provider
suspend is in flight: by the time the (slow) `connect → pause()` runs, the row already reads
`pausing`, which acquisition refuses. This closes the window where a concurrent tool call or
a second reaper could see a still-`running` row and acquire/use the sandbox mid-suspend.

Pause only happens between agent turns. The manager never pauses a sandbox with an
in-flight operation — and this guarantee cannot rest on `last_activity_at` alone. Today
`LazySandbox` refreshes activity *before* a tool call, so a single `execute` or browser
startup that runs longer than `ttl_seconds` would leave `last_activity_at` stale while the
op is still running, and the idle reaper could select and pause a live operation. We add an
explicit **in-use guard**: an `in_use_until` lease column (see DB fields) that an operation
sets when it acquires the sandbox and clears (or lets expire) when it finishes. The idle
reaper requires *both* `last_activity_at + ttl_seconds` in the past *and* no active lease
(`in_use_until` null or in the past). So eligibility is "idle by clock AND not in-flight",
not just timestamp freshness.

### DB fields (UserSandbox)

Add to `backend/cubeplex/models/user_sandbox.py` (migration via
`alembic revision --autogenerate`):

- `status` — widen the accepted set to the states above; column stays `str(20)`.
- `paused_at: datetime | None` — when the sandbox entered `paused`. Drives the paused-TTL
  reaper.
- `paused_ttl_seconds: int` — how long a paused sandbox may sit before it is killed
  (default `24 * 60` = 24 minutes per OQ-2; well under e2b's 30-day ceiling). Separate
  from the idle `ttl_seconds` (default `30 * 60` per OQ-1).
- `provider: str` — which driver owns this row (`"opensandbox"` today, `"e2b"` later).
  Lets a mixed fleet be reaped correctly and is needed once #146 lands. Default
  `"opensandbox"`.
- `last_resumed_at: datetime | None` — diagnostics / metrics on resume frequency.
- `in_use_until: datetime | None` — the in-use lease. An operation that holds the sandbox
  (an agent turn, a long `execute`, browser startup) sets this to `now + lease_window` when
  it acquires the sandbox and clears it (or lets it expire) on completion. A live lease
  (`in_use_until` in the future) makes the row ineligible for idle pause regardless of
  `last_activity_at`. Long ops renew the lease so it outlives them; a TTL on the lease means
  a crashed holder can't pin a sandbox forever.

Keep `(workspace_id, user_id)` ownership (#144) intact: all new queries stay scoped by
`OrgScopedMixin` + the `user_id` filter; no transition crosses the (org, workspace) boundary.

Repository additions (`UserSandboxRepository`):
- `claim_pausing(id)` — the atomic pause claim: a single conditional UPDATE that sets
  `status = 'pausing'` only `WHERE id = :id AND status = 'running' AND (in_use_until IS NULL
  OR in_use_until < now()) AND last_activity_at + ttl_seconds <= now()`. The idleness term is
  re-asserted here (not just at selection) so a concurrent keepalive/touch that refreshes
  `last_activity_at` between selection and claim makes the claim a no-op. Returns whether a row
  was claimed. The reaper calls the provider
  `pause()` **only** on a successful claim, so the row is already `pausing` (and thus
  unacquirable) before the suspend starts. This replaces a plain `mark_pausing` that would
  have asserted-then-set in two steps; the claim must be one atomic statement so two reapers
  can't both win.
- `mark_paused(paused_at)` (on success) / `mark_running` (revert on failure, from `pausing`)
  / `mark_resuming` / `mark_running` — explicit transitions, each asserting the prior state to
  avoid illegal jumps.
- `get_resumable_by_user(user_id)` — returns a `paused` (or `running`) row for reuse. Never
  returns `pausing` / `resuming` rows — a mid-transition row is not handed out.
- `get_active_by_user` (existing) tightens to `status == 'running'` only; it must **not**
  match `pausing`, so the in-use acquisition path can't pick up a suspending sandbox.
- `list_idle_to_pause_system` — running rows past idle `ttl_seconds` **and** with no active
  in-use lease (`in_use_until` null or in the past). Selection only finds *candidates*; the
  reaper still re-claims each one via `claim_pausing` before pausing, so a candidate that was
  acquired, touched (re-activated), or already claimed between selection and claim is safely
  skipped. Replaces the kill
  selection for capable providers. The lease check is part of the WHERE clause so an in-flight
  op is never selected.
- `acquire_in_use(id, lease_window)` / `release_in_use(id)` — set / clear the `in_use_until`
  lease around an operation that holds the sandbox.
- `list_paused_expired_system` — paused rows past `paused_at + paused_ttl_seconds` (the new
  hard-kill reaper).

### Provider-interface methods

On `Sandbox` (`backend/cubeplex/sandbox/base.py`), add capability-gated lifecycle methods.
Keep them on the abstraction so the manager never imports a concrete driver:

- `supports_pause() -> bool` — default `False`; OpenSandbox returns `True`. Lets the
  manager pick "pause on idle" vs. "kill on idle" per provider.
- `async pause() -> None` — suspend; default raises `NotImplementedError`. OpenSandbox
  delegates to the SDK `pause()`.
- `connect_or_resume` is special: it produces a **new** live handle (the SDK `resume` is a
  classmethod that rebuilds adapters; the e2b `connect` likewise auto-resumes a paused
  sandbox). So this lives on the **manager**, not on a dead `Sandbox` instance — the
  manager calls the driver's `connect_or_resume` factory and gets back a fresh `Sandbox`.
  The base class exposes a driver-level
  `classmethod async connect_or_resume(sandbox_id, *, conn_config, ...) -> Sandbox`.
  OpenSandbox implements it by calling `opensandbox.Sandbox.resume(...)` server-side then
  connecting; e2b will implement it by calling its `connect` (which auto-resumes). The
  unified shape means #146 doesn't reshape the abstraction.

`SandboxManager` changes:
- `get_or_create` reuse path: if the DB row is `paused`, **resume** it (mark `resuming` →
  driver `connect_or_resume` → re-apply egress → `mark_running` + `last_resumed_at`) instead of
  treating it as missing. If `running`, behave as today (connect + health-check). If
  resume fails, fall through to create-new (and mark the old row `terminated`/`failed`).
- New `pause(user_id, ...)` / a reaper entry point `pause_idle()` mirroring
  `cleanup_expired`: for capable providers, per candidate row, `claim_pausing` (atomic
  `running` + unleased → `pausing`) → connect → `pause()` → `mark_paused`. The claim happens
  **before** the provider call, so the row is already `pausing` (unacquirable) for the whole
  suspend. If the claim doesn't change a row (already acquired, or another reaper won), skip
  it. If `pause()` raises, revert `pausing → running` via `mark_running` (then fall back to
  kill per "capability-gap handling") so a failed suspend never strands the row. Egress refs
  are **kept** (the sandbox will be resumed and reuse them), but their expiry is not extended
  while paused — on resume we re-apply egress and refresh them.
- New `reap_paused()`: kill paused rows past their paused-TTL (real `kill()` + revoke
  egress + `mark_terminated`), bounding stored state.

### What is guaranteed preserved across pause→resume

- **Filesystem**: fully preserved. (Already guaranteed for `/workspace` via the PVC; native
  pause additionally preserves the rest of the container FS.)
- **Memory / processes**: preserved by the provider's native suspend (OpenSandbox `Paused`
  state suspends all processes; e2b restores memory). We do **not** rely on this for
  correctness — see "endpoint reconstruction" — but it is the fast path.
- **Skill sync state**: the in-memory `_synced_skill_version_ids` set lives on the
  `Sandbox` object, which is rebuilt on resume → the resumed handle re-syncs skills lazily
  on first use (acceptable; idempotent and cheap). Files already on `/workspace`/`/.skills`
  persist regardless.
- **Not guaranteed**: in-guest port bindings of long-running servers (e2b #884/#1031 show
  this is flaky across providers), and open browser tabs (Chromium restarts at
  `about:blank`). The browser **profile** (cookies/logins under `/workspace`) persists.

### Endpoint / proxy reconstruction on resume

This is the load-bearing part. Across pause/resume the provider may hand back **different**
endpoint addresses (the SDK `resume` docstring says so explicitly, and re-resolves execd +
egress endpoints itself). So:

1. **Never reuse a pre-pause `Sandbox` handle.** Resume always goes through the driver's
   `connect_or_resume` factory, which rebuilds the execd/egress/health/metrics adapters
   against the freshly-resolved endpoints. The manager discards the old handle.
2. **Egress re-applied on resume.** `_apply_egress` already runs on the reuse path; the
   resume path calls it too — revoke-then-add fresh `EgressRef`s and re-`set_run_env`, so
   placeholders are valid for a new TTL window. Network policy is structural and set at
   create; pause/resume does not change it, so it survives.
3. **Browser live-view rebuilt lazily.** `get_browser_endpoint` mints a fresh signed URL on
   demand; nothing about pause/resume is cached there. But the Neko process is suspended by
   pause and `start-browser.sh` is idempotent — so the live-view flow calls `start_browser()`
   again after resume (it already does on first request), and the panel reconnects. The
   per-endpoint access-mode blocker (issue #949,
   `docs/dev/notes/2026-05-27-opensandbox-issue-949-endpoint-mode.md`) is **unchanged** by
   this feature — pause/resume neither helps nor worsens it.
4. **Health-probe after resume.** Use the SDK's `check_ready` (bounded by a `resume_timeout`
   config knob, default ~30s). If readiness fails, fall back to create-new.

### Optional idle auto-suspend

Replace the idle reaper's behaviour by capability:

- **Provider supports pause** → `pause_idle()` runs in the existing `sandbox_cleanup_loop`
  (or a sibling loop): select running rows that are past `ttl_seconds` idle **and** carry no
  active in-use lease (`in_use_until` null or in the past), then for each one `claim_pausing`
  (atomic `running` → `pausing`) before the provider `pause()`, and `mark_paused` with
  `paused_at = now` on success. Because the row is `pausing` for the whole suspend, no
  concurrent acquire can use it mid-pause. Compute is freed; state preserved.
- **Provider can't pause** → keep today's `cleanup_expired` kill behaviour.
- A second, slower pass `reap_paused()` kills paused rows past `paused_ttl_seconds` so paused
  state doesn't accumulate forever (mirrors e2b's 30-day deletion).

Config knobs (under `sandbox.*`, consistent with existing ones):
- `sandbox.pause_on_idle: bool` (default `True` where supported) — pause vs. kill on idle.
- `sandbox.idle_ttl_seconds` — `30 * 60` (30 minutes, OQ-1).
- `sandbox.paused_ttl_seconds` — `24 * 60` (24 minutes, OQ-2).
- `sandbox.lease_seconds` — `5 * 60` (5 minutes, OQ-7); managed by `LazySandbox` around
  long ops.
- `sandbox.resume_timeout` (default `30`).

Touch semantics unchanged for *freshness*, but freshness is no longer the only safety net.
An active agent turn or open browser panel keeps `last_activity_at` fresh **and** holds an
in-use lease (`in_use_until`) for the duration of the operation. The idle pass selects only
rows that are both stale by clock and unleased, so a long-running op — even one that outlives
`ttl_seconds` between touches — is never paused mid-flight.

### Capability-gap handling

The whole design is gated on `Sandbox.supports_pause()`:

- The manager asks the driver before choosing pause vs. kill, and before attempting resume.
- A driver with no native pause (`LocalSandbox`, a future minimal provider) returns `False`;
  its rows never enter `paused`, and the idle reaper kills as today. No code path assumes
  pause exists.
- If `pause()` raises at runtime, the reaper reverts the row `pausing → running`
  (`mark_running`), logs, and **falls back to kill** for that row (don't leave a sandbox
  half-transitioned). If `connect_or_resume` raises, `get_or_create` falls back to
  create-new. Both keep the state machine consistent.

### v1 scope

- Add `paused` / `pausing` / `resuming` / `failed` states + the new `UserSandbox` columns
  and repo transitions (autogen migration).
- `Sandbox.supports_pause()` / `pause()` / `connect_or_resume` on the base class;
  OpenSandbox implements all three via the SDK; `LocalSandbox` and `LazySandbox`
  forward/no-op.
- `SandboxManager`: resume-on-reuse, `pause_idle()`, `reap_paused()`; wire both into the
  cleanup loop; egress re-applied on resume.
- Config knobs above.
- **Out of v1:** named snapshots/templates, manual pause/resume API + UI, cross-node
  migration, e2b driver (#146 — but the interface is shaped to fit it).

## Testing strategy (E2E-first)

E2E against a real OpenSandbox (the only way to validate that pause actually frees compute,
resume re-resolves endpoints, and the filesystem survives):

1. **Pause/resume round-trip**: create sandbox → write a file under `/workspace` and one
   under `/tmp` (outside the PVC) → pause → assert DB row `paused` + provider reports
   `Paused` → resume via `get_or_create` → assert both files readable and the agent can
   `execute` again. The `/tmp` file proves native pause preserves more than the PVC.
2. **Endpoint reconstruction**: after resume, run a command (proves execd re-resolved) and,
   with the browser skill, request the live-view endpoint and confirm it reconnects
   (proves browser + egress rebuild).
3. **Idle auto-pause**: short `ttl_seconds`, run `pause_idle()`, assert transition to
   `paused` (not `terminated`); confirm a still-active (recently touched) sandbox is *not*
   paused.
4. **Paused-TTL reap**: short `paused_ttl`, run `reap_paused()`, assert `terminated` +
   egress refs revoked.
5. **Capability gap**: a fake/non-pausing driver (or `LocalSandbox`) — idle reaper kills,
   never enters `paused`; resume path falls back to create.

Unit tests for the repository transition guards (illegal jumps rejected) and manager
fallback logic (pause-raises → kill; resume-raises → create-new). Concurrency: two
overlapping `get_or_create` on a paused row must resume exactly once (lock / `resuming`
guard) — covered by a unit/integration test on the manager. Also: a `claim_pausing` race —
two reapers (or a reaper + an in-flight `get_or_create`) target the same `running` row; only
one claim wins (`running → pausing`), the loser sees no row changed and skips, and no path
hands out a `pausing` row for use.

Run worktree tests on the per-slot DB (`uv run pytest`, ports from `.worktree.env`).

## Open questions

1. **Pause cost vs. idle TTL tuning.** e2b pause is ~4s/GiB; OpenSandbox pause cost is
   unmeasured. If pause itself is expensive, a too-short idle TTL could thrash
   (pause→resume→pause). Need a real measurement before picking default `ttl_seconds` for
   the pause path. Should there be a minimum running-time before a sandbox is eligible to
   pause?
   **Resolved 2026-05-28: default idle TTL = 30 minutes, configurable in the config file
   (`sandbox.idle_ttl_seconds`, default `30 * 60`). No separate "min-runtime" knob — the
   idle TTL itself is the thrash guard; an extra minimum-runtime gate adds a knob that
   doesn't earn its keep.**
2. **Does OpenSandbox bill paused sandboxes** (storage for the frozen memory/FS)? That sets
   the right `paused_ttl` default and whether paused-but-idle should still eventually kill
   aggressively.
   **Resolved 2026-05-28: default paused TTL = 24 minutes (note: minutes, not hours),
   configurable as `sandbox.paused_ttl_seconds` (default `24 * 60`). We can't yet
   confirm whether the provider bills paused storage; the short default is deliberately
   conservative until we measure.**
3. **Mid-transition crash recovery.** If the backend dies during `pausing`/`resuming`, the
   DB row is stuck in a transient state. Do we add a reconciler that queries the provider's
   actual state (`get_info().status`) and repairs the row, or just time-box transients and
   kill? Provider is the source of truth — lean toward a reconcile pass.
   **Resolved 2026-05-28: add a reconciler (see plan Task 5b reconciler). Periodically
   read provider `get_info().status` and repair stuck `pausing`/`resuming` rows. Scan
   period 30s, bounded by `claim_timeout`. Provider is the source of truth.**
4. **Resume-then-immediately-pause races** with the open browser panel keepalive: if a panel
   reopens just as the idle reaper pauses, do we cancel the pause or resume right after?
   Probably: keepalive touch wins (won't be selected), but the window needs a guard.
   **Resolved 2026-05-28: already handled by the atomic `claim_pausing` UPDATE that
   re-asserts idleness (and the lease) in its WHERE clause. A keepalive touch landing
   between selection and claim makes the claim a no-op; no extra guard needed.**
5. **e2b mapping (#146).** e2b's `connect`-auto-resumes model differs from OpenSandbox's
   explicit `resume_by_id`. Does the `resume_by_id` interface cleanly cover both, or do we
   need a `connect_or_resume` shape? Decide when #146 starts so the abstraction isn't
   reshaped twice.
   **Resolved 2026-05-28: unify under `connect_or_resume(sandbox_id)` on the provider
   abstraction. OpenSandbox impl calls the SDK's `Sandbox.resume(...)` classmethod
   server-side then connects; e2b impl calls `connect` (which auto-resumes). The
   provider-abstraction method is named `connect_or_resume` from the start so #146
   doesn't reshape it.**
6. **Snapshots (`create_snapshot`).** Out of scope here, but should paused-then-reaped
   sandboxes optionally snapshot-on-kill so a long-idle user can still get state back? Or is
   that a separate "templates" feature entirely?
   **Resolved 2026-05-28: NOT in scope for v1. Snapshot-as-template is a distinct future
   feature; v1 ships pause/resume only.**
7. **In-use lease window vs. renewal.** What lease window balances "long op never gets
   paused" against "crashed holder can't pin a sandbox forever"? A short window forces
   renewal mid-op (a heartbeat) while a long one delays reclaim after a crash. Decide the
   default window, the renewal cadence, and where it lives (`LazySandbox` wrapper vs. the
   tool-execution boundary) when implementing — and confirm the renewal point sees every
   long op (`execute`, browser startup, file transfer), not just agent-turn boundaries.
   **Resolved 2026-05-28: default lease window = 5 minutes (`sandbox.lease_seconds`,
   default `5 * 60`), managed at the `LazySandbox` layer. Acquire when entering a long
   operation, renew during, release on completion. Boundary is the long-operation
   boundary (`execute`, browser start, file transfer) — NOT agent-turn boundaries — so
   every long op is covered.**

## References

- OpenSandbox SDK lifecycle: `pause()`, `resume()`, `create_snapshot()`, `SandboxState`
  (installed at `backend/.venv/lib/python3.13/site-packages/opensandbox/sandbox.py`,
  `.../models/sandboxes.py`).
- Endpoint reconstruction concern: `docs/dev/notes/2026-05-27-opensandbox-issue-949-endpoint-mode.md`.
- Browser deployment / profile persistence: `docs/dev/notes/2026-05-22-sandbox-browser-deployment.md`.
- Current lifecycle code: `backend/cubeplex/sandbox/{base,opensandbox,manager,lazy,cleanup}.py`,
  `backend/cubeplex/models/user_sandbox.py`, `backend/cubeplex/repositories/user_sandbox.py`.
- [e2b persistence docs](https://e2b.dev/docs/sandbox/persistence) ·
  [e2b auto-pause #875](https://github.com/e2b-dev/e2b/issues/875) ·
  [e2b resume-not-persisting #884](https://github.com/e2b-dev/E2B/issues/884) ·
  [e2b process-not-killed #1031](https://github.com/e2b-dev/e2b/issues/1031)
- [Firecracker snapshot support](https://github.com/firecracker-microvm/firecracker/blob/main/docs/snapshotting/snapshot-support.md) ·
  [Firecracker page-fault handling on resume](https://github.com/firecracker-microvm/firecracker/blob/main/docs/snapshotting/handling-page-faults-on-snapshot-resume.md)
- [gVisor checkpoint/restore](https://gvisor.dev/docs/user_guide/checkpoint_restore/) ·
  [gVisor active-TCP limitation #113](https://github.com/google/gvisor/issues/113)
- [CRIU](https://criu.org/Main_Page) · [Modal memory snapshots](https://modal.com/blog/mem-snapshots)
