# MCP Catalog + OAuth (M2)

This document describes how cubebox manages and runs remote MCP
connectors. It is a working reference for operators and engineers; the
authoritative source for design decisions remains the spec at
`docs/dev/specs/2026-05-08-mcp-catalog-oauth-design.md`.

## Architecture

There are two layers:

1. **Catalog layer (system-wide).** A curated list of available
   connectors lives in `mcp_catalog_connectors`. Catalog rows are
   templates: server URL, transport, supported auth methods, OAuth
   metadata (DCR-supported flag, default scope, optional pre-registered
   client credentials), and static-form schema. They contain no
   tenant-scoped credentials and no tools. The list is materialized
   from a Python source-of-truth (`cubebox.mcp.catalog_seed.CATALOG`)
   via an explicit deploy step (see "Deploy: catalog seed step" below).

2. **Connector identity layer (org-owned).** When an admin adds a
   catalog connector, an `mcp_connectors` row is created. This is the
   connector identity — org-owned, keyed by `connector_id`, and shared
   across workspaces. Workspace state and credential grants reference
   this identity:
   - **`MCPWorkspaceConnectorState`** — per-workspace enablement,
     keyed by `connector_id`. Controls whether a workspace sees the
     connector's tools.
   - **`MCPCredentialGrant`** — per-scope credential pointers, keyed
     by `connector_id`. Credentials can be scoped to the org, a
     workspace, or an individual user; there is no implicit fallback
     between scopes.

The runtime (`cubebox/mcp/runtime.py`) walks both layers when listing
connectors for a workspace: catalog rows describe what *could* be
added, connector rows describe what *is* added, and the workspace
state rows determine which connectors are enabled for a given
workspace.

## Data model

Field-level details live in spec §4; this is the operator-level summary.

| Table | Purpose |
| --- | --- |
| `mcp_connector_templates` | System catalog. Keyed by `slug`. Holds the connector template, OAuth metadata, and (for static-client connectors) a FK to a system-level `Credential` row that stores the encrypted client secret. |
| `mcp_connector_installs` | A specific install. References a catalog row (`template_id`). Stores the chosen `auth_method`, `credential_scope`, the tools cache, and last-discovered metadata. |
| `mcp_connectors` | The connector identity table. Org-owned, keyed by `connector_id`. Groups credentials and per-workspace state under a single identity shared across workspaces. |
| `mcp_workspace_connector_states` | Per-workspace enablement state, keyed by `connector_id`. Controls whether a workspace sees and can use the connector's tools. |
| `mcp_credential_grants` | Per-scope credential pointers, keyed by `connector_id`. One row per `(connector_id, scope)`. FKs into `credentials` (the credential vault). User-scope rows additionally hold the OAuth refresh-token credential id and `oauth_expires_at`. |

The credential vault (`credentials` table) is shared across kinds. New
MCP-related kinds: `mcp_oauth_client_secret` (system row, `org_id IS
NULL`), `mcp_oauth_access_token`, `mcp_oauth_refresh_token`, and the
existing `mcp_static_token` for non-OAuth connectors.

## API endpoints

High-level summary; route bodies live in
`backend/cubebox/api/routes/v1/`.

- **Catalog (workspace):** `GET /api/v1/ws/{ws}/mcp/catalog` — the
  single catalog read endpoint. The catalog is intentionally
  workspace-scoped (not exposed under `/admin/...`) because each entry
  carries per-`(workspace, user)` status fields — `connector_added`,
  `enabled`, `disabled`, and (for OAuth) `authed`
  — that only make sense in workspace context. Org admins use the same
  endpoint by selecting a workspace.
- **Static add:** `POST /api/v1/admin/mcp/installs` and
  `POST /api/v1/ws/{ws}/mcp/installs` — create an `mcp_connectors`
  row, encrypt the static credential, kick off discovery.
- **OAuth start:** `POST /api/v1/admin/mcp/installs/oauth/start` and
  `POST /api/v1/ws/{ws}/mcp/installs/oauth/start` — return the
  authorization URL with PKCE + state. The callback ticket is set as
  an HttpOnly cookie scoped to `/api/v1/oauth/mcp/callback`.
- **OAuth callback:** `GET /api/v1/oauth/mcp/callback` — single,
  unscoped callback that demultiplexes via state. Exchanges the code,
  writes vault rows, finalizes the connector, and 302s back to
  `${frontend_base_url}/oauth/mcp/return`.
- **Disable / enable:** per-workspace enablement state for org-owned
  connectors.

All scoped routes require workspace membership; admin routes require
org admin.

## OAuth flow

```
[start]    POST  /api/v1/{admin|ws}/mcp/installs/oauth/start
           ├─ resolve catalog row (slug → server_url, oauth metadata)
           ├─ if oauth_dcr_supported: discover AS metadata, register
           │  a client at /register, encrypt the new client_secret into
           │  a tenant-scoped credential
           ├─ else: use catalog row's pre-registered client_id +
           │  decrypt the system-level client_secret credential
           ├─ generate state (signed), PKCE pair (verifier in redis),
           │  callback ticket (HttpOnly cookie)
           └─ return { authorization_url }

[browser]  302 to authorization_url, vendor consent UI

[callback] GET  /api/v1/oauth/mcp/callback?code=...&state=...
           ├─ verify state signature, age, actor matches ticket cookie
           ├─ exchange code+verifier at the token endpoint
           ├─ encrypt access_token (+ refresh_token if returned) into
           │  vault rows, link via credential grant on the connector
           ├─ flip authed=true, run tool discovery, populate tools_cache
           └─ 302 to ${frontend_base_url}/oauth/mcp/return?install_id=...

[runtime]  agent step, request tools for a workspace
           ├─ resolve mcp_connectors rows for the workspace
           ├─ check MCPWorkspaceConnectorState (enabled?)
           ├─ pick the right MCPCredentialGrant by scope
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

`cubebox.mcp.oauth.token_manager.OAuthTokenManager` encapsulates the
read / refresh / persist cycle for OAuth-scoped connectors. Behavior:

- On every tool call that needs an OAuth bearer, the runtime asks the
  manager for a "valid for at least 60s" access token.
- If the cached token is within the safety window of `oauth_expires_at`
  (default 60s pre-expiry), the manager calls the AS `/token`
  endpoint with `grant_type=refresh_token`, encrypts the new pair, and
  swaps in the new credential ids inside a single DB transaction.
- If refresh fails (HTTP 4xx, network error, etc.), the manager flips
  `mcp_connectors.authed = false`, records `last_error`, and surfaces a
  reauthorization-required signal to the UI; the user gets a
  "Reconnect" prompt at the next interaction with the connector.

## Catalog seeder

`backend/cubebox/mcp/catalog_seed.py` defines the source-of-truth
`CATALOG` list and the idempotent `seed_catalog()` routine.

Invocation (from `backend/`):

```bash
python -m cubebox.cli seed-mcp-templates            # apply
python -m cubebox.cli seed-mcp-templates --dry-run  # preview only
python -m cubebox.cli seed-mcp-templates --quiet    # warnings only
```

The seeder:

1. Reads static `client_id` / `client_secret` env vars for non-DCR
   connectors. The convention is
   `CUBEBOX_MCP_OAUTH__<SLUG>__CLIENT_ID` and
   `CUBEBOX_MCP_OAUTH__<SLUG>__CLIENT_SECRET` (slug uppercased,
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
python -m cubebox.cli seed-mcp-templates
```

The seeder is idempotent: re-running with no changes is a no-op.

Static OAuth `client_id` / `client_secret` pairs (GitHub, Slack,
Google Workspace) are read from environment variables
`CUBEBOX_MCP_OAUTH__<SLUG>__CLIENT_ID` and `…__CLIENT_SECRET`. If
unset, the seeder skips those connectors with a warning and continues
with the rest.

`CUBEBOX_PUBLIC_BASE_URL` and `CUBEBOX_FRONTEND_BASE_URL` MUST be set
to real public origins in production. These drive the `redirect_uri`
sent to authorization servers and the post-callback browser
redirect; OAuth flows fail (or break the user's session) if either
points at `localhost` in a deployed environment.

Run `python -m cubebox.cli seed-mcp-templates --dry-run` to see what
would change without committing.

## Operator runbook

### Adding a new catalog connector

1. Add a `CatalogSeedEntry` to `CATALOG` in
   `backend/cubebox/mcp/catalog_seed.py`.
2. If the vendor doesn't support DCR, register an OAuth App in the
   vendor's developer console with redirect URI
   `${CUBEBOX_PUBLIC_BASE_URL}/api/v1/oauth/mcp/callback`. Place the
   credentials in env (see env-var convention above).
3. If the vendor exposes OAuth authorization-server metadata but does
   not expose the MCP protected-resource metadata endpoint, add
   `oauth_authorization_server_metadata_url` to the template's
   `template_metadata`. OAuth start first tries the standard MCP
   protected-resource discovery path and uses this URL only as a
   provider compatibility fallback.
4. Deploy the new code.
5. Run the seeder: `python -m cubebox.cli seed-mcp-templates`.
6. Verify `GET /api/v1/ws/{ws}/mcp/catalog` (from any workspace) lists
   the new connector with `status="active"`.

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
2. Update the corresponding `CUBEBOX_MCP_OAUTH__<SLUG>__CLIENT_SECRET`
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
  the vendor exactly matches `${CUBEBOX_PUBLIC_BASE_URL}/api/v1/oauth/mcp/callback`.
  Path mismatch (trailing slash, scheme) is the most common cause.
- **Refresh fails repeatedly**: a server-side revocation by the user
  is the typical cause. The connector row's `last_error` will record
  the AS response. The UI surfaces a "Reauthorize" affordance —
  clicking re-enters the OAuth start route and overwrites the
  credential.
- **Tools missing after adding a connector**: discovery runs once when
  the connector is added or reauthorized. Re-trigger by hitting
  `POST /api/v1/admin/mcp/installs/{install_id}/refresh-discovery`
  or `POST /api/v1/ws/{ws}/mcp/installs/{install_id}/refresh-discovery`;
  the tools cache is repopulated from the server's `tools/list`
  response.

## Manual staging verification

OAuth flows are intentionally not E2E tested locally (see spec §11.3 —
local E2E cannot fake real vendor consent screens). The staging-test
plan in `backend/docs/mcp_oauth_staging_test_plan.md` is the
production-verification gate; it MUST be exercised against staging
before any release that ships catalog or OAuth changes.
