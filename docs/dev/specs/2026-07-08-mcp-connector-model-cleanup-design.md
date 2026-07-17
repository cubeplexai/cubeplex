# Design: MCP Connector Model Cleanup

- **Date**: 2026-07-08
- **Status**: DRAFT - pending product review
- **Related**:
  - `docs/dev/specs/2026-07-08-mcp-credential-layering-design.md`
  - `docs/dev/plans/2026-07-08-mcp-credential-layering.md`
  - `backend/docs/mcp_catalog_oauth.md`

## 1. Problem

The credential layering work introduces `mcp_connectors` as an organization-owned
connector identity, but the runtime model still treats `mcp_connector_installs`
as the primary object in many flows:

- OAuth start/callback is keyed by `install_id`.
- Credential grants are keyed by `install_id`.
- Workspace enablement still references `install_id`.
- Effective runtime state starts from install rows.
- API responses still expose install rows as the main object.

That is a useful transition because it fixes the immediate 409 bug with limited
risk. It is not the clean final model.

The product concept is simpler than the current tables:

- A connector is an org-visible capability for an MCP server.
- A workspace can enable or disable that connector.
- Credentials can be provided at org, workspace, or user scope.
- Runtime selects exactly one credential source from workspace state.

"Install" is not a durable product object in that model. It is an action or, at
most, an internal provisioning record.

## 2. Product Definition

### 2.1 Connector

A connector is the organization-level identity for an MCP server. It answers:
"What MCP capability exists in this organization?"

Examples:

- Atlassian
- GitHub
- Notion
- Tavily

One active connector identity should exist per org for a catalog template, custom
URL, or runtime namespace. This protects tool namespace uniqueness without
blocking multiple credential sources.

The connector owns shared server metadata:

- catalog template reference, if any
- display name and tool namespace slug
- server URL and transport
- auth method selected for this connector
- OAuth client configuration for that MCP server
- static-auth injection shape
- tools cache, citations, discovery status, and discovery errors

### 2.2 Workspace State

Workspace state answers: "How does this workspace use this connector?"

It owns:

- enabled or disabled
- selected credential policy: `org`, `workspace`, `user`, or `none`
- enablement source, such as admin rollout or workspace manual enablement
- last updater

Workspace state must point directly at `connector_id`. It should not need an
install row to identify the connector.

### 2.3 Credential Grant

A credential grant answers: "Which credential should be used for this connector
at this scope?"

It owns:

- connector id
- grant scope: `org`, `workspace`, or `user`
- workspace id when scope is workspace or user
- user id when scope is user
- credential id and optional refresh credential id
- expiry and status

Credential grants should point directly at `connector_id`. They should not need
an install row to identify the connector.

### 2.4 Install

The product should stop treating install as a core object.

The word "install" may still appear in UI copy as a verb, but the backend model
should not require a durable `mcp_connector_installs` row to answer core product
questions.

If a durable record is still needed for audit or migration, it should be renamed
or narrowed to a specific internal purpose, for example:

- `mcp_connector_provisioning_events`
- `mcp_connector_admin_actions`
- an append-only audit event stream

It should not be the object that workspace state, grants, OAuth, and runtime all
depend on.

## 3. Target Data Model

```text
MCPConnectorTemplate
  -> MCPConnector
      -> MCPWorkspaceConnectorState
      -> MCPCredentialGrant
```

### 3.1 `mcp_connectors`

`mcp_connectors` becomes the canonical org connector table.

It should keep:

- `id`
- `org_id`
- `template_id`
- `name`
- `slug_name`
- `server_url`
- `server_url_hash`
- `transport`
- `auth_method`
- `oauth_client_config`
- `static_auth_style`
- `static_auth_header_name`
- `static_auth_query_param`
- `tools_cache`
- `tool_citations`
- `discovery_status`
- `last_error`
- `status`
- `created_by_user_id`
- timestamps

Active uniqueness remains on org-level connector identity:

- active `(org_id, template_id)` when `template_id IS NOT NULL`
- active `(org_id, server_url_hash)`
- active `(org_id, slug_name)`

### 3.2 `mcp_workspace_connector_states`

Change the unique identity from workspace + install to workspace + connector.

Target fields:

- `id`
- `org_id`
- `workspace_id`
- `connector_id`
- `enabled`
- `credential_policy`
- `enablement_source`
- `updated_by_user_id`
- timestamps

Target uniqueness:

- `(workspace_id, connector_id)`

Remove `install_id` after all readers and writers use `connector_id`.

### 3.3 `mcp_credential_grants`

Change the grant owner from install to connector.

Target fields:

- `id`
- `org_id`
- `connector_id`
- `grant_scope`
- `workspace_id`
- `user_id`
- `credential_id`
- `refresh_credential_id`
- `expires_at`
- `grant_status`
- `created_by_user_id`
- timestamps

Target uniqueness:

- one org grant per connector
- one workspace grant per connector + workspace
- one user grant per connector + workspace + user

Remove `install_id` after OAuth, static credential, and runtime flows all use
`connector_id`.

### 3.4 `mcp_connector_installs`

Deprecate this table from the runtime model.

The cleanup has two acceptable endings:

1. Drop the table after migration if no audit requirement exists.
2. Keep it only as a historical table, with no runtime foreign keys and no new
   writes from product flows.

The recommended ending is to drop it. The project has not shipped publicly, and
keeping a misleading table name creates more future cost than the audit value it
provides.

## 4. Behavior

### 4.1 Org Admin Adds a Connector

The admin action should create or reuse one `mcp_connectors` row.

If the admin provides an org credential, write or update an org-scope grant for
that connector.

Rollout writes workspace state rows:

- all workspaces
- selected workspaces
- no workspaces yet

The action must not fail because a workspace already has its own credential for
the same connector.

### 4.2 Workspace Enables a Connector

The workspace action should upsert one workspace state row by
`workspace_id + connector_id`.

If the workspace chooses workspace credentials, write or update a workspace-scope
grant for the same connector.

If the workspace chooses user credentials, no shared workspace grant is required;
each user connects their own account.

### 4.3 User Connects Their Account

The user OAuth/static credential action should write or update a user-scope grant
for:

- connector
- workspace
- user

It should not create a new connector. It should not create an install row.

### 4.4 Runtime Resolution

Runtime effective state should start from:

- connector
- workspace state
- selected grant for the workspace policy and current user

Credential fallback is not implicit:

- `org` requires an org grant.
- `workspace` requires a workspace grant.
- `user` requires a user grant for the current user.
- `none` requires no grant.

If the selected grant is missing or expired, the connector is visible but not
usable for that actor.

### 4.5 Discovery

Discovery should update connector-level metadata:

- tools cache
- citations
- discovery status
- last error

Discovery should not update workspace state or credential grants except for
auth-related status caused by OAuth token refresh failures.

If a future MCP server returns different tools for different credentials, that is
a separate product feature. The current model treats tool identity as connector
metadata.

## 5. API Shape

Public API responses should make `connector_id` the stable identifier.

Admin APIs:

- list organization connectors
- add connector to organization
- configure org credential for connector
- configure rollout for connector
- remove or deactivate connector

Workspace APIs:

- list available connectors
- enable connector in workspace
- disable connector in workspace
- configure workspace credential policy
- connect workspace credential
- connect user credential

During migration, legacy routes may still accept `install_id`, but new route
logic should convert it to `connector_id` at the boundary and keep internal
service logic connector-centric.

## 6. Migration Strategy

This should be a follow-up PR after the credential layering PR lands.

### 6.1 Add Connector-Centric Columns and Constraints

The current layering PR already adds nullable `connector_id` to workspace states
and grants. The cleanup PR should make those columns required after backfill.

Steps:

1. Ensure every active workspace state has `connector_id`.
2. Ensure every active credential grant has `connector_id`.
3. Add new unique constraints on connector-centric keys.
4. Update code to write connector-centric rows.
5. Stop writing install-centric rows.
6. Drop install-centric constraints and columns when no readers remain.

### 6.2 Backfill

Existing install rows map as follows:

- active org install -> connector plus optional org grant and rollout states
- active workspace install -> connector plus workspace state plus workspace grant
- uninstalled install -> ignored by runtime cleanup

If an install row has no credential grant, it still creates connector/state data
as appropriate. Missing credentials are represented by effective-state reason,
not by absence of connector identity.

### 6.3 Cutover

Cut over services in this order:

1. Connector repository and service become the creation path.
2. Workspace state repository keys by connector.
3. Grant repository keys by connector.
4. OAuth start/callback carries connector id and target grant scope.
5. Effective runtime reads connector + state + grant.
6. Routes and frontend types expose connector id as the primary id.
7. `mcp_connector_installs` is dropped or marked historical.

## 7. Testing

Add e2e tests for business invariants:

- Org admin can add a connector already used by a workspace credential.
- Workspace can keep its own credential after org credential is added.
- Two workspaces can use different workspace credentials for the same connector.
- User credential policy requires each user to have their own grant.
- Runtime does not fall back from missing workspace/user grant to org grant.
- OAuth callback writes the selected scope grant for the connector.
- Discovery updates connector metadata and does not create install rows.

Add migration tests:

- active org installs backfill to connectors and org grants
- active workspace installs backfill to connector states and workspace grants
- tombstoned installs do not become runtime-visible connectors
- uniqueness is enforced at connector identity and credential-scope levels

## 8. Non-Goals

This cleanup should not add:

- admin force-overwrite of workspace credential policy
- per-user tool discovery cache
- per-workspace connector identity for the same org connector
- support for multiple active auth methods on one connector
- audit/event history unless product explicitly requires it

## 9. Open Questions

1. Should removing an org connector deactivate the connector for all workspaces,
   or only remove org credential availability while preserving workspace/user
   overrides?
2. Should custom connectors allow two active entries with the same URL but
   different auth methods, or is URL identity always unique inside an org?
3. Do we need durable audit history for admin add/remove actions before dropping
   `mcp_connector_installs`?

## 10. Recommendation

Keep the current credential layering PR focused on the 409 fix and connector
identity introduction.

Then implement this cleanup as a separate PR that makes `connector_id` the core
foreign key for workspace state, grants, OAuth, and runtime resolution. At the
end of that cleanup, `mcp_connector_installs` should no longer be a runtime
table. Prefer dropping it unless product requires audit history.
