# MCP Catalog + OAuth (M2)

This document describes how cubeplex manages and runs remote MCP
connectors. It is a working reference for operators and engineers; the
authoritative source for design decisions remains the spec at
`docs/dev/specs/2026-05-08-mcp-catalog-oauth-design.md`.

## Architecture

Four layers, each with distinct ownership and lifecycle:

1. **Template layer (system-wide catalog).** `mcp_connector_templates`
   rows define what connectors are available: server URL, transport,
   `auth_method`, OAuth metadata (DCR flag, default scope, optional
   pre-registered client credentials), and static-form schema. These are
   immutable from a tenant's perspective — org admins cannot edit them.
   The list is materialized from a Python source-of-truth
   (`cubeplex.mcp.catalog_seed.CATALOG`) via an explicit deploy step
   (see "Deploy: catalog seed step" below).

2. **Connector layer (org-scoped).** `mcp_connectors` rows are created
   when an admin distributes a template to the org (via
   `POST /api/v1/admin/mcp/templates/{template_id}/distribute`). One
   connector per `(org_id, template_id)`. The connector owns tool
   discovery state and the `auto_enroll_new_workspaces` flag. It does
   not store `auth_method` or credential-level status — those live on
   the template and grants respectively.

3. **Workspace-connector state layer (workspace-scoped).** `mcp_workspace_connector_states`
   rows track whether a given connector is enabled or disabled in a
   specific workspace. Org admins can force-disable a template for the
   whole org (`PUT /admin/mcp/templates/{id}/disable`); workspace
   members can toggle individual connectors via the catalog UI.

4. **Credential grant layer (scope-keyed).** `mcp_credential_grants`
   rows hold the actual credentials, one row per
   `(connector_id, grant_scope)`. Scope is `org`, `workspace`, or
   `user`. For OAuth connectors, the grant points at the encrypted
   access-token and refresh-token credentials; `grant.grant_status`
   is the canonical auth signal (`valid` / `expired` / `pending`).

The runtime (`cubeplex/mcp/runtime.py`) walks all four layers when
building the tool list for a workspace turn: templates describe what
exists, connectors describe what the org has adopted, workspace-state
rows determine which are enabled, and grants supply the credentials.

## Data model

Field-level details live in spec §4; this is the operator-level summary.

| Table | Purpose |
| --- | --- |
| `mcp_connector_templates` | System catalog. Keyed by `slug`. Holds `auth_method`, OAuth metadata, and (for static-client connectors) a FK to a system-level `Credential` row storing the encrypted client secret. |
| `mcp_connectors` | Org-owned connector identity, one per `(org_id, template_id)`. Holds tool discovery state (`tools_cache`, `discovery_status`, `last_error`), `status`, and `auto_enroll_new_workspaces`. No `auth_method` — inherited from the template. |
| `mcp_workspace_connector_states` | Per-workspace enablement, keyed by `(connector_id, workspace_id)`. Tracks org-level disable overrides (`org_disabled`) and workspace-member toggles. |
| `mcp_connector_template_settings` | Per-org template-level overrides (e.g., org-wide disable). One row per `(org_id, template_id)`. |
| `mcp_credential_grants` | Per-scope credentials, one row per `(connector_id, grant_scope)`. Scope = `org | workspace | user`. `grant_status` (`valid` / `expired` / `pending`) is the canonical auth signal. User-scope rows also hold the OAuth refresh-token credential id and `oauth_expires_at`. |

The credential vault (`credentials` table) is shared across kinds. MCP-related
kinds: `mcp_oauth_client_secret` (system row, `org_id IS NULL`),
`mcp_oauth_access_token`, `mcp_oauth_refresh_token`, and `mcp_static_token`
for non-OAuth connectors.

## API endpoints

High-level summary; route bodies live in
`backend/cubeplex/api/routes/v1/`. All scoped routes require workspace
membership; admin routes require org admin.

### Admin routes (`/api/v1/admin/mcp/...`)

- **Catalog:** `GET /api/v1/admin/mcp/catalog` — same composition as
  the workspace catalog below, but scoped to the org (not a specific
  workspace). Returns all templates with per-org install status. This
  is the canonical discovery surface for the admin UI; it replaces the
  former `/admin/mcp/templates` list endpoint.
- **Template create/delete:** `POST` / `DELETE /api/v1/admin/mcp/templates/{template_id}` —
  add or remove a custom (non-catalog) template. Catalog-seeded
  templates are managed via the seeder, not via the API.
- **Distribute:** `POST /api/v1/admin/mcp/templates/{template_id}/distribute` —
  creates an `mcp_connectors` row for the org if one doesn't exist,
  and upserts workspace-state rows for all workspaces that should
  receive it (controlled by `auto_enroll_new_workspaces`).
- **Org-level disable / re-enable:** `PUT` / `DELETE /api/v1/admin/mcp/templates/{template_id}/disable` —
  writes an `mcp_connector_template_settings` row that disables the
  template for every workspace in the org.
- **Purge:** `POST /api/v1/admin/mcp/templates/{template_id}/purge` —
  deletes the connector, all workspace states, and all grants. Use with
  care; credential vault rows for existing grants are also removed.
- **Install read / update:** `GET` / `PATCH /api/v1/admin/mcp/installs/{connector_id}` —
  fetch or update a connector row (e.g., patch `auto_enroll_new_workspaces`).
- **Org credential grants:**
  `POST` / `DELETE /api/v1/admin/mcp/installs/{connector_id}/grants/org` —
  create or revoke the org-scope static credential grant.
  `POST /api/v1/admin/mcp/installs/{connector_id}/grants/org/oauth/start` —
  begin OAuth for the org-scope grant (returns `{ authorization_url }`).

### Workspace routes (`/api/v1/ws/{ws}/mcp/...`)

- **Catalog:** `GET /api/v1/ws/{ws}/mcp/catalog` — per-`(workspace, user)`
  view. Each entry carries `connector_added`, `enabled`, `org_disabled`,
  and (for OAuth) `authed`. The canonical discovery surface for the
  workspace settings UI; it replaces the former `/ws/{ws}/mcp/templates`
  list endpoint.
- **Template create / promote:**
  `POST /api/v1/ws/{ws}/mcp/templates` — create a workspace-private
  custom template and immediately distribute it to this workspace.
  `POST /api/v1/ws/{ws}/mcp/templates/{template_id}/promote` — escalate
  a workspace-private template to org scope (admin-only within a
  workspace context).
- **Workspace-connector state:**
  `PUT /api/v1/ws/{ws}/mcp/templates/{template_id}/state` — toggle
  enable / disable for this workspace.
- **Connectors list:** `GET /api/v1/ws/{ws}/mcp/connectors` — list of
  `MCPConnector` rows active for the workspace (status fields only;
  no credential details).
- **Active tools:** `GET /api/v1/ws/{ws}/mcp/active-tools` — flattened
  list of tools from all enabled connectors in the workspace; used by
  the agent runtime to build the tool manifest.
- **Workspace credential grants:**
  `POST` / `DELETE /api/v1/ws/{ws}/mcp/installs/{connector_id}/grants/workspace` —
  workspace-scope static grant.
  `POST /api/v1/ws/{ws}/mcp/installs/{connector_id}/grants/workspace/oauth/start` —
  OAuth start for the workspace-scope grant.
- **User credential grants:**
  `POST` / `DELETE /api/v1/ws/{ws}/mcp/installs/{connector_id}/grants/me` —
  user-scope static grant.
  `POST /api/v1/ws/{ws}/mcp/installs/{connector_id}/grants/me/oauth/start` —
  OAuth start for the user-scope grant.

### OAuth callback (shared)

- `GET /api/v1/oauth/mcp/callback` — single unscoped callback.
  Demultiplexes via signed state. Exchanges the code, writes vault rows,
  sets `grant.grant_status = "valid"`, runs tool discovery, and 302s to
  `${frontend_base_url}/oauth/mcp/return`.

## OAuth flow

```
[start]    POST  /api/v1/{admin|ws}/mcp/installs/{connector_id}/grants/{scope}/oauth/start
           ├─ look up connector → template (for auth_method, OAuth metadata)
           ├─ if oauth_dcr_supported: discover AS metadata, register a
           │  client at /register, encrypt new client_secret into a
           │  tenant-scoped credential
           ├─ else: use template's pre-registered client_id +
           │  decrypt the system-level client_secret credential
           ├─ generate state (signed), PKCE pair (verifier in redis),
           │  callback ticket (HttpOnly cookie)
           └─ return { authorization_url }

[browser]  302 to authorization_url, vendor consent UI

[callback] GET  /api/v1/oauth/mcp/callback?code=...&state=...
           ├─ verify state signature, age, actor matches ticket cookie
           ├─ exchange code+verifier at the token endpoint
           ├─ encrypt access_token (+ refresh_token if returned) into
           │  vault rows, link via MCPCredentialGrant row
           ├─ set grant.grant_status = "valid", run tool discovery
           └─ 302 to ${frontend_base_url}/oauth/mcp/return

[runtime]  agent step, request tools for a workspace
           ├─ resolve mcp_connectors rows for the workspace
           ├─ check MCPWorkspaceConnectorState (enabled?)
           ├─ pick the right MCPCredentialGrant by scope (user > workspace > org)
           └─ if access_token expired: refresh (see below)
```

### Static-client vs DCR

- **DCR-supported connectors** (Notion, Linear, Atlassian, Asana,
  Sentry, Intercom, Cloudflare, Google Workspace*): no env vars
  required. The first add hits the AS `/register` endpoint and
  stores the resulting `client_id` plus an encrypted `client_secret`
  on the connector row's `oauth_client_config`.
- **Static-client connectors** (GitHub, Slack, Google Workspace): the
  vendor does not expose DCR. An OAuth App must be registered out-of-
  band in the vendor's developer console, the `client_id` and
  `client_secret` placed in env vars, and the seeder invoked once to
  load them into the system catalog row.

(* GWS supports neither DCR nor a public well-known; treat as
static-client.)

## Token refresh

`cubeplex.mcp.oauth.token_manager.OAuthTokenManager` encapsulates the
read / refresh / persist cycle for OAuth-scoped connectors. Behavior:

- On every tool call that needs an OAuth bearer, the runtime asks the
  manager for a "valid for at least 60s" access token.
- If the cached token is within the safety window of `oauth_expires_at`
  (default 60s pre-expiry), the manager calls the AS `/token`
  endpoint with `grant_type=refresh_token`, encrypts the new pair, and
  swaps in the new credential ids inside a single DB transaction.
- If refresh fails (HTTP 4xx, network error, etc.), the manager sets
  `grant.grant_status = "expired"`, records `last_error` on the
  connector, and surfaces a reauthorization-required signal to the UI;
  the user gets a "Reconnect" prompt at the next interaction with the
  connector.
- **Early revocation (401 with a valid-looking grant).** Providers may
  invalidate an access token before the `expires_in` they reported
  (Cloudflare did, 2026-07-17). When the MCP server answers 401 even
  though `oauth_expires_at` is still in the future, both discovery and
  runtime tool calls force one refresh
  (`get_access_token_for_grant(force_refresh=True)`) and retry the
  request once. Concurrent forced refreshes collapse to a single
  rotation via the redis lock plus an `expires_at` snapshot check. If
  the refresh fails — or the server rejects even the fresh token — the
  grant flips to `expired` and the UI shows Reconnect; discovery
  records `last_error` starting with `oauth_reauthorization_required:`
  for the refresh-failure case.

## Catalog seeder

`backend/cubeplex/mcp/catalog_seed.py` defines the source-of-truth
`CATALOG` list and the idempotent `seed_catalog()` routine.

Invocation (from `backend/`):

```bash
python -m cubeplex.cli seed-mcp-templates            # apply
python -m cubeplex.cli seed-mcp-templates --dry-run  # preview only
python -m cubeplex.cli seed-mcp-templates --quiet    # warnings only
```

The seeder:

1. Reads static `client_id` / `client_secret` env vars for non-DCR
   connectors. The convention is
   `CUBEPLEX_MCP_OAUTH__<SLUG>__CLIENT_ID` and
   `CUBEPLEX_MCP_OAUTH__<SLUG>__CLIENT_SECRET` (slug uppercased,
   `-` mapped to `_`). Missing vars cause the connector to be skipped
   with a warning; the rest of the run continues.
2. Upserts each `CATALOG` entry into `mcp_catalog_connectors` keyed by
   `slug`. For static-client connectors, also encrypts the secret into
   a system-level `Credential` row (`org_id IS NULL`,
   `kind = mcp_oauth_client_secret`) and links it via
   `oauth_static_client_secret_credential_id`.
3. Marks any DB row whose slug is no longer in the in-code list as
   `status='deprecated'`. The row is preserved so existing connectors
   keep working; new connectors from deprecated slugs are rejected by
   the API.

## Deploy: catalog seed step

Catalog rows are NOT auto-loaded at app startup. After every release
that includes catalog changes:

```bash
cd backend
alembic upgrade head
python -m cubeplex.cli seed-mcp-templates
```

The seeder is idempotent: re-running with no changes is a no-op.

Static OAuth `client_id` / `client_secret` pairs (GitHub, Slack,
Google Workspace) are read from environment variables
`CUBEPLEX_MCP_OAUTH__<SLUG>__CLIENT_ID` and `…__CLIENT_SECRET`. If
unset, the seeder skips those connectors with a warning and continues
with the rest.

`CUBEPLEX_PUBLIC_BASE_URL` and `CUBEPLEX_FRONTEND_BASE_URL` MUST be set
to real public origins in production. These drive the `redirect_uri`
sent to authorization servers and the post-callback browser
redirect; OAuth flows fail (or break the user's session) if either
points at `localhost` in a deployed environment.

Run `python -m cubeplex.cli seed-mcp-templates --dry-run` to see what
would change without committing.

## Operator runbook

### Adding a new catalog connector

1. Add a `CatalogSeedEntry` to `CATALOG` in
   `backend/cubeplex/mcp/catalog_seed.py`.
2. If the vendor doesn't support DCR, register an OAuth App in the
   vendor's developer console with redirect URI
   `${CUBEPLEX_PUBLIC_BASE_URL}/api/v1/oauth/mcp/callback`. Place the
   credentials in env (see env-var convention above).
3. If the vendor exposes OAuth authorization-server metadata but does
   not expose the MCP protected-resource metadata endpoint, add
   `oauth_authorization_server_metadata_url` to the template's
   `template_metadata`. OAuth start first tries the standard MCP
   protected-resource discovery path and uses this URL only as a
   provider compatibility fallback.
4. Deploy the new code.
5. Run the seeder: `python -m cubeplex.cli seed-mcp-templates`.
6. Verify `GET /api/v1/ws/{ws}/mcp/catalog` (from any workspace) lists
   the new template with `status="active"`.
7. (Optional) Distribute to the org: `POST /api/v1/admin/mcp/templates/{template_id}/distribute`.

### Removing a catalog connector

1. Delete the entry from `CATALOG`.
2. Deploy and run the seeder. The slug is marked
   `status="deprecated"`; existing connectors continue to work, new
   connectors from this slug are blocked at the API layer.
3. (Optional, follow-up release.) Audit existing connectors from that
   slug and notify owners; do NOT delete `mcp_catalog_connectors` rows
   directly — connector rows reference them by FK.

### Rotating an OAuth App's client secret

1. In the vendor's developer console, generate a new client secret.
2. Update the corresponding `CUBEPLEX_MCP_OAUTH__<SLUG>__CLIENT_SECRET`
   env var.
3. Re-run the seeder: it re-encrypts and updates the system-level
   credential row in place. New OAuth flows immediately use the new
   secret; existing credential grants are unaffected (refresh tokens
   were minted by the AS, not this secret).

### Troubleshooting

- **Seeder skips a connector**: check the warning line for the missing
  env var name. The seeder's exit code is 0 even on partial-skip;
  check the JSON summary on stdout for `skipped > 0`.
- **OAuth callback 4xx**: confirm the redirect URI registered with
  the vendor exactly matches `${CUBEPLEX_PUBLIC_BASE_URL}/api/v1/oauth/mcp/callback`.
  Path mismatch (trailing slash, scheme) is the most common cause.
- **Refresh fails repeatedly**: a server-side revocation by the user
  is the typical cause. The connector row's `last_error` will record
  the AS response. The UI surfaces a "Reauthorize" affordance —
  clicking re-enters the OAuth start route and overwrites the
  credential.
- **`last_error` starts with `oauth_reauthorization_required:`**: the
  MCP server rejected the access token with 401 before its recorded
  expiry, and the automatic forced refresh also failed (see "Token
  refresh" → early revocation). The grant is already `expired`; the
  user must reconnect. A plain 401 in `last_error` without that prefix
  means the failure predates the retry, or auth isn't OAuth — check
  the grant row.
- **Tools missing after distributing a connector**: discovery runs once
  on distribute and again after each successful OAuth grant. Re-trigger
  via `POST /api/v1/admin/mcp/installs/{connector_id}/refresh-discovery`;
  the tools cache is repopulated from the server's `tools/list` response.

## Manual staging verification

OAuth flows are intentionally not E2E tested locally (see spec §11.3 —
local E2E cannot fake real vendor consent screens). The staging-test
plan in `backend/docs/mcp_oauth_staging_test_plan.md` is the
production-verification gate; it MUST be exercised against staging
before any release that ships catalog or OAuth changes.
