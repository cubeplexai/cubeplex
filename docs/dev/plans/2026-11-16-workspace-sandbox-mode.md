# Workspace Sandbox Mode (`per_user` vs `workspace` Shared) — Implementation Plan

- **Goal**: Allow workspace creators to permanently choose between per-user isolated sandboxes (`per_user`, default) and a single workspace-wide shared sandbox (`workspace`) across all conversations, topics, automations, panels, RBAC, and env vars.
- **Architecture**: Add immutable `Workspace.sandbox_mode` (`"per_user" | "workspace"`) and extend `UserSandbox`'s polymorphic scope key with `scope_type = "workspace", scope_id = workspace.id` (`user_id = NULL` on both `UserSandbox` and `EgressRef` so shared sandboxes survive member departures and user deletions). Centralize sandbox scope resolution into `cubeplex.sandbox.scope` so agent runs, HTTP sandbox panels, and background task checks resolve identically, while gating shared-sandbox lifecycle mutations (`restart`, `delete`, `reset-rootfs`) to workspace admins and restricting shared-sandbox env vars to `org` + `workspace` scopes (with read-only workspace env metadata visibility for members).
- **Tech stack**: Python 3.13 / FastAPI / SQLModel / Alembic / pytest (backend), TypeScript / Next.js / React 19 / Zustand / next-intl / Vitest (frontend), Docusaurus (English + `zh-Hans` product docs).

---

## Unit 1: Database Schema, Models & Alembic Migration

### Files
- Modify: `backend/cubeplex/models/workspace.py` — add `sandbox_mode` column (`default="per_user"`, `server_default="per_user"`).
- Modify: `backend/cubeplex/models/user_sandbox.py` — make `user_id` nullable (`str | None = Field(default=None, foreign_key="users.id", max_length=20, nullable=True, index=True)`); update docstring to document `'workspace'` scope.
- Modify: `backend/cubeplex/models/egress_ref.py` — make `user_id` nullable (`str | None = Field(default=None, foreign_key="users.id", max_length=20, nullable=True)`).
- Modify: `backend/cubeplex/repositories/workspace.py` — accept and validate `sandbox_mode: WorkspaceSandboxMode = "per_user"` in `WorkspaceRepository.create`.
- Modify: `backend/cubeplex/repositories/user_sandbox.py` — widen `user_id: str | None` on `create` and `reserve`, enforcing `(scope_type == "workspace" and user_id is None) or (scope_type in ("user", "conversation", "topic") and user_id is not None)`.
- Create: `backend/alembic/versions/<rev>_add_workspace_sandbox_mode.py` — generated strictly via `uv run alembic revision --autogenerate -m "add workspace sandbox mode"` (zero hand-edits).

### Interfaces
- `WorkspaceSandboxMode = Literal["per_user", "workspace"]`
- `Workspace.sandbox_mode: str = Field(default="per_user", max_length=20, sa_column_kwargs={"server_default": "per_user"})`
- `UserSandbox.user_id: str | None = Field(default=None, foreign_key="users.id", max_length=20, nullable=True, index=True)`
- `EgressRef.user_id: str | None = Field(default=None, foreign_key="users.id", max_length=20, nullable=True)`

### Core Logic
- Existing rows in `workspaces` receive `'per_user'` via `server_default`.
- Apply migration to both the worktree dev DB and per-slot test DB (`cubeplex_test_<slug>`).

### Tests
- `backend/tests/unit/test_sandbox_scope.py` & `backend/tests/e2e/test_workspace_sandbox_mode.py`: repository validation invariants (`WorkspaceRepository.create` rejects invalid `sandbox_mode`; `UserSandboxRepository.reserve`/`create` enforce `user_id is None` iff `scope_type == "workspace"`).

---

## Unit 2: Unified Sandbox Scope Resolution, Manager & Runtime Integration

### Files
- Create: `backend/cubeplex/sandbox/scope.py` — shared scope types and pure/DB resolution helpers.
- Modify: `backend/cubeplex/sandbox/lazy.py` — widen `user_id: str | None` and expose `owner_user_id: str | None` property.
- Modify: `backend/cubeplex/sandbox/manager.py` —
  - Widen `user_id: str | None` across `get_or_create`, `_provision_new_container`, `_connect_existing`, `_resume_record`, `_await_resumed_by_winner`, `_resolve_active_env`, and `_apply_egress`.
  - Add `resolve_workspace_sandbox_mode(self, workspace_id: str) -> WorkspaceSandboxMode` (or combine inside `resolve_run_sandbox_runtime` using `self._session_factory`, NOT `cubeplex.db.engine.async_session_maker`, to respect test session factories and avoid cross-event-loop errors).
  - Handle concurrent `IntegrityError` on `repo.reserve(...)` in `get_or_create` (`await session.rollback()`, then `_await_provisioning_winner` in a clean session / repo, raising `SandboxError` on timeout so `LazySandbox` retry semantics apply).
- Modify: `backend/cubeplex/streams/run_manager.py` —
  - Resolve `Workspace.sandbox_mode` via `sandbox_manager` in `_build_lazy_sandbox` and compute `(scope_type, scope_id, owner_user_id)` via `resolve_sandbox_target_pure`.
  - When wiring `make_session_loader` for `sandbox_config` (~line 4889), pass `user_id = sandbox.owner_user_id if isinstance(sandbox, LazySandbox) else ctx.user_id` so non-`LazySandbox` test fakes (`sandbox_factory` / `MagicMock`) never raise `AttributeError`.
- Modify: `backend/cubeplex/services/background_tasks.py` — update `_validate_sandbox_scope` to validate `("workspace", self.workspace_id, None)` when the workspace is in `"workspace"` mode.
- Modify: `backend/cubeplex/api/routes/v1/conversations.py` — skip `rekey` on 1→2 group-chat invite and force `sandbox_mode=None` + skip `rekey_to_topic` on `upgrade_conversation_to_topic` when `workspace.sandbox_mode == "workspace"`.
- Modify: `backend/cubeplex/api/routes/v1/ws_topics.py` — normalize `sandbox_mode=None` on `create_topic` and resolve `("workspace", workspace_id)` in `get_topic_sandbox_status` when `workspace.sandbox_mode == "workspace"`.
- Modify: `backend/cubeplex/api/routes/v1/ws_im.py` & `backend/cubeplex/im/conversation_resolver.py` — relax `sandbox_mode` requirement on shared IM routing when `workspace.sandbox_mode == "workspace"` and normalize IM topic `sandbox_mode` to `None`.

### Interfaces
```python
# backend/cubeplex/sandbox/scope.py
WorkspaceSandboxMode = Literal["per_user", "workspace"]

@dataclass(frozen=True, slots=True)
class ResolvedSandboxTarget:
    scope_type: Literal["workspace", "user", "conversation", "topic"]
    scope_id: str
    owner_user_id: str | None
    workspace_sandbox_mode: WorkspaceSandboxMode

def resolve_sandbox_target_pure(
    *,
    workspace_id: str,
    workspace_sandbox_mode: WorkspaceSandboxMode,
    caller_user_id: str,
    conversation_id: str | None = None,
    is_group_chat: bool = False,
    conversation_creator_user_id: str | None = None,
    topic_id: str | None = None,
    topic_sandbox_mode: str | None = None,
    topic_creator_user_id: str | None = None,
) -> ResolvedSandboxTarget: ...

async def get_workspace_sandbox_mode(
    session: AsyncSession | None, workspace_id: str
) -> WorkspaceSandboxMode: ...
```

### Core Logic
- When `workspace_sandbox_mode == "workspace"`, `resolve_sandbox_target_pure` unconditionally returns `ResolvedSandboxTarget("workspace", workspace_id, None, "workspace")`.
- When `session is None` (unit-test-only callers), `get_workspace_sandbox_mode` returns `"per_user"`.
- `build_sandbox_pvc_name(prefix, workspace_id, "workspace", workspace_id)` produces `<prefix>-<sanitize(f"ws-{workspace_id}-workspace-{workspace_id}")>`.

### Tests
- `backend/tests/unit/test_sandbox_scope.py`: Pure unit tests covering `resolve_sandbox_target_pure` across `per_user` vs `workspace` for no-conv, 1:1, group chat, dedicated topic, and creator topic, plus `build_sandbox_pvc_name` for `scope_type="workspace"`.
- `backend/tests/e2e/test_workspace_sandbox_mode.py`:
  - Two distinct workspace members executing sandboxes in separate 1:1 conversations, group chats, and topics inside a `"workspace"`-mode workspace attach to the exact same `UserSandbox` row (`scope_type="workspace"`, `user_id=None`).
  - Inviting a second participant to a 1:1 or upgrading to a topic in a `"workspace"`-mode workspace does not rekey the shared `UserSandbox` row.
  - Separate test (without creator-owned `Topic` rows, avoiding pre-existing `topics.creator_user_id` FK on account delete): deleting the workspace creator's account after promoting another member to admin leaves the shared `UserSandbox` row and its `EgressRef`s intact.

---

## Unit 3: Backend HTTP Routes, RBAC & Sandbox Env Vault

### Files
- Modify: `backend/cubeplex/api/routes/v1/workspaces.py` — add `sandbox_mode` to `WorkspaceCreate`; include `"sandbox_mode"` and `"role"` on `POST /api/v1/workspaces` (`"role": Role.ADMIN.value`), `GET /api/v1/workspaces`, and `PATCH /api/v1/workspaces/{id}` (`"role": ctx.role.value`).
- Modify: `backend/cubeplex/api/routes/v1/onboarding.py` & `backend/cubeplex/auth/users.py` — accept `sandbox_mode: WorkspaceSandboxMode = "per_user"` in `OnboardingRequest`, `_bootstrap_org_and_workspace`, and `_bootstrap_workspace_in_org`.
- Modify: `backend/cubeplex/api/routes/v1/ws_sandbox.py` —
  - Update `_resolve_sandbox_scope(session: AsyncSession | None, ctx: RequestContext, conversation_id: str | None) -> tuple[str, str, str | None]`: keep conversation/topic ACL `404` checks first when `conversation_id` is passed; treat `session is None` (when `conversation_id is None` in unit tests) as `ws_mode = "per_user"`.
  - In `reset_sandbox_rootfs`: immediately after `_resolve_sandbox_scope` (and BEFORE `repo.get_active_by_scope`), check `if scope_type == "workspace" and ctx.role != Role.ADMIN: raise HTTPException(403, "workspace admin required")`.
- Modify: `backend/cubeplex/api/routes/v1/ws_browser.py` & `backend/cubeplex/services/artifact_content.py` — type compatibility with `owner_user_id: str | None`.
- Modify: `backend/cubeplex/api/schemas/ws_sandbox.py` & `backend/cubeplex/api/routes/v1/ws_sandboxes.py` — add `can_manage: bool = True` to `MySandboxOut`; in `"workspace"` mode list `scope_type == "workspace"` rows to all members with `can_manage = (ctx.role == Role.ADMIN)`, and require `ctx.role == Role.ADMIN` (`403` otherwise) on `restart_my_sandbox` and `delete_my_sandbox` when `row.scope_type == "workspace"`.
- Modify: `backend/cubeplex/api/schemas/admin_sandbox.py` — change `UserSandboxSnapshotOut.user_id` to `str | None`.
- Modify: `backend/cubeplex/repositories/sandbox_env.py`, `backend/cubeplex/services/sandbox_env.py`, `backend/cubeplex/services/sandbox_runtime_config.py`, `backend/cubeplex/tools/builtin/sandbox_config.py` — accept `user_id: str | None` and omit `scope == "user"` entries when `user_id is None`.
- Modify: `backend/cubeplex/api/routes/v1/ws_sandbox_env.py` —
  - Check `ws_mode = await get_workspace_sandbox_mode(session, workspace_id)` **before** row lookup on `/me` routes: in `"workspace"` mode, `GET /me` returns empty `EnvEntryListOut(entries=[])`, and `POST /me`, `PATCH /me/{entry_id}`, `DELETE /me/{entry_id}` immediately return `400`.
  - On `GET /workspace`: use `Depends(require_member)` and raise `403` if `ws_mode != "workspace" and ctx.role != Role.ADMIN`; allow `200` for any member when `ws_mode == "workspace"`. Keep `POST/PATCH/DELETE /workspace` on `Depends(require_admin)`.

### Interfaces
- `POST /api/v1/workspaces`: body `{ name: str, org_id: str, sandbox_mode: "per_user" | "workspace" = "per_user" }` → `201 { id, name, org_id, role: "admin", sandbox_mode }`.
- `GET /api/v1/workspaces`: `200 [{ id, name, org_id, role, sandbox_mode, last_activity_at }]`.
- `PATCH /api/v1/workspaces/{workspace_id}`: `200 { id, name, org_id, role, sandbox_mode }`.
- `POST /api/v1/onboarding`: body `{ org_name?, org_slug?, workspace_name, sandbox_mode: "per_user" | "workspace" = "per_user" }`.
- `MySandboxOut`: adds `can_manage: bool = True`.
- `UserSandboxSnapshotOut`: `user_id: str | None`.

### Tests
- Existing unit tests `backend/tests/unit/sandbox/test_route_closing.py`, `test_sandbox_browser.py`, `test_sandbox_lease.py`, `test_run_manager_build_agent.py` remain green.
- `backend/tests/e2e/test_workspace_sandbox_mode.py`:
  - `POST /api/v1/workspaces` (returns `role="admin"` + `sandbox_mode`) and `POST /api/v1/onboarding` persist `sandbox_mode`; `GET /api/v1/workspaces` returns it; `PATCH /api/v1/workspaces/{id}` cannot mutate it.
  - Non-admin member in `"workspace"` mode sees shared sandbox in `GET /ws/{ws}/sandboxes` with `can_manage=False`, gets `403` on `POST .../restart`, `DELETE .../{id}`, and `POST .../sandbox/reset-rootfs` (even when no container is active); workspace admin succeeds.
  - Non-participant passing another user's private `conversation_id` to `/ws/{ws}/sandbox/status?conversation_id=...` in `"workspace"` mode still gets `404`.
  - In `"workspace"` mode: `POST/PATCH/DELETE /ws/{ws}/sandbox-env/me` return `400`, `GET /me` returns `[]`; `GET /ws/{ws}/sandbox-env/workspace` returns `200` (metadata only) for non-admin member while `POST /workspace` returns `403` (and in `"per_user"` mode `GET /workspace` still returns `403` for non-admin); runtime env resolution and `load_agent_view` exclude `scope="user"` entries; `/api/v1/admin/sandboxes` returns `200` with `user_id: null`.

---

## Unit 4: Frontend (`@cubeplex/core` & `packages/web`) + i18n

### Files
- Modify: `frontend/packages/core/src/api/workspaces.ts` — add `WorkspaceSandboxMode = 'per_user' | 'workspace'`, `sandbox_mode?: WorkspaceSandboxMode` on `Workspace`, `sandboxMode?: WorkspaceSandboxMode` on `CreateWorkspaceInput` (sending `sandbox_mode` when provided).
- Modify: `frontend/packages/core/src/stores/workspaceStore.ts` — pass optional `sandboxMode` through `create(client, name, sandboxMode)` and ensure `role: ws.role ?? 'admin'` on prepend.
- Modify: `frontend/packages/core/src/api/onboarding.ts` — add optional `sandbox_mode?: WorkspaceSandboxMode` to `OnboardingRequest`.
- Modify: `frontend/packages/core/src/types/sandbox.ts` — add `can_manage?: boolean` to `MySandboxOut`.
- Modify: `frontend/packages/web/components/workspace/WorkspaceCreateForm.tsx` — add Sandbox Mode radio option cards (`per_user` default vs `workspace` shared, noting immutability).
- Modify: `frontend/packages/web/components/onboarding/OnboardingForm.tsx` — add Sandbox Mode radio option cards (`per_user` default vs `workspace` shared).
- Modify: `frontend/packages/web/components/workspace/WorkspaceList.tsx` & `frontend/packages/web/components/workspace-settings/PersonaEditor.tsx` — display read-only Sandbox Mode in workspace list and General settings.
- Modify: `frontend/packages/web/components/workspace-settings/sandboxes/SandboxCard.tsx` — render `scope.workspace` label for `scope_type === 'workspace'`; when `sandbox.can_manage === false`, hide Restart & Delete buttons and show localized admin-only label.
- Modify: `frontend/packages/web/components/workspace-settings/SandboxEnvPanel.tsx`, `frontend/packages/web/app/(app)/w/[wsId]/sandbox-env/page.tsx`, & `frontend/packages/web/components/sandbox-env/EnvTable.tsx` —
  - Deduplicate `app/(app)/w/[wsId]/sandbox-env/page.tsx` by rendering `<SandboxEnvPanel wsId={wsId} />`.
  - When workspace `sandbox_mode === 'workspace'`: hide `Add Personal Variable`, fetch `listWsEnvWorkspace(client, wsId)` for both admins and members, and pass `mode="workspace-readonly"` to `EnvTable` for non-admin members (hiding Edit/Delete columns/buttons and showing a subtle admin-managed description).
- Modify: `frontend/packages/web/components/dialogs/CreateGroupChatDialog.tsx` & `frontend/packages/web/components/dialogs/UpgradeToTopicDialog.tsx` — hide `sandbox_mode` radio controls and omit `sandbox_mode` from request body when workspace `sandbox_mode === 'workspace'`.
- Modify: `frontend/packages/web/components/im/ImAccountDetailPanel.tsx` — look up workspace `sandbox_mode` by `account.workspace_id`; when `'workspace'`, hide `#im-sandbox-mode` `<Select>`, send `sandbox_mode: null` on save / routing change, and show a workspace-shared sandbox line in `behaviorSummary`.
- Modify: `frontend/packages/web/messages/en.json` & `frontend/packages/web/messages/zh.json` — add matching English & Chinese translation keys.

### Core Logic
- Build `@cubeplex/core` (`pnpm --filter @cubeplex/core build`) before typechecking/testing/building `packages/web`.

### Tests
- `frontend/packages/core/__tests__/api/workspaces.test.ts` & `workspaceStore.test.ts` — verify `sandbox_mode` payload and store propagation (`role: 'admin'`, `sandbox_mode`).
- `frontend/packages/web/__tests__/components/OnboardingForm.test.tsx` — verify default `'per_user'` compatibility and explicit `'workspace'` submission.
- Component tests for `WorkspaceCreateForm`, `SandboxCard` (`scope_type: 'workspace'`, `can_manage: false`), and `SandboxEnvPanel` (shared workspace member read-only view vs admin view).

---

## Unit 5: Bilingual Product Documentation (`en` + `zh-Hans`)

### Files
- Modify English docs under `docs/site/docs/`:
  - `getting-started/workspace-setup.md`
  - `guides/conversations/sandboxes.md`
  - `guides/conversations/topics.md`
  - `guides/conversations/group-chats.md`
  - `guides/im/overview.md`
  - `admin/sandbox.md`
- Modify matching Simplified Chinese docs under `docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/`:
  - `getting-started/workspace-setup.md`
  - `guides/conversations/sandboxes.md`
  - `guides/conversations/topics.md`
  - `guides/conversations/group-chats.md`
  - `guides/im/overview.md`
  - `admin/sandbox.md`

### Core Logic
- Every behavior, RBAC rule (Restart/Delete admin-only in UI), immutability note, topic/group-chat/IM automatic workspace sandbox usage, and env scope rule (Organization + Workspace only, member read-only visibility) must match 1:1 across English and Simplified Chinese (AGENTS.md Rule 13). Do not reference a non-existent UI button for Reset RootFS.

### Verification
- Inspect `git diff origin/main...HEAD -- docs/site` to confirm 1:1 file pairing and content equivalence, and run `pnpm build` in `docs/site` if package scripts exist.
