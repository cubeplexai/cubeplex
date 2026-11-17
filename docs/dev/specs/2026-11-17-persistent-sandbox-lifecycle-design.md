# Persistent Sandbox Lifecycle & Volume Cleanup — Design

- **Status:** Draft
- **Date:** 2026-11-17
- **Branch / worktree:** `feat/2026-11-17-persistent-sandbox-lifecycle` / `.worktrees/feat/2026-11-17-persistent-sandbox-lifecycle`

## 1. Goal

Preserve every sandbox's system root filesystem (RootFS) and `/workspace` storage permanently across idle periods and manual restarts, while destroying both the remote sandbox instance and its persistent volumes when a sandbox, workspace, or user account is deleted.

## 2. Context

Even after switching `sandbox.provider` to `cubepod` (which mounts both a per-instance `<sandboxID>-rootfs-pvc` OverlayFS upper layer and a deterministic `<volumeKey>-workspace-pvc`), system-disk changes inside a sandbox are still lost after 30 minutes of inactivity or a manual restart, and persistent volumes are leaked on deletion:

1. **Idle sandboxes are terminated instead of paused (`config.yaml` & `sandbox/manager.py`):**
   - `sandbox.pause_on_idle` defaults to `false` in `backend/config.yaml` (left over from OpenSandbox v1.0.12 before real disk-backed suspend/resume).
   - Even when `pause_on_idle` is set to `true`, `sandbox_cleanup_loop` (`sandbox/cleanup.py`) runs `cleanup_expired()` unconditionally before `pause_idle()`. Because `cleanup_expired()` kills every `running` row past `ttl_seconds` (`1800s`, equal to `idle_ttl_seconds`), `cleanup_expired()` destroys idle `running` sandboxes before `pause_idle()` ever runs.
   - In E2B / CubePod semantics, `pause` (`POST /sandboxes/{id}/pause`) deletes the compute Pod (`0` CPU / `0` RAM) while keeping both `rootfs-pvc` and `workspace-pvc` intact for `connect` (`resume`). By contrast, `terminate` (`DELETE /sandboxes/{id}?deleteVolumes=false`) permanently deletes `<sandboxID>-rootfs-pvc` and keeps only `<volumeKey>-workspace-pvc`, resetting the OS layer on next `create`.
2. **Paused sandboxes are still auto-killed by `reap_paused()`:**
   - `sandbox.paused_ttl_seconds` defaults to `86400` in `config.yaml`, `UserSandbox.paused_ttl_seconds` defaults to `1440` (24 minutes) on the SQLModel field, and `UserSandboxRepository.reserve()` never passes `self._paused_ttl_seconds` when creating rows. Any paused sandbox is therefore hard-killed (`terminate`) after 24 minutes (or 24 hours), wiping its RootFS.
3. **Manual Restart (`POST /api/v1/ws/{ws}/sandboxes/{id}/restart`) calls `terminate`:**
   - `SandboxManager.restart_user_sandbox()` routes `action="restart"` through `_kill_record()`, which calls `provider.terminate(sandbox_id)`. On CubePod, this destroys `<sandboxID>-rootfs-pvc` instead of stopping the guest Pod via `pause` so the next run wakes a fresh Pod with both RootFS and `/workspace` preserved (wiping RootFS already has its own explicit `POST /api/v1/ws/{ws}/sandbox/reset-rootfs` endpoint).
4. **Workspace, sandbox, and user deletions leak provider instances and volumes:**
   - `SandboxProvider.terminate(sandbox_id)` has no `delete_volumes` parameter; `CubePodProvider.terminate()` hardcodes `delete_volumes=False`, leaving `<volumeKey>-workspace-pvc` orphaned even on explicit `DELETE /api/v1/ws/{ws}/sandboxes/{id}`.
   - `DELETE /api/v1/workspaces/{workspace_id}` (`api/routes/v1/workspaces.py`) and `DELETE /api/v1/auth/me` (`api/routes/v1/auth.py`) delete `UserSandbox` rows directly from PostgreSQL without calling `SandboxManager` at all (assuming `cleanup_expired()` would reap them later — which neither happens once DB rows are gone nor reclaims paused sandboxes or PVCs), and fail to clear the circular foreign key between `user_sandboxes.last_skill_sync_event_id` and `user_sandbox_sync_events.user_sandbox_id`.

## 3. Approaches Considered

| Approach | Summary | Why chosen / why not |
|---|---|---|
| **A. E2B-native permanent hibernation + explicit volume reclamation in CubePlex** *(chosen)* | Enable `pause_on_idle: true` by default, skip pause-capable `running` rows in `cleanup_expired()`, set `paused_ttl_seconds: 0` (`<= 0` disables `reap_paused()`), stop sandboxes via `pause` on manual Restart when `capabilities.pause` is `True`, add `delete_volumes: bool = False` to `SandboxProvider.terminate()`, and run provider + volume cleanup on sandbox, workspace, and user deletion. | Matches standard E2B lifecycle semantics (`pause`/`connect` preserves RootFS + volumes at 0 CPU/RAM; `delete` destroys the instance; `reset-rootfs` resets OS only), requires zero upstream `cubepod` changes (`cubepod>=0.4.1` already supports `delete_volumes=True`), and requires no database schema migration. |
| **B. Bind `rootfs-pvc` to `volumeKey` upstream in `cubepod` + Approach A** | Change `cubeplexai/cubepod` so `<volumeKey>-rootfs-pvc` also survives `DELETE ?deleteVolumes=false -> CREATE`, then apply Approach A. | Breaks E2B's `DELETE -> CREATE` clean-slate contract (a corrupted `/etc` or dynamic linker could not even recover via recreate), complicates `rootfsGeneration` across deleted `PodBox` resources, and requires cross-repo controller release/upgrade. Rejected. |
| **C. Disable idle TTL completely (`ttl = 0`)** | Keep sandbox VMs `Running` 24/7 without pausing or terminating. | Wastes 2 CPU and 2–4 GiB RAM per idle workspace indefinitely. Rejected. |

## 4. Design

### 4.1 Data Model & Configuration (Zero Schema Migration)

- **`backend/config.yaml`**:
  - `sandbox.pause_on_idle: true` (default `True`; idle sandboxes on providers with `capabilities.pause == True` suspend instead of terminating).
  - `sandbox.paused_ttl_seconds: 0` (`<= 0` disables automatic termination of paused sandboxes so paused instances retain RootFS and `/workspace` indefinitely until resumed or explicitly deleted; setting a positive integer such as `86400` re-enables hard-kill after that duration).
- **`UserSandbox` & `UserSandboxRepository` (`models/user_sandbox.py`, `repositories/user_sandbox.py`)**:
  - Change Python field default `UserSandbox.paused_ttl_seconds` from `24 * 60` to `0` (keeping existing DB `server_default="1440"` untouched so Alembic has zero schema drift).
  - Add `paused_ttl_seconds: int | None = None` to `UserSandboxRepository.reserve()` and `promote_to_running()`, passing `self._paused_ttl_seconds` from `SandboxManager.get_or_create()` and `_provision_new_container()`.
  - Update `UserSandboxRepository.list_paused_expired_system()` and `claim_terminated_from_paused()` to require `paused_ttl_seconds > 0` in SQL (`paused_ttl_seconds > 0 AND paused_at + paused_ttl_seconds * INTERVAL '1 second' <= NOW()`).
  - In `SandboxManager.__init__()`, default `self._paused_ttl_seconds` to `config.get("sandbox.paused_ttl_seconds", 0)`. In `SandboxManager.reap_paused()`, return immediately when `self._paused_ttl_seconds <= 0` so pre-existing database rows carrying `paused_ttl_seconds = 1440` from earlier versions are also protected from auto-termination.

### 4.2 Fixing `cleanup_expired()` vs `pause_idle()` (`sandbox/manager.py`)

In `SandboxManager.cleanup_expired()`:
- Continue reaping:
  1. Orphaned `provisioning` rows whose `last_activity_at` exceeded `create_timeout + ready_timeout`.
  2. `kill_pending` rows (`cleanup_action == "restart"` or `"delete"`) whose earlier stop/delete attempt has not yet finalized.
  3. Expired `running` rows when `self._pause_on_idle` is `False` **or** `not provider.capabilities.pause` (such as `LocalSandbox`).
- When `record.status == "running"` and `self._pause_on_idle` is `True` and `provider.capabilities.pause` is `True`: **skip the row**, leaving it for `pause_idle()` in the same cleanup pass.

### 4.3 Non-Destructive Manual Restart (`sandbox/manager.py`, `repositories/user_sandbox.py`)

When `POST /api/v1/ws/{workspace_id}/sandboxes/{user_sandbox_id}/restart` calls `SandboxManager.restart_user_sandbox(user_sandbox_id)`:
1. `repo.request_cleanup(row_id, action="restart", requested_at=now)` fences new work by setting `status = "kill_pending", cleanup_action = "restart"` (unchanged: `terminated` / `failed` rows remain no-ops; `kill_pending` with `cleanup_action == "delete"` still raises `409 Conflict`).
2. If `instance_id` is `None` (`pending-*` placeholder), `_finalize_cleanup_without_instance()` behaves unchanged (waits within create budget, then marks `terminated` once stale).
3. After `_request_managed_task_stop()` fences managed background tasks on `instance_id`:
   - **Pause-capable provider (`provider.capabilities.pause == True`):**
     Run `_pause_record_for_restart(session, repo, row)`:
     - Call `await provider.pause(sandbox_id)`:
       - If `provider.pause()` raises `SandboxInstanceGoneError` (404), mark commands killed, revoke `EgressRef`s, and call `repo.mark_terminated(row.id, clear_sandbox_id=True)` so next use revives cleanly.
       - If `provider.pause()` raises `SandboxConflictError`, check `provider.inspect_state(sandbox_id)`; treat `ProviderInstanceState.PAUSED` as already paused, `FAILED` / `TERMINATED` / `None` as terminated, and any other conflict as retryable (`status = "kill_pending"`).
       - On any other transient provider error, keep `status = "kill_pending", cleanup_action = "restart"` so user retry and `cleanup_expired()` retry `_pause_record_for_restart()`.
     - Once `pause()` succeeds, inspect provider state (`provider.inspect_state(sandbox_id)`):
       - Mark in-flight sandbox commands killed (`kill_sandbox_commands`) and revoke active `EgressRef`s (`_revoke_egress`) so stale tokens from the stopped guest are invalidated (fresh refs are minted on `_resume_record()`).
       - If state is `PAUSED`, atomically update the row to `status = "paused", paused_at = now, cleanup_action = None, cleanup_requested_at = None` (preserving `sandbox_id`).
       - If state is `TRANSITIONAL` or `RUNNING` (asynchronous pause backend), update the row to `status = "pausing", cleanup_action = None, cleanup_requested_at = None` so `reconcile_transients()` advances it to `paused` once confirmed.
       - If state is `None`, `FAILED`, or `TERMINATED`, call `repo.mark_terminated(row.id, clear_sandbox_id=True)`.
     - Likewise, in `cleanup_expired()`, when retrying a `kill_pending` row whose `cleanup_action == "restart"` on a pause-capable provider, dispatch to `_pause_record_for_restart()` instead of `_kill_record()`.
   - **Non-pausing provider (`provider.capabilities.pause == False`, e.g. `LocalSandbox`):**
     Keep existing `_kill_record(session, repo, row, delete_on_success=False)`.

On next use (`get_or_create()`), a restarted sandbox in `status == "paused"` resumes via `_resume_record() -> provider.resume(sandbox_id)`, booting a fresh Pod with both RootFS (`rootfs-pvc`) and `/workspace` (`workspace-pvc`) intact.

### 4.4 Provider & Volume Reclamation on Delete

1. **Provider interface (`sandbox/provider.py` & `sandbox/providers/{cubepod,opensandbox,local}.py`):**
   - Change signature to:
     ```python
     async def terminate(self, sandbox_id: str, *, delete_volumes: bool = False) -> None
     ```
   - **`CubePodProvider.terminate()`**: pass `delete_volumes=delete_volumes` to `await client.delete(sandbox_id, delete_volumes=delete_volumes)`. When `delete_volumes=True`, CubePod deletes the compute Pod, `PodBox`, `<sandboxID>-rootfs-pvc`, and `<volumeKey>-workspace-pvc` (HTTP 404 remains idempotent success).
   - **`LocalSandboxProvider.terminate()`**: pop `sandbox_id` from `_instances`; when `delete_volumes=True` and the instance's `workdir` is a subdirectory strictly inside `self.default_workdir`, remove that directory tree (`shutil.rmtree(..., ignore_errors=True)`).
   - **`OpenSandboxProvider.terminate()`**: accept `delete_volumes: bool = False` (calls `mgr.kill_sandbox(sandbox_id)`; OpenSandbox's `kill_sandbox` API does not delete external K8s PVCs, so `_kill_record()` only logs the operator-reclamation warning when `provider.name == "opensandbox"`).
2. **`SandboxManager._kill_record()` (`sandbox/manager.py`):**
   - Pass `delete_volumes=should_delete` (`should_delete = delete_on_success or record.cleanup_action == "delete"`) to `await provider.terminate(sandbox_id, delete_volumes=should_delete)`.
   - Only emit the orphaned-PVC operator warning when `should_delete and provider.name == "opensandbox"`.
3. **Workspace hard-delete (`api/routes/v1/workspaces.py`) & User account hard-delete (`api/routes/v1/auth.py`):**
   - Add `get_sandbox_manager_or_none() -> SandboxManager | None` in `sandbox/manager.py` plus two batch cleanup methods on `SandboxManager`:
     - `async def delete_workspace_sandboxes(self, workspace_id: str) -> bool`
     - `async def delete_user_sandboxes(self, user_id: str) -> bool`
     Each method queries all non-deleted `UserSandbox` rows in that scope that still hold a `sandbox_id` or are in `provisioning` / `kill_pending`, runs `await self.delete_user_sandbox(row.id)` on each, and returns `True` (`cleanup_pending`) if any row still has `deleted_at is None` (for example, an in-flight `provisioning` within create budget or a transient provider failure that left `status = "kill_pending"`).
   - In `delete_workspace()` (`workspaces.py`, right after `close_workspace_generations` / `signal_stopped_runs`) and `delete_my_account()` (`auth.py`, right after `close_user_generations` / `signal_stopped_runs`):
     - If `manager := get_sandbox_manager_or_none()` is present, await `manager.delete_workspace_sandboxes(workspace_id)` (or `manager.delete_user_sandboxes(user.id)`). If it returns `True`, return `HardDeleteResponse(deleted=False, cleanup_pending=True)` so the caller retries cleanly without orphaning remote sandboxes or PVCs.
   - Before executing `DELETE FROM user_sandboxes` in both `workspaces.py` and `auth.py`, break the FK cycle with `user_sandbox_sync_events`:
     1. `UPDATE user_sandboxes SET last_skill_sync_event_id = NULL WHERE ...`
     2. `DELETE FROM user_sandbox_sync_events WHERE ...`
     3. `DELETE FROM user_sandboxes WHERE ...`

### 4.5 UI Copy & Bilingual Documentation

- **Frontend i18n (`frontend/packages/web/messages/{en,zh}.json`):**
  - Update `wsSandboxes.deleteConfirmBody` to state that deleting permanently removes the sandbox and its stored files (instead of saying stored files are always left on disk).
- **Product & deployment docs (English + 简体中文 in the same PR):**
  - `docs/site/docs/deployment/backend-config.md` + `zh-Hans` counterpart: document new defaults `sandbox.pause_on_idle: true` and `sandbox.paused_ttl_seconds: 0` (`<= 0` = never auto-terminate paused sandboxes).
  - `docs/site/docs/deployment/docker-compose.md` + `zh-Hans` counterpart: update the pause/resume table note (`pause_on_idle` defaults to `true`; operators on plain Docker OpenSandbox can set `pause_on_idle: false` if they want containers killed instead of `docker pause`d across host restarts).
  - `docs/site/docs/guides/conversations/sandboxes.md` + `zh-Hans` counterpart: update **Restart** and **Delete** sections to reflect pause-based non-destructive restart (preserving RootFS + `/workspace` on CubePod) and volume deletion on sandbox/workspace delete (noting OpenSandbox PVC reclamation difference).

## 5. Out of Scope

- Changing `cubeplexai/cubepod` upstream PVC naming (`<sandboxID>-rootfs-pvc` remains bound to `PodBox` identity per E2B semantics).
- Reclaiming historical orphaned OpenSandbox K8s PVCs whose `UserSandbox` rows were already deleted without a `sandbox_id`.
- Modifying agent system prompts (`prompts/sandbox.py`), preserving prompt-cache stability.

## 6. Success Criteria

1. With default config (`pause_on_idle: true`, `paused_ttl_seconds: 0`), an idle CubePod sandbox transitions `running -> pausing -> paused` via `sandbox_cleanup_loop` without being killed by `cleanup_expired()`, and remains `paused` indefinitely across `reap_paused()` passes (`rootfs-pvc` and `workspace-pvc` both intact on resume).
2. Setting positive `paused_ttl_seconds` (both config and row) still terminates stale `paused` sandboxes via `reap_paused()` without deleting `/workspace` (`delete_volumes=False`).
3. `POST /api/v1/ws/{ws}/sandboxes/{id}/restart` on a pause-capable provider fences tasks, pauses the sandbox (`sandbox_id` retained, `EgressRef`s revoked), and resumes with RootFS + `/workspace` intact on next use; non-pausing providers (`LocalSandbox`) still terminate and recreate cleanly.
4. `DELETE /api/v1/ws/{ws}/sandboxes/{id}`, `DELETE /api/v1/workspaces/{ws}`, and `DELETE /api/v1/auth/me` call `provider.terminate(..., delete_volumes=True)`, return `cleanup_pending=True` if provider teardown is not yet confirmed, and cleanly remove `user_sandbox_sync_events` + `user_sandboxes` without FK errors once confirmed.
