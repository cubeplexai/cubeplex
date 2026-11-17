# Persistent Sandbox Lifecycle & Volume Cleanup — Implementation Plan

- **Goal**: Keep every sandbox's RootFS and `/workspace` storage permanently across idle periods and manual restarts via E2B-native `pause`/`resume`, while destroying both the remote sandbox instance and its persistent volumes when a sandbox, workspace, or user account is deleted.
- **Architecture**: Enable `pause_on_idle: true` and `paused_ttl_seconds: 0` (`<= 0` = never auto-reap paused sandboxes) by default, prevent `cleanup_expired()` from killing pause-capable `running` rows before `pause_idle()` runs, and switch manual `restart_user_sandbox()` on pause-capable providers from `terminate` to `pause` (preserving `sandbox_id`, RootFS, and `/workspace`). Extend `SandboxProvider.terminate(..., delete_volumes: bool = False)` and wire `SandboxManager` batch deletion into `DELETE /api/v1/workspaces/{id}` and `DELETE /api/v1/auth/me` (breaking the `user_sandboxes <-> user_sandbox_sync_events` FK cycle before row removal).
- **Tech stack**: Python 3.13 / FastAPI / SQLModel / pytest (backend), TypeScript / Next.js / next-intl (frontend i18n), Docusaurus (English + `zh-Hans` docs).

---

## Unit 1: Permanent Idle Hibernation (`pause_on_idle`, `paused_ttl_seconds = 0`, `cleanup_expired` Guard)

### Files
- Modify: `backend/config.yaml` — set `sandbox.pause_on_idle: true` and `sandbox.paused_ttl_seconds: 0`; update inline comments explaining `<= 0` disables paused-TTL auto-termination.
- Modify: `backend/cubeplex/models/user_sandbox.py` — change Python default `UserSandbox.paused_ttl_seconds` from `24 * 60` to `0` (keep `sa_column_kwargs={"server_default": "1440"}` unchanged so Alembic has zero schema drift).
- Modify: `backend/cubeplex/repositories/user_sandbox.py` —
  - Accept `paused_ttl_seconds: int | None = None` in `reserve()` and `promote_to_running()`, persisting it when non-`None`.
  - Add `paused_ttl_seconds > 0` predicate to `list_paused_expired_system()` and `claim_terminated_from_paused()`.
- Modify: `backend/cubeplex/sandbox/manager.py` —
  - Default `self._paused_ttl_seconds` to `config.get("sandbox.paused_ttl_seconds", 0)`.
  - Pass `paused_ttl_seconds=self._paused_ttl_seconds` into `repo.reserve()` and `repo.promote_to_running()`.
  - In `reap_paused()`, return immediately when `self._paused_ttl_seconds <= 0` so pre-existing rows carrying `paused_ttl_seconds = 1440` are also never reaped under the default config.
  - In `cleanup_expired()`, skip `record.status == "running"` rows when `self._pause_on_idle` is `True` and `self._provider_for_record(record).capabilities.pause` is `True` (while still reaping stuck `provisioning` rows, `kill_pending` rows, and `running` rows on non-pausing providers).

### Interfaces
```python
# backend/cubeplex/repositories/user_sandbox.py
async def reserve(
    self,
    *,
    user_id: str | None,
    image: str,
    scope_type: str,
    scope_id: str,
    volumes_config: dict[str, Any] | None = None,
    ttl_seconds: int = 3600,
    paused_ttl_seconds: int | None = None,
    provider: str = "opensandbox",
) -> UserSandbox: ...

async def promote_to_running(
    self,
    record_id: str,
    *,
    sandbox_id: str,
    image: str | None = None,
    provider: str | None = None,
    paused_ttl_seconds: int | None = None,
) -> None: ...
```

### Core Logic
- In `sandbox_cleanup_loop`, `cleanup_expired()` runs before `pause_idle()`. Skipping pause-capable `running` rows in `cleanup_expired()` when `self._pause_on_idle` is `True` hands idle `running` rows cleanly to `pause_idle()` in the same iteration.
- When an operator configures `sandbox.paused_ttl_seconds > 0` (or an E2E test sets `mgr._paused_ttl_seconds > 0`), `reap_paused()` continues terminating paused rows whose `paused_ttl_seconds > 0` window has elapsed (`delete_volumes=False`).

### Tests
- `backend/tests/unit/test_user_sandbox_model.py`: verify `UserSandbox.paused_ttl_seconds` defaults to `0`.
- `backend/tests/unit/test_sandbox_manager_pause.py`: verify `SandboxManager` defaults (`_pause_on_idle is True`, `_paused_ttl_seconds == 0`), `reap_paused()` is a no-op when `_paused_ttl_seconds <= 0`, and `cleanup_expired()` skips pause-capable `running` rows when `_pause_on_idle` is `True` while still reaping non-pausing `running` rows and `kill_pending` rows.
- `backend/tests/unit/test_manager_egress_injection.py`: pin `manager._pause_on_idle = False` in `test_cleanup_expired_revokes_refs` (which explicitly tests the legacy kill-on-idle path).
- `backend/tests/e2e/test_user_sandbox_repo_transitions.py` & `backend/tests/e2e/test_sandbox_pause_resume.py`: verify `list_paused_expired_system` / `claim_terminated_from_paused` never claim rows with `paused_ttl_seconds <= 0`, and pin `mgr._paused_ttl_seconds = 86400` in the `test_sandbox_pause_resume.py` fixture that tests positive `paused_ttl_seconds=1` expiration.

---

## Unit 2: Non-Destructive Manual Restart (`pause` Instead of `terminate`)

### Files
- Modify: `backend/cubeplex/repositories/user_sandbox.py` — update `mark_paused()` and add `mark_pausing_from_restart()` (extending `mark_paused()` to accept `"kill_pending"` alongside `"pausing"`, clearing `cleanup_action = None` and `cleanup_requested_at = None`).
- Modify: `backend/cubeplex/sandbox/manager.py` —
  - Add `_pause_record_for_restart(self, session: AsyncSession, scoped_repo: UserSandboxRepository, record: UserSandbox) -> None`.
  - In `_request_user_cleanup()`, when `action == "restart"` and `provider.capabilities.pause` is `True`, call `_pause_record_for_restart()` instead of `_kill_record()`.
  - In `cleanup_expired()`, when retrying a `kill_pending` row with `cleanup_action == "restart"` and `provider.capabilities.pause` is `True`, call `_pause_record_for_restart()` instead of `_kill_record()`.

### Interfaces
```python
# backend/cubeplex/repositories/user_sandbox.py
async def mark_paused(self, record_id: str, *, paused_at: datetime) -> bool:
    """Transition ``pausing`` or ``kill_pending`` (restart) -> ``paused`` and clear cleanup fields."""

async def mark_pausing_from_restart(self, record_id: str) -> bool:
    """Transition ``kill_pending`` (restart) -> ``pausing`` for async-pause providers."""
```

### Core Logic
- `_pause_record_for_restart()` calls `await provider.pause(sandbox_id)`:
  - If `SandboxInstanceGoneError` is raised, mark sandbox commands killed, revoke `EgressRef`s, and `mark_terminated(record_id, clear_sandbox_id=True)`.
  - If `SandboxConflictError` is raised, probe `provider.inspect_state(sandbox_id)`: treat `PAUSED` as already paused, `None` / `FAILED` / `TERMINATED` as terminated, and other states as retryable (`kill_pending`).
  - On any other exception, leave the row in `status="kill_pending", cleanup_action="restart"` for retry.
- After `pause()` succeeds, mark sandbox commands killed, revoke active `EgressRef`s, and inspect provider state:
  - `PAUSED` -> `mark_paused(record_id, paused_at=now)` (`sandbox_id` retained).
  - `TRANSITIONAL` / `RUNNING` -> `mark_pausing_from_restart(record_id)` so `reconcile_transients()` settles to `paused`.
  - `None` / `FAILED` / `TERMINATED` -> `mark_terminated(record_id, clear_sandbox_id=True)`.
- Non-pausing providers (`LocalSandbox`) continue using `_kill_record(..., delete_on_success=False)`.

### Tests
- `backend/tests/unit/test_sandbox_manager_cleanup.py`: verify `restart_user_sandbox` on a pause-capable provider fences tasks, calls `provider.pause` (never `provider.terminate`), revokes `EgressRef`s, and transitions to `paused` while retaining `sandbox_id`; verify non-pausing provider still calls `_kill_record`.
- `backend/tests/e2e/sandbox/test_sandbox_cleanup_async_session.py`: E2E test verifying `restart_user_sandbox` on a pause-capable provider preserves `sandbox_id`, transitions `running -> paused`, retries cleanly from `kill_pending` + `cleanup_action="restart"`, and resumes the same instance on next `get_or_create`.

---

## Unit 3: Volume Reclamation on Sandbox, Workspace, and User Deletion

### Files
- Modify: `backend/cubeplex/sandbox/provider.py` — add `*, delete_volumes: bool = False` to `SandboxProvider.terminate()`.
- Modify: `backend/cubeplex/sandbox/providers/cubepod.py` — forward `delete_volumes=delete_volumes` to `await client.delete(sandbox_id, delete_volumes=delete_volumes)`.
- Modify: `backend/cubeplex/sandbox/providers/local.py` — accept `*, delete_volumes: bool = False` in `terminate()`; when `True` and the instance `workdir` is strictly inside `self.default_workdir`, remove that directory tree.
- Modify: `backend/cubeplex/sandbox/providers/opensandbox.py` — accept `*, delete_volumes: bool = False` in `terminate()`.
- Modify: `backend/cubeplex/sandbox/manager.py` —
  - Pass `delete_volumes=should_delete` in `_kill_record()` (`should_delete = delete_on_success or record.cleanup_action == "delete"`), logging the operator PVC warning only when `should_delete and provider.name == "opensandbox"`.
  - Add `get_sandbox_manager_or_none() -> SandboxManager | None`.
  - Add `delete_workspace_sandboxes(self, workspace_id: str) -> bool` and `delete_user_sandboxes(self, user_id: str) -> bool`, returning `True` if any matching non-deleted row remains unfinalized (`deleted_at is None`).
- Modify: `backend/cubeplex/api/routes/v1/workspaces.py` —
  - Call `manager.delete_workspace_sandboxes(workspace_id)` when `get_sandbox_manager_or_none()` is present; return `HardDeleteResponse(deleted=False, cleanup_pending=True)` if still pending.
  - Null `UserSandbox.last_skill_sync_event_id` and delete `UserSandboxSyncEvent` rows for `workspace_id` before deleting `UserSandbox` rows.
- Modify: `backend/cubeplex/api/routes/v1/auth.py` —
  - Call `manager.delete_user_sandboxes(user.id)` when `get_sandbox_manager_or_none()` is present; return `HardDeleteResponse(deleted=False, cleanup_pending=True)` if still pending.
  - Null `UserSandbox.last_skill_sync_event_id` and delete `UserSandboxSyncEvent` rows for the user's `UserSandbox` IDs before deleting `UserSandbox` rows.

### Interfaces
```python
# backend/cubeplex/sandbox/provider.py
@abstractmethod
async def terminate(self, sandbox_id: str, *, delete_volumes: bool = False) -> None: ...

# backend/cubeplex/sandbox/manager.py
def get_sandbox_manager_or_none() -> SandboxManager | None: ...
async def delete_workspace_sandboxes(self, workspace_id: str) -> bool: ...
async def delete_user_sandboxes(self, user_id: str) -> bool: ...
```

### Core Logic
- Batch deletion queries `UserSandbox` rows (`workspace_id == workspace_id` or `user_id == user_id`) where `deleted_at IS NULL` and (`sandbox_id IS NOT NULL` or `status IN ('provisioning', 'kill_pending')`). Rows already in `terminated` / `failed` with `sandbox_id IS NULL` need no provider call and are removed directly by the subsequent SQL delete.
- If any targeted row remains `deleted_at IS NULL` after `delete_user_sandbox()` (e.g., `provisioning` within create budget or transient provider failure), returning `HardDeleteResponse(deleted=False, cleanup_pending=True)` preserves the workspace/user row so retry or `cleanup_expired()` completes provider + volume teardown instead of leaking storage.

### Tests
- `backend/tests/unit/sandbox/test_cubepod_provider.py` (or provider unit tests): verify `CubePodProvider.terminate(id, delete_volumes=False)` vs `terminate(id, delete_volumes=True)` sends `deleteVolumes=true` iff `delete_volumes=True`, and `LocalSandboxProvider.terminate(..., delete_volumes=True)` deletes the scoped workdir.
- `backend/tests/e2e/sandbox/test_sandbox_cleanup_async_session.py` & workspace/account delete E2E tests:
  - Verify `delete_user_sandbox()` calls `provider.terminate(..., delete_volumes=True)`, whereas `reap_paused()` / `cleanup_expired()` (non-delete) call `provider.terminate(..., delete_volumes=False)`.
  - Verify `DELETE /api/v1/workspaces/{workspace_id}` and `DELETE /api/v1/auth/me` terminate active/paused sandboxes with `delete_volumes=True`, return `cleanup_pending=True` if provider teardown fails transiently, and succeed cleanly even when `UserSandboxSyncEvent` rows exist (`last_skill_sync_event_id` populated).

---

## Unit 4: UI Copy & Bilingual Documentation (`en` + `zh-Hans`)

### Files
- Modify: `frontend/packages/web/messages/en.json` & `frontend/packages/web/messages/zh.json` — update `wsSandboxes.deleteConfirmBody` (and `restartConfirmBody`) to match volume deletion on Delete and environment preservation on Restart.
- Modify: `docs/site/docs/deployment/backend-config.md` & `docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/deployment/backend-config.md` — update `sandbox.pause_on_idle` default (`true`) and `sandbox.paused_ttl_seconds` default (`0`, `<= 0` = permanent retention while paused).
- Modify: `docs/site/docs/deployment/docker-compose.md` & `docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/deployment/docker-compose.md` — update the pause/resume table note for `pause_on_idle: true`.
- Modify: `docs/site/docs/guides/conversations/sandboxes.md` & `docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/guides/conversations/sandboxes.md` — update **Restart** (pauses on capable providers, keeping system environment and `/workspace`) and **Delete** (deletes persistent volumes on CubePod/Local; notes operator reclamation only on OpenSandbox).

### Tests
- `frontend/packages/web/__tests__/components/SandboxCard.test.tsx`: verify component unit tests stay green with updated i18n copy.
- Diff check across every modified `docs/site/docs/...` file and its matching `docs/site/i18n/zh-Hans/...` file (AGENTS.md Rule 13).
