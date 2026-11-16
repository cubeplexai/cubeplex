# Workspace Sandbox Mode (`per_user` vs `workspace` Shared) — Design

**Status**: Draft · 2026-11-16

---

## 1. Goal

Allow users creating a workspace to permanently choose whether the workspace provisions an isolated sandbox per user (`per_user`, default) or shares a single sandbox and persistent volume across the entire workspace (`workspace`).

---

## 2. Context

Today `Workspace` has no sandbox configuration. Sandboxes are tracked in `user_sandboxes` keyed by `(org_id, workspace_id, scope_type, scope_id)` where `scope_type` is one of `'user'`, `'conversation'`, or `'topic'`:

- **1:1 conversations and standalone workspace panel**: `('user', user_id)` — each member gets their own container and persistent volume (`<prefix>-ws-<ws>-user-<user>`).
- **Standalone group chats**: `('conversation', conversation_id)` — rekeyed from `'user'` when a second participant is invited.
- **Topics & IM connectors**: `('topic', topic_id)` in `dedicated` mode, or `('user', creator_user_id)` in `creator` mode.

Teams collaborating on a single shared codebase, data pipeline, or workspace-wide environment inside a workspace currently have no way to make the entire workspace share one sandbox and `/workspace` volume across all members, conversations, topics, and automations. Conversely, individual or multi-team workspaces still want per-user isolation by default. Because switching modes after files exist on PVCs would strand or expose existing storage, the mode must be chosen when the workspace is created and remain immutable afterward.

---

## 3. Approaches Considered

### Approach A (Recommended): Extend `UserSandbox` polymorphic scope with `scope_type = "workspace"` + immutable `Workspace.sandbox_mode`

- Add `sandbox_mode: Literal["per_user", "workspace"] = "per_user"` on `workspaces`.
- When `workspace.sandbox_mode == "workspace"`, every sandbox resolution in that workspace returns `(scope_type="workspace", scope_id=workspace.id, owner_user_id=None)`.
- Allow `user_sandboxes.user_id` and `egress_refs.user_id` to be `NULL` when `scope_type == "workspace"` (enforced by repository/schema invariants), decoupling the workspace-shared sandbox from any single user's membership or account deletion.
- Reuse the existing `uq_user_sandbox_active_scope` partial unique index, reaper loops (`pause_idle`, `reap_paused`, `cleanup_expired`, `reconcile_transients`), skill sync, command coordinator, and `build_sandbox_pvc_name` (which already formats non-`user` scopes as `<prefix>-ws-<ws>-workspace-<ws>`).

**Why chosen**: Zero duplicate lifecycle machinery. Pause/resume, leases, background command tracking, skill sync, and PVC naming work unchanged. Nullable `user_id` cleanly avoids orphan/cascade bugs when the workspace creator leaves or deletes their account.

### Approach B: Store a `WorkspaceSandbox` table separate from `UserSandbox`

- Keeps `user_sandboxes.user_id` non-null, but duplicates every lifecycle state machine, reaper query, `SandboxCommand` FK, skill sync event FK, and status endpoint.
- **Rejected**: High duplication and bug surface for no behavioral benefit.

### Approach C: Keep `user_sandboxes.user_id` non-null and pin it to the workspace creator

- Avoids altering `user_id` nullability, but breaks when the original creator leaves the workspace or deletes their account (`DELETE FROM user_sandboxes WHERE user_id = :uid` in `auth.py` would destroy the entire workspace's shared sandbox, or fail on FK constraints). Also conflates self-service ownership checks (`user_id == actor.id`) with workspace-admin RBAC.
- **Rejected**: Fragile ownership semantics on member departure and account deletion.

---

## 4. Design

### 4.1 Data Model & Database Migration

1. **`workspaces.sandbox_mode`** (`backend/cubeplex/models/workspace.py`):
   - Type: `VARCHAR(20) NOT NULL DEFAULT 'per_user'` (`server_default="per_user"` so existing rows backfill seamlessly).
   - Validated to `('per_user', 'workspace')` in Pydantic request schemas and `WorkspaceRepository.create` (matching `Topic.sandbox_mode` and `UserSandbox.scope_type` conventions so Alembic `--autogenerate` produces a pure, unedited migration).
   - Immutable after insert: no PATCH/update route modifies `sandbox_mode`.

2. **`user_sandboxes.user_id`** (`backend/cubeplex/models/user_sandbox.py`):
   - Change `user_id` from `NOT NULL` to `NULLABLE` (`str | None = Field(default=None, foreign_key="users.id", max_length=20, nullable=True, index=True)`).
   - `UserSandboxRepository.create` and `UserSandboxRepository.reserve` enforce `(scope_type == 'workspace' and user_id is None) or (scope_type in ('user', 'conversation', 'topic') and user_id is not None)`.
   - Existing partial unique index `uq_user_sandbox_active_scope` on `(org_id, workspace_id, scope_type, scope_id) WHERE deleted_at IS NULL` guarantees at most one live workspace-shared `UserSandbox` row per workspace.

3. **`egress_refs.user_id`** (`backend/cubeplex/models/egress_ref.py`):
   - Change `user_id` from `NOT NULL` to `NULLABLE` (`str | None = Field(default=None, foreign_key="users.id", max_length=20, nullable=True)`).
   - `EgressExchangeService` only reads `ref.sandbox_id`, `ref.org_id`, and `ref.bindings`, never `ref.user_id`. Keeping `user_id = NULL` on workspace-shared sandbox refs ensures that deleting an individual user account (`DELETE FROM egress_refs WHERE user_id = :uid`) does not revoke active egress placeholders for a shared workspace container.

4. **Migration**:
   - Generated strictly via `uv run alembic revision --autogenerate -m "add workspace sandbox mode"` with zero hand-edits (also upgrade the per-slot test DB `cubeplex_test_<slug>`).

### 4.2 Unified Sandbox Scope Resolution

Create a single authoritative scope-resolution module `backend/cubeplex/sandbox/scope.py` used by all runtime and HTTP paths:

```python
WorkspaceSandboxMode = Literal["per_user", "workspace"]
WORKSPACE_SANDBOX_MODES: tuple[WorkspaceSandboxMode, ...] = ("per_user", "workspace")

@dataclass(frozen=True, slots=True)
class ResolvedSandboxTarget:
    scope_type: Literal["workspace", "user", "conversation", "topic"]
    scope_id: str
    owner_user_id: str | None
    workspace_sandbox_mode: WorkspaceSandboxMode
```

Resolution rules:
1. Load `workspace.sandbox_mode` for `workspace_id` (404/LookupError if workspace missing).
2. **If `workspace.sandbox_mode == "workspace"`**:
   - Still enforce caller conversation/topic access checks when `conversation_id` is supplied on HTTP endpoints (`ws_sandbox.py`, `ws_browser.py`, `artifact_content.py`) so a non-participant cannot probe another private conversation's existence via `?conversation_id=...` (preserving the 404 non-leak invariant).
   - Always return `ResolvedSandboxTarget(scope_type="workspace", scope_id=workspace_id, owner_user_id=None, workspace_sandbox_mode="workspace")`.
3. **If `workspace.sandbox_mode == "per_user"`**:
   - Exact existing behavior:
     - No conversation or 1:1 personal conversation: `("user", caller_user_id, caller_user_id, "per_user")`
     - Standalone group chat (`topic_id is None and is_group_chat`): `("conversation", conversation_id, conversation_creator_user_id, "per_user")`
     - Dedicated topic: `("topic", topic_id, topic_creator_user_id, "per_user")`
     - Creator-mode topic: `("user", topic_creator_user_id, topic_creator_user_id, "per_user")`

All three existing scope resolvers delegate to this shared logic:
- **Agent runtime**: `RunManager._build_lazy_sandbox` (`streams/run_manager.py`) resolves `Workspace.sandbox_mode` via `SandboxManager._session_factory` (inside `SandboxManager.resolve_run_sandbox_runtime` alongside `resolve_scope_runtime`, avoiding direct `engine.async_session_maker` cross-loop issues in E2E tests and saving an extra DB round-trip) so all 11 `RunContext` callers (interactive chat, IM worker/resume, scheduled tasks, event triggers, HITL resume, command wake coordinator) automatically honor `workspace` mode without needing manual plumbing at every `RunContext(...)` construction site.
- **HTTP panel / files / terminal / browser / artifact sync**: `_resolve_sandbox_scope` in `api/routes/v1/ws_sandbox.py` (reused by `ws_browser.py` and `services/artifact_content.py`; falls back to `"per_user"` when `session is None` and `conversation_id is None` for unit-test compatibility) and `get_topic_sandbox_status` in `ws_topics.py`.
- **Background task reservation check**: `BackgroundTaskService._validate_sandbox_scope` (`services/background_tasks.py`) checks `(sandbox.scope_type, sandbox.scope_id, sandbox.user_id) == ("workspace", self.workspace_id, None)` when the workspace has `sandbox_mode == "workspace"`.

### 4.3 Topic, Group Chat, and IM Interactions in `workspace` Mode

When `workspace.sandbox_mode == "workspace"`:
- **1:1 → Group Chat participant invite** (`conversations.py:invite_participants`): skip `sbx_repo.rekey(from_scope_type="user", ...)` (there is no per-user sandbox to rekey; all runs already use `("workspace", workspace_id)`).
- **Conversation → Topic upgrade** (`conversations.py:upgrade_conversation_to_topic`): force `topic.sandbox_mode = None` and skip `sbx_repo.rekey_to_topic(...)`.
- **Create Topic** (`ws_topics.py:create_topic`): ignore/normalize `sandbox_mode` to `None` when `workspace.sandbox_mode == "workspace"`.
- **IM Bot settings** (`ws_im.py:update_im_account_settings` & `im/conversation_resolver.py`): when `workspace.sandbox_mode == "workspace"`, `PUT /ws/{ws}/im/accounts/{id}/settings` does **not** require `body.sandbox_mode` when `routing_mode == "shared"` (stores `sandbox_mode = None`), and `im/conversation_resolver.py` leaves `topic.sandbox_mode = None` when creating IM topics.

### 4.4 API Surface

1. **Workspace Creation & Listing (`api/routes/v1/workspaces.py`)**
   - `WorkspaceCreate`:
     ```python
     class WorkspaceCreate(BaseModel):
         name: str
         org_id: str
         sandbox_mode: Literal["per_user", "workspace"] = "per_user"
     ```
   - `POST /api/v1/workspaces` persists `sandbox_mode` and returns `{"id", "name", "org_id", "role": "admin", "sandbox_mode"}` (including `role` so client-side navigation into the newly created workspace immediately recognizes the creator as workspace admin).
   - `GET /api/v1/workspaces` includes `"sandbox_mode": ws.sandbox_mode` on every item.
   - `PATCH /api/v1/workspaces/{workspace_id}` only updates `name` (does not accept or mutate `sandbox_mode`), returning `{"id", "name", "org_id", "role": ctx.role.value, "sandbox_mode"}`.

2. **Onboarding (`api/routes/v1/onboarding.py` & `auth/users.py`)**
   - `OnboardingRequest` adds `sandbox_mode: Literal["per_user", "workspace"] = "per_user"`.
   - `_bootstrap_org_and_workspace` and `_bootstrap_workspace_in_org` accept `sandbox_mode: WorkspaceSandboxMode = "per_user"` (defaulting to `"per_user"` for automatic single-tenant `"Personal"` registration bootstrap).

3. **Workspace Sandboxes Management (`api/routes/v1/ws_sandboxes.py`)**
   - `MySandboxOut` adds `can_manage: bool = True` (or frontend derives from `scope_type === "workspace" ? isAdmin : true`; including `can_manage: bool` on `MySandboxOut` keeps server authoritative).
   - `GET /api/v1/ws/{workspace_id}/sandboxes` (`require_member`):
     - If `workspace.sandbox_mode == "workspace"`: returns non-deleted rows in this workspace where `scope_type == "workspace"` (`can_manage = (ctx.role == Role.ADMIN)`). Every workspace member can see the shared sandbox status.
     - If `workspace.sandbox_mode == "per_user"`: existing query (`user_id == actor.id`, `can_manage = True`).
   - `POST /api/v1/ws/{workspace_id}/sandboxes/{id}/restart` and `DELETE /api/v1/ws/{workspace_id}/sandboxes/{id}`:
     - Look up the non-deleted `UserSandbox` in `(ctx.org_id, ctx.workspace_id)`.
     - If missing: `404`.
     - If `row.scope_type == "workspace"`: require `ctx.role == Role.ADMIN`, else raise `403 Forbidden` (`"workspace admin required"`). (Note: 403 is appropriate here because all workspace members can already see the shared sandbox in `GET /sandboxes`; there is no cross-user existence secret to hide.)
     - Else (`user` / `conversation` / `topic`): keep existing `row.user_id == actor.id` check (`404` on mismatch).

4. **Reset RootFS (`api/routes/v1/ws_sandbox.py`)**
   - `POST /api/v1/ws/{workspace_id}/sandbox/reset-rootfs`:
     - Run `_resolve_sandbox_scope(session, ctx, conversation_id)` first (preserving `404` on unauthorized `?conversation_id=...`), then check `if scope_type == "workspace" and ctx.role != Role.ADMIN: raise HTTPException(403, "workspace admin required")` before looking up active sandbox state.
   - Daily collaboration endpoints (`/status`, `/files`, `/files/content`, `/files/upload`, `/files/download`, `/files/preview-token`, `/terminal`, `/browser/live-view`, `/browser/keepalive`) remain `require_member` for all workspace members.

5. **Admin Sandbox Observability (`api/schemas/admin_sandbox.py`)**
   - `UserSandboxSnapshotOut.user_id` becomes `str | None` so `/api/v1/admin/sandboxes` serializes workspace-shared sandboxes (`user_id=None`) cleanly without Pydantic validation errors.

### 4.5 Sandbox Environment Variables (`SandboxEnv`) in `workspace` Mode

1. **Runtime & Agent Diagnosis (`SandboxEnvRepository`, `SandboxEnvResolver`, `SandboxManager`, `sandbox_runtime_config`)**:
   - `SandboxEnvRepository.list_for_resolution(*, workspace_id: str, user_id: str | None)` and `SandboxEnvResolver.resolve(*, workspace_id: str, user_id: str | None)`: when `user_id is None`, only `scope == "org"` and `(scope == "workspace" and r.workspace_id == workspace_id)` entries are returned; `scope == "user"` rows are excluded.
   - In `SandboxManager._resolve_active_env`, when `record.scope_type == "workspace"` (or `user_id is None`), pass `user_id=None` to `SandboxEnvResolver.resolve`.
   - In `run_manager.py` when wiring `make_session_loader` for the `sandbox_config` tool, pass `user_id = sandbox.owner_user_id if isinstance(sandbox, LazySandbox) else ctx.user_id` (guarding non-`LazySandbox` test fakes/factories while ensuring `None` for workspace-shared sandboxes and `owner_user_id` for creator/topic sandboxes), and update `make_session_loader` / `load_agent_view` / `resolve_env_inventory` to accept `user_id: str | None`.

2. **HTTP Routes (`api/routes/v1/ws_sandbox_env.py`)**:
   - Personal env endpoints (`/me`):
     - When `workspace.sandbox_mode == "workspace"` (checked before row existence lookup on `PATCH`/`DELETE` so mutations consistently return `400`):
       - `GET /ws/{workspace_id}/sandbox-env/me` returns `EnvEntryListOut(entries=[])`.
       - `POST /me`, `PATCH /me/{entry_id}`, `DELETE /me/{entry_id}` return `400 Bad Request` (`"personal sandbox env vars are disabled in shared-sandbox workspaces"`).
   - Workspace env list endpoint (`GET /ws/{workspace_id}/sandbox-env/workspace`):
     - Change dependency from `require_admin` to `require_member`, and enforce:
       - If `workspace.sandbox_mode == "per_user"` and `ctx.role != Role.ADMIN`: raise `403 Forbidden` (preserving current admin-only visibility in `per_user` workspaces).
       - If `workspace.sandbox_mode == "workspace"`: allow any workspace member (`require_member`) to read the workspace env metadata list (`EnvEntryOut` never includes secret plaintext or `credential_id`).
     - `POST /workspace`, `PATCH /workspace/{entry_id}`, `DELETE /workspace/{entry_id}` remain `require_admin`.

### 4.6 Frontend UI Flow

1. **`@cubeplex/core` Types, APIs & Store**:
   - `WorkspaceSandboxMode = 'per_user' | 'workspace'`.
   - Add `sandbox_mode: WorkspaceSandboxMode` to `Workspace` (`api/workspaces.ts`), optional `sandboxMode?: WorkspaceSandboxMode` to `createWorkspace`, `workspaceStore.create`, and `OnboardingRequest` (`api/onboarding.ts`).
   - Add `can_manage: boolean` to `MySandboxOut` (`types/sandbox.ts`).

2. **Creation Forms**:
   - `WorkspaceCreateForm.tsx` (`/workspaces`) and `OnboardingForm.tsx` (`/onboarding`):
     - Add a clear radio-card / option group for **Sandbox Mode** with two choices:
       - **Per-user isolated sandbox** (`per_user`, default selected) — Each member gets their own private sandbox and storage.
       - **Workspace shared sandbox** (`workspace`) — All members, topics, and automations share one sandbox and `/workspace` storage; cannot be changed after creation.

3. **Read-Only Mode Display**:
   - `PersonaEditor.tsx` (Workspace Settings → General): display a read-only **Sandbox Mode** section below Workspace Name showing the current mode and a brief note that it is fixed at creation time.
   - `WorkspaceList.tsx` (`/workspaces`): display the sandbox mode label alongside the user's role on each workspace row.

4. **Workspace Settings → Sandboxes (`SandboxesPanel.tsx` & `SandboxCard.tsx`)**:
   - Handle `sandbox.scope_type === 'workspace'` with localized label (e.g., `"Workspace shared sandbox"` / `"工作区共享沙箱"`).
   - When `sandbox.can_manage === false` (non-admin member viewing a workspace-shared sandbox), hide the **Restart** and **Delete** buttons and display a localized admin-managed hint.

5. **Workspace Sandbox Env (`SandboxEnvPanel.tsx`, standalone route `app/(app)/w/[wsId]/sandbox-env/page.tsx`, & `EnvTable.tsx`)**:
   - Deduplicate `app/(app)/w/[wsId]/sandbox-env/page.tsx` by rendering `<SandboxEnvPanel wsId={wsId} />` (preserving the scope-isolated page route while sharing the panel module).
   - Read `sandboxMode` from `useWorkspaceStore`.
   - When `sandboxMode === 'workspace'`:
     - Hide the **Add Personal Variable** button.
     - Both admins and regular members fetch `listWsEnvWorkspace(client, wsId)` (skip `listWsEnvMe`).
     - For Admins (`isAdmin === true`): full add/edit/delete on workspace variables.
     - For Members (`isAdmin === false`): render `EnvTable` in a new read-only mode (`'workspace-readonly'`, hiding Edit/Delete action buttons) with a subtitle/banner noting that workspace environment variables are managed by workspace admins.

6. **Topic & IM Dialogs**:
   - `CreateGroupChatDialog.tsx` and `UpgradeToTopicDialog.tsx`: when the current workspace has `sandbox_mode === 'workspace'`, hide the `sandbox_mode` (`dedicated` / `creator`) radio selector and omit `sandbox_mode` from the request payload.
   - `ImAccountDetailPanel.tsx`: when the workspace (`account.workspace_id`) has `sandbox_mode === 'workspace'`, hide the `#im-sandbox-mode` `<Select>`, send `sandbox_mode: null` on save / routing change, and render a dedicated workspace-shared sandbox line in `behaviorSummary`.

### 4.7 Product Documentation (English & Simplified Chinese)

Per AGENTS.md Rule 13, update both English (`docs/site/docs/...`) and Simplified Chinese (`docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/...`) in the same PR:
1. `getting-started/workspace-setup.md`: Document choosing the sandbox mode (**Per-user** vs **Workspace shared**, immutable after creation) during workspace creation.
2. `guides/conversations/sandboxes.md`: Explain workspace-level sandbox modes, the **Workspace shared sandbox** scope row in Settings → Sandboxes, and that **Restart** and **Delete** on a workspace-shared sandbox require workspace admin permissions while all members can view and use it.
3. `guides/conversations/topics.md` & `guides/conversations/group-chats.md`: Note that in workspaces configured with a workspace-shared sandbox, group chats and topics automatically use the workspace sandbox (topics do not prompt for a topic sandbox mode).
4. `guides/im/overview.md`: Note that in workspaces configured with a workspace-shared sandbox, shared IM routing automatically uses the workspace sandbox without prompting for a sandbox mode.
5. `admin/sandbox.md`: Note under Environment Variables → Scope levels that workspaces in shared-sandbox mode use Organization and Workspace variables only (User-scope variables are disabled, and members have read-only visibility into Workspace variable names).

### 4.8 Edge Cases

- **Workspace creator leaves or deletes account**: Because `user_sandboxes.user_id` and `egress_refs.user_id` are `NULL` for `scope_type == "workspace"`, `leave_workspace`, `remove_workspace_member`, and `delete_account` (`auth.py`) leave the workspace-shared sandbox and its active egress refs intact for remaining members.
- **Workspace deletion**: `delete_workspace` (`workspaces.py`) deletes `EgressRef` and `UserSandbox` by `workspace_id == workspace_id`, which cleanly removes `scope_type == "workspace"` rows regardless of `user_id IS NULL`.
- **Concurrent first-run provisioning**: Two members messaging simultaneously in a brand-new shared workspace both target `(org_id, workspace_id, "workspace", workspace_id)`. `uq_user_sandbox_active_scope` lets one caller win `reserve()` while the second catches `IntegrityError` / waits via `_await_provisioning_winner` and attaches to the same container.
- **Cross-conversation existence probing**: Even though all conversations in a shared workspace resolve to the same sandbox, `_resolve_sandbox_scope` still validates `_assert_personal_or_conv_access` / `_assert_topic_access` when `conversation_id` is passed so non-participants still receive `404 Conversation not found`.

---

## 5. Out of Scope

- Converting an existing workspace between `per_user` and `workspace` after creation (intentionally immutable to avoid PVC migration or cross-user file leakage).
- Automatic Kubernetes PVC deletion on workspace delete (continues to follow the platform's existing operator-managed PVC retention policy).
- Per-topic `dedicated` sandbox overrides inside a `workspace`-mode workspace (all conversations/topics share the single workspace sandbox by design).

---

## 6. Success Criteria

1. Creating a workspace via `POST /api/v1/workspaces` or `POST /api/v1/onboarding` persists `sandbox_mode` (`"per_user"` default or `"workspace"`), and `GET /api/v1/workspaces` returns it.
2. In a `"workspace"`-mode workspace, two different members running code in separate personal conversations, group chats, and topics attach to the exact same `UserSandbox` row (`scope_type="workspace"`, `scope_id=workspace_id`, `user_id=None`) and mount the same PVC (`<prefix>-ws-<ws>-workspace-<ws>`).
3. Removing a member or deleting the workspace creator's user account (after transferring admin to another member) does not delete or break the workspace-shared `UserSandbox` or its `EgressRef`s.
4. Non-admin members in a `"workspace"`-mode workspace can view the shared sandbox in `GET /ws/{ws}/sandboxes` and use files/terminal/browser, but receive `403` on `POST .../restart`, `DELETE .../{id}`, and `POST .../sandbox/reset-rootfs`; workspace admins succeed (`202` / `200`).
5. In a `"workspace"`-mode workspace, `scope="user"` sandbox env mutations return `400`, runtime & `sandbox_config` tool resolve only `org` + `workspace` env vars, and non-admin members can read `GET /ws/{ws}/sandbox-env/workspace` (`200`, metadata only) while mutations stay `403`.
6. Bilingual product docs (`en` and `zh-Hans`) and UI i18n (`en.json` and `zh.json`) are updated with full parity.
