# MCP Management Four-Layer Design

Date: 2026-05-15
Status: Draft
Branch: `feat/mcp-management-spec`
Worktree slot: 23

## Problem

MCP 管理当前已经具备 catalog、install、workspace enablement、credential 等能力，
但功能语义没有稳定收敛：

1. `mcp_servers` 同时表达安装、运行实例、授权状态和 workspace 私有性。
2. `workspace_mcp_overrides` 实际表达 workspace 是否启用 org install，但名字仍像
   "覆盖默认继承"。
3. `workspace-private install` 在一些路径像 workspace 级安装，在另一些路径像
   creator-private 安装。
4. `authed=false` 同时可能表示 pending OAuth、credential 被删除、发现失败、token
   过期或被 revoke。
5. UI/API/runtime 都在重复推断 "当前用户在当前 workspace 能否使用这个 connector"。

这导致用户心智模型复杂，功能状态难解释，后续 OAuth、workspace credential、user
credential、admin distribution 继续叠加时会变得更难维护。

本设计把 MCP 管理重新抽象成四个明确对象：

1. `ConnectorTemplate`
2. `ConnectorInstall`
3. `WorkspaceConnectorState`
4. `CredentialGrant`

核心目标是让系统始终能回答一个问题：

> 某个用户在某个 workspace 中能否使用某个 connector？如果不能，原因是什么？

## Goals

- 建立清晰的功能模型，使 org、workspace、user 三层职责分离。
- 明确安装、启用、授权、可运行四个状态不是同一件事。
- 统一 admin 页面、workspace settings、catalog 页面、runtime loading 的状态来源。
- 支持以下产品场景：
  - org admin 安装 connector，并分发到一个或多个 workspace。
  - workspace admin 启用 org connector，并选择 credential policy。
  - workspace admin 创建 workspace-local connector。
  - workspace member 为 user policy connector 连接自己的授权。
  - no-auth connector 可以被正确安装、启用并运行。
- 为后续重构 API、DB schema、UI 和 runtime 提供共同设计基准。

## Non-Goals

- 不在本 spec 中定义具体迁移脚本步骤。
- 不要求一次 PR 完成所有对象重命名。
- 不设计 MCP server tool invocation 的 try-it 后端。
- 不改变 Organization、Workspace、Membership、OrganizationMembership 的基础身份模型。
- 不移除 credential vault；本设计继续使用现有 vault 作为 secret 存储层。

## Recommended Direction

推荐采用分层自助模型：

- Org admin 管理 org-level connector install 和 org shared credential。
- Workspace admin 管理 workspace 是否启用 connector、credential policy、workspace shared
  credential，以及 workspace-local install。
- Workspace member 只能管理自己的 user credential grant。

不推荐完全收紧成 "只有 org admin 安装"，因为 cubebox 的 workspace 是独立协作单元，
workspace-local connector 是合理能力。

也不推荐完全放开成 "每个成员都能安装自己的 connector"，因为那会引入 personal
connector 语义。personal connector 可以作为未来功能，但不应混入 workspace install。

## Conceptual Model

### ConnectorTemplate

`ConnectorTemplate` 是系统 catalog 中的可安装模板。它只回答：

> 系统支持什么 connector？安装它需要哪些信息？

它不属于任何 organization，不保存 tenant secret，也不表示已经安装。

Conceptual fields:

| Field | Meaning |
| --- | --- |
| `id` | Template id |
| `slug` | Stable key, such as `github`, `notion`, `mslearn` |
| `name` | Display name |
| `provider` | Provider name |
| `description` | User-facing description |
| `server_url` | Default remote MCP endpoint |
| `transport` | `streamable_http` or `sse` |
| `supported_auth_methods` | `oauth`, `static`, `none` |
| `default_credential_policy` | Suggested policy: `org`, `workspace`, `user`, `none` |
| `static_form_schema` | Static credential form schema |
| `oauth_metadata` | OAuth/DCR/static client metadata |
| `tool_citation_defaults` | Default citation config by tool name |
| `status` | `active`, `deprecated`, `disabled` |

Rules:

- Template is global.
- Template rows are seeded by system catalog seeding.
- Template can be deprecated without breaking existing installs.
- Template cannot be "enabled" in a workspace directly.
- Template cannot be "authorized".

### ConnectorInstall

`ConnectorInstall` is a materialized connector in an organization or workspace.
It answers:

> Has this org or workspace installed this connector capability?

Conceptual fields:

| Field | Meaning |
| --- | --- |
| `id` | Install id |
| `template_id` | Optional reference to `ConnectorTemplate` |
| `org_id` | Owning organization |
| `install_scope` | `org` or `workspace` |
| `workspace_id` | Present when `install_scope=workspace` |
| `server_url` | Runtime MCP endpoint |
| `transport` | Runtime transport |
| `auth_method` | `oauth`, `static`, `none` |
| `auth_status` | `not_required`, `pending`, `authorized`, `disconnected`, `error` |
| `discovery_status` | `not_run`, `success`, `error` |
| `tools_cache` | Last successful discovered tools |
| `tool_citations` | Install-specific citation mappings |
| `install_state` | `active` or `uninstalled` |
| `created_by_user_id` | Actor who created the install |

Rules:

- Install does not by itself mean every workspace can use the connector.
- Org install requires workspace enablement before runtime use.
- Workspace install belongs to exactly one workspace.
- `disconnect` clears authorization but keeps install.
- `uninstall` removes or tombstones install and should not block reinstall.
- Custom connectors are installs with no template reference.

### WorkspaceConnectorState

`WorkspaceConnectorState` describes one workspace's relationship to one install.
It answers:

> Is this install enabled in this workspace, and what credential policy should
> this workspace use?

Conceptual fields:

| Field | Meaning |
| --- | --- |
| `id` | State id |
| `org_id` | Organization |
| `workspace_id` | Workspace |
| `install_id` | Connector install |
| `enabled` | Whether the workspace has enabled this install |
| `credential_policy` | `org`, `workspace`, `user`, `none` |
| `enablement_source` | `admin_auto`, `admin_manual`, `workspace_manual` |
| `updated_by_user_id` | Last actor |

Rules:

- State is not a credential.
- State is not the install itself.
- For org installs, state is required for workspace runtime use.
- For workspace installs, state is created with `enabled=true` when install is created.
- No state means "not enabled" unless a migration compatibility layer explicitly says otherwise.
- The object should not be named "override" in product language.

### CredentialGrant

`CredentialGrant` represents an authorization usable by runtime. It answers:

> Which credential should runtime use for this install under this workspace/user?

Conceptual fields:

| Field | Meaning |
| --- | --- |
| `id` | Grant id |
| `org_id` | Organization |
| `install_id` | Connector install |
| `grant_scope` | `org`, `workspace`, `user` |
| `workspace_id` | Required for workspace grant |
| `user_id` | Required for user grant |
| `credential_id` | Vault access token/static credential id |
| `refresh_credential_id` | OAuth refresh token credential id |
| `expires_at` | OAuth access token expiry |
| `grant_status` | `valid`, `missing`, `expired`, `revoked`, `error` |
| `created_by_user_id` | Actor who created the grant |

Rules:

- Grant is not an install.
- Grant is not workspace enablement.
- Static token and OAuth token are both grants.
- One install can have many user grants.
- `auth_method=none` does not create a grant; runtime signs cubebox identity token.

## Functional State Composition

Runtime usability is derived from four independent layers:

```text
ConnectorTemplate active
  -> ConnectorInstall active
    -> WorkspaceConnectorState enabled
      -> CredentialGrant available or auth_method=none
        -> Runtime usable
```

The system should expose both usable and unusable connector states. Hiding unusable
rows makes setup and debugging harder.

Canonical effective state fields:

| Field | Meaning |
| --- | --- |
| `template_status` | Template active/deprecated/disabled |
| `install_state` | Install active/uninstalled |
| `install_scope` | `org` or `workspace` |
| `enabled` | Workspace enablement |
| `credential_policy` | Effective policy |
| `required_grant_scope` | Which grant runtime needs |
| `grant_status` | Current user's relevant grant status |
| `auth_status` | Install authorization status |
| `discovery_status` | Last discovery state |
| `usable` | Whether runtime should load it |
| `reason` | Machine-readable reason when not usable |

Recommended `reason` values:

| Reason | Meaning |
| --- | --- |
| `not_installed` | Template has no install in this org/workspace |
| `not_enabled_in_workspace` | Install exists but workspace has not enabled it |
| `install_uninstalled` | Install was removed/tombstoned |
| `template_deprecated` | Template is deprecated but install may still exist |
| `pending_oauth` | OAuth install not completed |
| `missing_org_grant` | Org credential required but missing |
| `missing_workspace_grant` | Workspace credential required but missing |
| `user_needs_connection` | Current user credential required but missing |
| `grant_expired` | Token expired and refresh unavailable |
| `discovery_failed` | Tool discovery failed |
| `server_unreachable` | Runtime/tool loading cannot reach server |

## User Roles And Permissions

### Org Owner/Admin

Can:

- View all org installs.
- Create org install from template.
- Create custom org install.
- Configure org credential grant.
- Complete org OAuth.
- Disconnect org grant.
- Uninstall org install.
- Auto-enable an org install into workspaces.
- Manually enable/disable an org install for any workspace.
- Set default workspace credential policy for an org install.

Cannot:

- Create a user grant for another user.

### Workspace Admin

Can:

- View effective connector state for the workspace.
- Enable/disable org installs in the workspace, if org policy allows workspace control.
- Choose credential policy for the workspace.
- Create workspace-local install.
- Configure workspace shared credential grant.
- Disconnect workspace shared grant.
- Uninstall workspace-local install.

Cannot:

- Change org grant.
- Change connector template.
- Enable an install from another org.

### Workspace Member

Can:

- View connector state relevant to the workspace.
- Create/update/delete their own user grant.
- Start OAuth for their own user grant.

Cannot:

- Enable/disable workspace connector state.
- Change credential policy.
- Create workspace shared credential.
- Uninstall workspace-local install unless they also have workspace admin rights.

## Product Flows

### Flow 1: Org Admin Installs GitHub For Organization

1. Admin opens MCP admin page.
2. Admin selects GitHub `ConnectorTemplate`.
3. Admin creates `ConnectorInstall(install_scope=org, auth_method=oauth)`.
4. Install enters `auth_status=pending`.
5. Admin starts OAuth.
6. OAuth callback creates `CredentialGrant(grant_scope=org)`.
7. Install enters `auth_status=authorized`.
8. Discovery runs and stores tools.
9. Admin chooses distribution:
   - auto-enable all current workspaces; or
   - manually enable selected workspaces.
10. For each enabled workspace, create `WorkspaceConnectorState(enabled=true)`.

Outcome:

- GitHub is installed at org level.
- Selected workspaces can use it.
- Workspaces use org credential unless their state selects another policy.

### Flow 2: Workspace Admin Enables Existing Org Install

1. Workspace admin opens workspace settings.
2. System shows org installs available to this workspace.
3. Admin enables the GitHub install.
4. System creates or updates `WorkspaceConnectorState(enabled=true)`.
5. Admin selects credential policy:
   - `org`: use org shared grant.
   - `workspace`: prompt for workspace shared credential.
   - `user`: each member must connect.
   - `none`: no external credential required.

Outcome:

- Workspace enablement is explicit.
- Credential policy is visible and auditable.
- Runtime has a deterministic resolver path.

### Flow 3: Member Connects User Grant

1. Workspace state has `credential_policy=user`.
2. Member sees connector with `reason=user_needs_connection`.
3. Member clicks Connect.
4. For OAuth, backend starts user OAuth flow.
5. Callback creates `CredentialGrant(grant_scope=user, user_id=current_user)`.
6. Member's effective state becomes `usable=true`.
7. Other members still see `user_needs_connection`.

Outcome:

- Workspace decision and user authorization are separate.
- No user's credential leaks into another user's runtime.

### Flow 4: Workspace Admin Creates Workspace-Local Install

1. Workspace admin selects a template or custom connector in workspace settings.
2. System creates `ConnectorInstall(install_scope=workspace, workspace_id=ws)`.
3. System creates `WorkspaceConnectorState(enabled=true)` for that install.
4. Admin chooses credential policy.
5. Runtime only considers this install inside that workspace.

Outcome:

- Workspace-local install is shareable within workspace according to policy.
- It is not creator-private.
- If future personal connectors are needed, they should be a separate concept.

### Flow 5: No-Auth Connector

1. Admin or workspace admin installs a no-auth template such as Microsoft Learn.
2. Install uses `auth_method=none` and `auth_status=not_required`.
3. Workspace state uses `credential_policy=none`.
4. Runtime signs a short-lived cubebox identity token for the MCP server when needed.
5. No `CredentialGrant` is created.

Outcome:

- No-auth does not accidentally become user-scope.
- Runtime can load tools without looking for a user grant.

### Flow 6: Disconnect Versus Uninstall

Disconnect:

1. Actor removes a grant.
2. Install remains active.
3. Workspace state remains enabled.
4. Effective state becomes missing grant or pending auth.
5. User can reconnect without reinstalling.

Uninstall:

1. Actor removes or tombstones install.
2. Workspace states are removed or marked inactive.
3. Grants are revoked/deleted as appropriate.
4. Duplicate install checks ignore uninstalled rows.
5. User can install the same template again.

Outcome:

- "Disconnect" and "Uninstall" are no longer overloaded.

## Effective Connector Service

Introduce one conceptual service as the only source of truth:

```text
EffectiveConnectorService.list_for_workspace_user(
  org_id,
  workspace_id,
  user_id,
  include_unusable=true
)
```

Responsibilities:

1. Load templates relevant to catalog display.
2. Load org installs and workspace-local installs.
3. Load workspace connector states.
4. Resolve credential policy.
5. Resolve required grant.
6. Refresh OAuth grants when needed.
7. Compute `usable` and `reason`.
8. Return normalized DTOs for UI and runtime.

Consumers:

- Admin MCP page.
- Workspace MCP/settings page.
- Catalog page.
- Runtime MCP tool loader.
- Future diagnostics/support endpoints.

Runtime should not manually inspect install/state/grant tables. It should consume
only normalized effective connector DTOs where `usable=true`.

## API Shape

This is a product/API shape, not a final route-by-route implementation plan.

### Templates

```text
GET /api/v1/mcp/templates
```

Returns active/deprecated connector templates. The response may include install
summary for the current org if the caller is authenticated.

### Admin Installs

```text
GET    /api/v1/admin/mcp/installs
POST   /api/v1/admin/mcp/installs
GET    /api/v1/admin/mcp/installs/{install_id}
PATCH  /api/v1/admin/mcp/installs/{install_id}
DELETE /api/v1/admin/mcp/installs/{install_id}
```

Admin install create supports:

```json
{
  "template_id": "mctlg-...",
  "install_scope": "org",
  "auth_method": "oauth",
  "auto_enable": {
    "mode": "selected",
    "workspace_ids": ["ws-..."]
  },
  "default_credential_policy": "org"
}
```

`DELETE` means uninstall. Disconnect should be a separate grant action.

### Workspace Connector State

```text
GET   /api/v1/ws/{workspace_id}/mcp/connectors
PATCH /api/v1/ws/{workspace_id}/mcp/connectors/{install_id}/state
```

`GET` returns normalized effective connector DTOs for the current user.

Example:

```json
{
  "template_slug": "github",
  "install_id": "mcp-...",
  "install_scope": "org",
  "enabled": true,
  "credential_policy": "user",
  "required_grant_scope": "user",
  "grant_status": "missing",
  "auth_status": "authorized",
  "discovery_status": "success",
  "usable": false,
  "reason": "user_needs_connection"
}
```

`PATCH state` supports:

```json
{
  "enabled": true,
  "credential_policy": "user"
}
```

### Workspace-Local Installs

```text
POST   /api/v1/ws/{workspace_id}/mcp/installs
DELETE /api/v1/ws/{workspace_id}/mcp/installs/{install_id}
```

Workspace install creates `ConnectorInstall(scope=workspace)` and
`WorkspaceConnectorState(enabled=true)`.

### Grants

```text
POST   /api/v1/admin/mcp/installs/{install_id}/grants/org
DELETE /api/v1/admin/mcp/installs/{install_id}/grants/org

POST   /api/v1/ws/{workspace_id}/mcp/installs/{install_id}/grants/workspace
DELETE /api/v1/ws/{workspace_id}/mcp/installs/{install_id}/grants/workspace

POST   /api/v1/ws/{workspace_id}/mcp/installs/{install_id}/grants/me
DELETE /api/v1/ws/{workspace_id}/mcp/installs/{install_id}/grants/me
```

OAuth start can be scoped to the grant being created:

```text
POST /api/v1/.../grants/{org|workspace|me}/oauth/start
```

This makes OAuth intent explicit. The callback should know whether it is creating
an org, workspace, or user grant.

## UI Model

### Admin MCP Page

Admin page should be organized around org installs:

- Catalog/templates list.
- Installed org connectors.
- Install detail:
  - Overview: auth/discovery/install state.
  - Credentials: org grant status and reconnect/disconnect.
  - Workspaces: enablement matrix and credential policy per workspace.
  - Tools: discovered tools and citation mapping.

Primary admin actions:

- Install.
- Authenticate/Reauthenticate.
- Enable in workspaces.
- Set default credential policy.
- Refresh tools.
- Disconnect org credential.
- Uninstall.

### Workspace MCP Settings

Workspace settings should show effective connector states:

- Available from org.
- Enabled in workspace.
- Workspace-local installs.
- User connection needed.
- Missing workspace credential.
- Discovery/error state.

Primary workspace admin actions:

- Enable/disable connector.
- Change credential policy.
- Add workspace shared credential.
- Create workspace-local install.
- Uninstall workspace-local install.

Primary member actions:

- Connect my account/token.
- Reconnect my account/token.
- Disconnect my grant.

### Status Language

Use precise labels:

| Label | Meaning |
| --- | --- |
| Installed | A `ConnectorInstall` exists and is active |
| Enabled | Workspace state is enabled |
| Connected | Required grant is available |
| Needs connection | User/workspace/org grant missing |
| Pending OAuth | OAuth flow not completed |
| Tools synced | Discovery succeeded |
| Tools sync failed | Discovery failed |
| Disconnected | Grant was removed but install remains |
| Uninstalled | Install was removed/tombstoned |

Avoid using "override" in UI.

## Data Model Mapping To Current Tables

The four concepts can map onto current tables initially:

| Concept | Current Table |
| --- | --- |
| `ConnectorTemplate` | `mcp_catalog_connectors` |
| `ConnectorInstall` | `mcp_servers` |
| `WorkspaceConnectorState` | `workspace_mcp_overrides` |
| `CredentialGrant(org)` | `mcp_servers.credential_id` initially |
| `CredentialGrant(workspace)` | `workspace_mcp_credentials` |
| `CredentialGrant(user)` | `user_mcp_credentials` |

Short-term compatibility is acceptable, but product/service names should move
toward the four concepts even before physical table names change.

Longer term, org grants should move out of `mcp_servers.credential_id` into a
dedicated grant table or unified grant repository. That will make org/workspace/user
grant resolution symmetrical.

## Migration Strategy

### Phase 1: Semantic Service Layer

- Introduce normalized DTOs for effective connector state.
- Implement one effective-state service over current tables.
- Update workspace catalog/settings/runtime to consume this service.
- Keep existing routes as compatibility wrappers.

### Phase 2: Correctness Fixes

- Fix no-auth workspace install to use `credential_policy=none`.
- Validate credential policy values.
- Reject custom OAuth installs unless hand-rolled OAuth metadata is supported.
- Wire OAuth refresh into runtime effective state resolution.

### Phase 3: API And UI Realignment

- Add new connector/state/grant routes.
- Update admin MCP page to use install/state/grant language.
- Update workspace settings to show normalized reason states.
- Deprecate ambiguous fields such as `user_install_id` for workspace-local install.

### Phase 4: Schema Cleanup

- Rename or replace `workspace_mcp_overrides` with workspace connector state.
- Add explicit `install_state`.
- Split disconnect and uninstall persistence.
- Consider unified credential grant table.

## Invariants

- A workspace can use an org install only if workspace state is enabled.
- A workspace-local install can only be used inside its owning workspace.
- A user grant can only satisfy runtime for the same user.
- A workspace grant can only satisfy runtime for the same workspace.
- An org grant can satisfy any enabled workspace inside the same org.
- `auth_method=none` must never require a credential grant.
- `credential_policy=user` must never fall back to org/workspace grants.
- `credential_policy=workspace` must never fall back to org/user grants.
- Uninstalled installs must not block reinstall of the same template.
- Runtime must not load connectors with `usable=false`.

## Error Handling

Errors should preserve the layer where the problem happened:

- Template errors: template disabled, deprecated, unsupported auth method.
- Install errors: install missing, uninstalled, pending auth, discovery failed.
- Workspace state errors: not enabled, invalid credential policy.
- Grant errors: missing, expired, revoked, refresh failed.
- Runtime errors: server unreachable, tool load failed.

API responses should return stable machine codes. UI can map codes to clear copy.

## Testing Strategy

High-value E2E tests:

1. Org admin installs no-auth template, enables workspace, runtime loads tools.
2. Workspace admin installs no-auth workspace-local template, runtime loads tools.
3. Org install with `credential_policy=user`: user A connected, user B not connected.
4. Workspace policy changed from org to workspace: runtime stops using org grant.
5. Disconnect org grant keeps install and workspace state, but effective state becomes
   `missing_org_grant`.
6. Uninstall org install removes/tombstones state and allows reinstall.
7. OAuth grant refresh happens before runtime returns usable connector.
8. Invalid credential policy is rejected before persistence.

Unit tests:

- Effective state service reason matrix.
- Credential policy resolver.
- Grant scope isolation.
- Install state transitions.

Frontend tests:

- Workspace settings renders `usable=false` reasons correctly.
- Member sees "Connect" but not workspace/admin actions.
- Workspace admin sees enable/policy/workspace credential actions.
- Admin install detail shows install, workspace enablement, and grant state separately.

## Open Decisions

These decisions should be made before implementation planning:

1. Should workspace members be allowed to create personal connectors in a future phase?
   Recommended answer: not in this redesign; keep it separate.
2. Should workspace state rows be created explicitly for disabled org installs?
   Recommended answer: yes for auditability, but runtime can treat missing as disabled during
   migration.
3. Should workspace admin be allowed to enable any org install, or only those admin marks
   distributable?
   Recommended answer: add an org install policy later; v1 can allow enable for all org installs.
4. Should org grant stay on `mcp_servers.credential_id` short-term?
   Recommended answer: yes short-term; introduce unified grants after API semantics settle.

## Success Criteria

- A product manager can explain MCP state using four nouns: template, install, workspace
  state, grant.
- UI can show why a connector is unavailable without reading raw DB fields.
- Runtime loads connectors from one effective-state service.
- No-auth, org credential, workspace credential, and user credential connectors all follow
  the same state composition rules.
- Disconnect and uninstall are separate user actions with separate persistence semantics.
- Workspace-local install is consistently workspace-local, not creator-private.
