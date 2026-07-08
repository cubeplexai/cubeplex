# Design: MCP Connector Credential Layering

- **Date**: 2026-07-08
- **Status**: DRAFT — pending product review
- **Branch**: main
- **Related**:
  - `docs/dev/specs/2026-05-08-mcp-catalog-oauth-design.md`
  - `backend/docs/mcp_catalog_oauth.md`

## 1. Problem

The current MCP four-layer model uses the word **install** for two different
product concepts:

1. Making a connector available at a governance layer.
2. Providing credentials for that connector at org / workspace / user scope.

That conflation creates incorrect behavior. If a workspace has a private
Atlassian install, org admin install of the same Atlassian template returns
`409 install_already_exists`. From a product perspective this is wrong:

- Org admin install means: "the organization provides an org-level credential
  and default usage policy for this connector."
- Workspace usage may still choose not to use the org credential and instead
  use workspace or user credentials.
- Therefore org-level provisioning and workspace/user credential overrides are
  not duplicates. They are different credential sources for the same connector.

The current technical uniqueness rule has a valid safety goal: avoid duplicate
MCP tool identities and namespace collisions. But the rule is attached to the
wrong concept. It treats multiple credential scopes as duplicate connector
instances.

## 2. Product Definition

### 2.1 Connector

An MCP connector is an organization-level capability entry for an external MCP
service, usually derived from a catalog template.

Examples:

- Atlassian Rovo
- GitHub
- Notion
- Tavily

There should be one connector identity for a given org + template / URL / tool
namespace. This identity defines:

- template or custom server URL
- transport
- auth methods supported by the server
- tool discovery cache
- citation defaults
- runtime tool namespace

### 2.2 Credential Source

Credential source answers: "When a workspace/user invokes this connector, whose
authorization is used?"

Allowed sources:

- **Org credential** — provided by org admin; usable by workspaces that choose
  organization-managed access.
- **Workspace credential** — provided by workspace admin; shared by that
  workspace only.
- **User credential** — provided by each user; used for personal OAuth/account
  access.
- **None** — no credential required.

Credential source is not connector identity. Multiple credential sources can
exist for the same connector.

### 2.3 Workspace Connector State

Workspace connector state answers: "How does this workspace use this connector?"

It owns:

- enabled / disabled
- selected credential policy: `org`, `workspace`, `user`, or `none`
- whether the workspace is using the org default or an explicit override

Workspace state should not create a second connector identity.

### 2.4 Product Vocabulary

The product should avoid using "install" for every action.

Admin area:

- **Add to organization** — create or activate the org connector identity.
- **Provide org credential** — attach org-scoped credential or OAuth grant.
- **Set default access** — choose which workspaces are enabled by default and
  whether they use org credentials.

Workspace area:

- **Enable connector** — turn the connector on for the workspace.
- **Use organization credential** — select org credential source.
- **Use workspace credential** — provide a workspace-scoped credential.
- **Connect my account** — provide a user-scoped OAuth/token grant.

Implementation may keep API names such as `install` temporarily, but UI copy
and future schema names should reflect these concepts.

## 3. Desired Behavior

### 3.1 Org Admin Adds a Connector With No Existing Workspace Usage

1. Org admin opens **Admin > MCP Connectors**.
2. Atlassian appears as available.
3. Admin chooses **Add to organization**.
4. Admin chooses rollout:
   - make available only
   - enable selected workspaces
   - enable all workspaces
5. Admin completes OAuth or static credential setup if needed.
6. Workspaces can use the org credential unless they choose an allowed
   workspace/user override.

### 3.2 Org Admin Adds a Connector Already Used by a Workspace

If a workspace already uses Atlassian with a workspace credential:

1. Atlassian still appears in the admin catalog.
2. Admin can add Atlassian to the organization.
3. The existing workspace credential is preserved.
4. Existing workspace behavior is not silently changed.
5. Admin rollout only affects workspaces that do not already have an explicit
   workspace/user credential policy, unless the admin explicitly chooses a
   future "force org credential" action.

The org admin action must not return 409 simply because a workspace has its own
credential.

### 3.3 Workspace Chooses Not to Use Org Credential

If an org credential exists:

1. Workspace admin opens workspace **MCP** page.
2. Connector shows as available from organization.
3. Workspace can choose:
   - use org credential
   - provide workspace credential
   - require each user to connect their own account
   - disable in this workspace

This is an intended override path, not an error or duplicate install.

### 3.4 Multiple Workspaces Use Their Own Credentials

Multiple workspaces may each use workspace-scoped credentials for the same
connector identity.

Example:

- Engineering uses Atlassian workspace credential A.
- Support uses Atlassian workspace credential B.
- Org admin also provides org credential C for new workspaces.

Runtime selection is per workspace state:

- Engineering calls use A.
- Support calls use B.
- Newly enabled workspace calls use C unless overridden.

## 4. Invariants

The clean model keeps these invariants:

1. **Connector identity is unique.** Within an org, a template / URL / namespace
   corresponds to one connector identity.
2. **Credential sources are layered.** Org, workspace, and user credentials can
   coexist for the same connector identity.
3. **Workspace state selects the credential source.** Runtime never guesses
   between grants; it follows the workspace state and user context.
4. **Org rollout does not silently destroy overrides.** Explicit workspace/user
   credential choices survive org admin provisioning.
5. **Backend still protects namespace collisions.** Duplicate connector identity
   remains forbidden; duplicate credential sources do not.

## 5. Conceptual Data Model

The target model is:

```text
MCPConnectorTemplate
  -> MCPConnectorIdentity (org-owned connector capability)
      -> MCPWorkspaceConnectorState (enabled + credential policy)
      -> MCPCredentialGrant (org / workspace / user)
```

### 5.1 Connector Identity

Create a new `mcp_connectors` table. One row represents one connector identity
inside an organization: one catalog template or one custom connector namespace.

Fields:

- `org_id`
- `template_id` nullable for custom
- `name`
- `slug_name`
- `server_url`
- `server_url_hash`
- `transport`
- `auth_method`
- `tools_cache`
- `discovery_status`
- `status`
- `created_by_user_id`
- timestamps

This is the row that prevents duplicate template / URL / namespace collisions.
It is not an install action and it does not store workspace ownership.

Representative schema:

```sql
mcp_connectors (
  id text primary key,                      -- prefix mcpco
  org_id text not null references organizations(id),
  template_id text null references mcp_connector_templates(id),
  name text not null,
  slug_name text not null,
  server_url text not null,
  server_url_hash text not null,
  transport text not null,
  auth_method text not null,
  oauth_client_config jsonb not null default '{}',
  static_auth_style text not null default 'bearer',
  static_auth_header_name text null,
  static_auth_query_param text null,
  tools_cache jsonb not null default '[]',
  tool_citations jsonb not null default '{}',
  discovery_status text not null default 'not_run',
  last_error text null,
  status text not null default 'active',
  created_by_user_id text null references users(id),
  created_at timestamptz not null,
  updated_at timestamptz not null
)
```

Active uniqueness:

- `(org_id, template_id)` when `template_id IS NOT NULL AND status='active'`
- `(org_id, server_url_hash)` when `status='active'`
- `(org_id, slug_name)` when `status='active'`

### 5.2 Workspace State

One row per workspace + connector identity when the workspace has explicit
state.

Fields:

- `workspace_id`
- `connector_id`
- `enabled`
- `credential_policy`
- `enablement_source`
- `updated_by_user_id`

Absence may mean "not enabled" or "inherits org default" depending on the
rollout model. The implementation should make that interpretation explicit.

### 5.3 Credential Grants

Many grants per connector identity, scoped by:

- org
- workspace
- user, optionally workspace-lensed

The existing `MCPCredentialGrant` shape is close to this target. The key change
is that org/workspace/user grants should reference `mcp_connectors.id`, not
`mcp_connector_installs.id`.

During migration, `mcp_workspace_connector_states` and `mcp_credential_grants`
may temporarily keep legacy `install_id` columns for compatibility. New code
should write and read `connector_id`.

## 6. Current Model Gap

Current `MCPConnectorInstall` mixes connector identity and credential
provisioning:

- `install_scope='org'` means org-level connector row.
- `install_scope='workspace'` means workspace-private connector row.
- Both rows include template/server URL/auth/tool cache.
- Service uniqueness checks consider active rows across scopes.

That design prevents namespace duplication but blocks valid credential layering.

The model should evolve so that:

- `mcp_connectors` owns identity and tool namespace
- workspace rows own enablement/policy
- grants own credential material
- `mcp_connector_installs` is migrated and deprecated rather than redefined

## 7. Migration Strategy

There are two viable implementation paths.

### Option A — Create `mcp_connectors` and Normalize Existing Installs

Create the new identity table and migrate all active install rows into it.

For each active `mcp_connector_installs` row:

1. Create or reuse one `mcp_connectors` row for the same org + template / URL /
   namespace.
2. Convert workspace-scope installs into:
   - workspace state row pointing at the connector identity
   - workspace/user credential grants pointing at the connector identity
3. Convert org-scope installs into:
   - connector identity
   - org credential grants pointing at the connector identity
   - workspace state rows for existing rollout/access configuration
4. Tombstone or archive the old install rows.
5. Preserve existing workspace behavior.

After the migration, new write paths use `mcp_connectors`; the old install table
is not the source of truth.

Pros:

- Clean target model.
- Runtime has one connector identity.
- Long-term code becomes simpler.
- Future engineers do not have to remember that "install" actually means
  connector identity.

Cons:

- Requires careful migration code.
- Needs tests for credential preservation and tool cache handling.
- Touches more API/runtime code than reusing the old table.

### Option B — Allow Org and Workspace Install Rows to Coexist Temporarily

Keep both row types, but change runtime resolution so workspace-scope rows are
treated as credential overrides of the org connector.

Pros:

- Smaller immediate migration.
- Less risk to existing rows.

Cons:

- Keeps conceptual debt.
- Runtime must reconcile multiple rows for one connector.
- Namespace collision logic remains fragile.

### Recommendation

Use **Option A**. Do not rename `mcp_connector_installs` in place and do not
continue using it as the long-term identity table. The name encodes the wrong
product concept and will keep causing design mistakes.

## 8. API Semantics

### 8.1 Admin Add Connector

`POST /api/v1/admin/mcp/installs` can keep its route name initially, but product
semantics should be:

> Ensure the org has a connector identity for this template/custom connector,
> optionally attach org credential, and optionally create workspace state rows.

If matching workspace-private installs exist, the endpoint should not 409.
Instead it should normalize/adopt them according to the chosen migration
strategy.

Response should identify:

- connector id
- whether an existing identity was reused/adopted
- workspace overrides preserved
- any workspace states created by rollout

### 8.2 Workspace Enable Connector

Workspace create/connect endpoint semantics should be:

> Enable this connector in the workspace and set credential policy / grant.

If org connector identity exists, workspace action should not create a second
connector identity.

If org connector identity does not exist, workspace action may create connector
identity lazily, but the identity remains org-owned in the data model. The
workspace-specific part is state + credential, not identity.

### 8.3 Runtime Credential Resolution

For a given workspace/user and connector:

1. Load connector identity.
2. Load workspace state.
3. Determine effective credential policy.
4. Resolve grant:
   - `org` -> org grant
   - `workspace` -> workspace grant
   - `user` -> user grant for actor
   - `none` -> no grant
5. If required grant is missing, return a scoped missing-credential reason.

Runtime must not choose a workspace install over org install by row ordering.
It should follow explicit state.

## 9. UI Requirements

### 9.1 Admin MCP Page

Admin catalog should show templates regardless of workspace credential usage.

Template states:

- **Available** — no connector identity or org provisioning yet.
- **Added to organization** — org connector exists.
- **Used in workspaces** — workspace states/grants exist, but no org credential
  or default access has been configured.
- **Needs credential** — connector identity exists but selected policy requires
  a missing credential.

Admin detail actions:

- Add to organization
- Provide / replace org credential
- Enable selected workspaces
- Enable all workspaces
- View workspace overrides

Admin UI should not hide a template only because workspaces use their own
credentials.

### 9.2 Workspace MCP Page

Workspace detail should make credential source explicit:

- Current source: Organization / Workspace / My account / None
- Current grant status
- Action to switch source, subject to permission
- Action to disable connector in workspace

Workspace users should understand whether calls use a shared org credential or
their own account.

## 10. Permission Model

- Org admin can:
  - add connector to organization
  - provide org credential
  - set org defaults
  - enable/disable connector for workspaces
  - view that workspace overrides exist

- Workspace admin can:
  - enable/disable in workspace
  - choose workspace credential policy if allowed
  - provide workspace credential

- Workspace member can:
  - provide user credential when policy is user
  - disconnect their own user credential

Org admin should not silently overwrite workspace credentials without an
explicit force/override action.

## 11. Testing Strategy

Backend tests:

- Org add succeeds when workspace credential already exists for same template.
- Existing workspace credential remains active after org add.
- Runtime uses workspace credential when workspace policy is `workspace`.
- Runtime uses org credential when workspace policy is `org`.
- User policy does not fall back to org/workspace grant.
- Duplicate connector identity is still rejected for true duplicate custom
  connector identity, not for credential layering.

Frontend tests:

- Admin catalog shows templates used by workspaces.
- Admin add flow does not disappear because of workspace usage.
- Workspace detail shows credential source and switching options.
- Missing credential band points to correct scope.

Migration tests:

- Existing workspace-scope installs become workspace state + grants without
  losing credentials.
- Tombstoned workspace installs do not produce active runtime tools.
- Tool namespace remains stable.

## 12. Rollout Plan

1. Freeze current UI workaround work. Do not ship template hiding as the final
   behavior.
2. Write migration plan for existing workspace-scope MCP installs.
3. Implement backend identity/state/grant normalization.
4. Update admin install endpoint to adopt/normalize instead of 409 for
   credential-layering cases.
5. Update workspace install endpoint to create state/grants against connector
   identity.
6. Update runtime credential resolution to depend on state, not competing
   install rows.
7. Update admin/workspace UI copy from generic "install" toward add/enable/use
   credential language.
8. Remove compatibility paths after existing rows are migrated.

## 13. Open Questions

1. Should workspace-created connector identity be visible in admin immediately
   as "Used in workspaces" even before org credential exists?
   - Recommended: yes.

2. Can org admin force all workspaces to use org credential?
   - Recommended for later, not first version. It needs strong UI warnings and
     audit logging.

3. If multiple workspace credentials exist before org add, should org add
   automatically normalize all of them?
   - Recommended: yes, because they are credential grants for the same
     connector identity. Do not force them to choose one canonical credential.

4. Should custom connectors follow the same layering model?
   - Recommended: yes when URL/name namespace matches. Custom connectors still
     need one org connector identity and layered credentials.

5. Should tool discovery cache be shared across all credential sources?
   - Recommended: connector identity owns the cache, but discovery may need a
     chosen credential lens. Store the last successful cache and the credential
     scope used to discover it for diagnostics.
