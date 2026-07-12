# MCP OAuth Staging Manual Test Plan

OAuth flows are not E2E tested locally (see spec §11.3 — local Playwright
cannot drive real vendor consent screens, and tests against fake
authorization servers don't catch the integration risks that matter).
This document is the production-verification gate. It MUST be exercised
against staging before any release that ships catalog rows, OAuth route
changes, or token-manager changes.

## Prerequisites

- Staging environment with a real public hostname (no `*.lvh.me`,
  no `*.ngrok.io` for tier-1 release qual; ngrok is acceptable for
  exploratory checks).
- Test user accounts on every connector under qualification:
  Notion, GitHub, Linear, Asana, Atlassian, Sentry, Intercom,
  Cloudflare (Workers / Logs / Radar), Slack, Google Workspace.
- OAuth Apps registered in each vendor's developer console for the
  staging hostname. Redirect URI for every app:
  `${STAGING_PUBLIC_BASE_URL}/api/v1/oauth/mcp/callback`.
- Static `client_id` / `client_secret` env vars set on staging for
  non-DCR connectors (GitHub, Slack, Google Workspace) — see
  `backend/.env.example`.
- Seeder run after env var changes:
  `python -m cubeplex.cli seed-mcp-catalog`.
- A staging org with at least two workspaces and three users
  (one org admin, one workspace admin, one workspace member).

## Coverage matrix

For each connector under qualification, exercise scenarios A through D.
DCR-supporting connectors additionally run scenario E.

### Scenario A — Org admin install (org-wide)

1. Sign in as the org admin.
2. Navigate to `/admin/mcp`.
3. Click the connector card → Install Drawer.
4. Choose "Connect with OAuth".
5. Complete the vendor auth flow (consent screen → vendor redirects
   back to the cubeplex callback).
6. **Verify:** drawer dismisses, success toast, install row appears in
   `GET /api/v1/admin/mcp/catalog` with `installed_org_wide=true,
   authed=true`.
7. **Verify cross-workspace visibility:** in another workspace under
   the same org, `GET /api/v1/ws/{ws}/mcp/catalog` shows the
   connector as available (not installed-private, not disabled).

### Scenario B — Workspace user self-install

1. Sign in as a workspace member (not org admin).
2. Navigate to `/w/{wsId}/integrations/mcp`.
3. Click the same connector card → Install Drawer (workspace mode).
4. Choose OAuth → complete the vendor flow.
5. **Verify:** drawer dismisses, install row scoped to that
   workspace + that user (`owner_workspace_id={wsId},
   credential_scope='user'` or `workspace` per the connector default).
6. **Verify isolation:** another user in the same workspace does NOT
   see this user's install (or sees the server but not authed when
   `credential_scope=user`).

### Scenario C — Token refresh near expiry

1. Either wait until ~5 minutes before the access token's `expires_at`,
   or, in staging, manually advance `oauth_expires_at` on the install
   row to within the safety window via DB shell.
2. Trigger an agent run that calls a tool on the connector.
3. **Verify:** server logs show exactly one
   `OAuthTokenManager.refresh` call; the new access token is encrypted
   into a fresh credential row; the install row's `oauth_expires_at`
   advances; subsequent tool calls in the same agent run reuse the new
   token without further refreshes.

### Scenario D — Server-side revocation

1. In the vendor's connected-apps UI (e.g. GitHub → Settings →
   Applications), revoke the cubeplex app's access for the test user.
2. Trigger an agent run that calls a tool on the connector.
3. **Verify:** the refresh attempt fails (4xx from the AS); the install
   row flips to `authed=false` with `last_error` populated; the UI
   shows a "Reauthorize" affordance on the connector card.
4. Click "Reauthorize" → re-complete the vendor flow.
5. **Verify:** install flips back to `authed=true`; tool call now
   succeeds; old credential rows are no longer referenced.

### Scenario E — Dynamic Client Registration (DCR-supporting connectors only)

Run on the *first* install of a slug into an org (i.e. before any
install row references the catalog row).

1. Confirm the catalog row has `oauth_dcr_supported=true` and no
   pre-registered `oauth_static_client_id`.
2. Trigger Scenario A.
3. **Verify network:** the OAuth-start step hits the AS metadata's
   `/register` endpoint with the cubeplex redirect URI in
   `redirect_uris`.
4. **Verify DB:** the resulting install row's `oauth_client_config`
   contains a freshly minted `client_id`. The encrypted
   `client_secret` lives in a tenant-scoped credential vault row
   (not in any catalog row, not in plaintext anywhere — confirm via
   PG dump grep).

## Exit criteria

- **Tier 1 (release-blocking):** scenarios A, B, C, D pass for
  GitHub, Notion, Linear, Atlassian, Slack.
- **Tier 2:** scenarios A, B, D pass for Asana, Sentry, Intercom,
  Cloudflare (Workers / Logs / Radar), Google Workspace. Scenario C
  is best-effort; for connectors that issue long-lived tokens
  (no refresh) it is N/A.
- **Scenario E** must pass for at least one DCR connector per release
  that touches `cubeplex.mcp.oauth.dcr`.

A connector that fails scenario D (revocation handling) blocks
release, even on Tier 2 — silent token failure is the worst class of
regression here.

## Recording

Per-release results are captured in the release ticket:

- Connector × scenario matrix with pass / fail / N/A and a
  one-line note for each failure (HTTP status, log excerpt).
- Screenshots of the consent screen and post-callback UI for at
  least one Tier 1 connector per release.
- Links to staging server logs covering the test window.

This plan is NOT auto-runnable in CI. It runs as a release-qual gate
against a real staging environment with real vendor accounts.
