# MCP OAuth: react to 401 with a forced token refresh

## Goal

When an MCP server rejects an access token with `401 Unauthorized` even
though the grant's recorded `expires_at` says the token is still valid,
force one token refresh and retry the request; if the refresh fails, or
the server still rejects the refreshed token, mark the grant `expired`
so the UI surfaces the Reconnect prompt.

## Context

Production incident (2026-07-17, `cloudflare-api` connector): Cloudflare
issued an access token claiming a 16-hour lifetime (`expires_in=57600`),
then started rejecting it with 401 roughly 2.5 hours **before** the
recorded expiry. The same server's newer tokens carry a 1-hour lifetime,
so this was a provider-side TTL/revocation change — which providers are
allowed to do at any time: `expires_in` is a hint, not a contract.

Today every consumer of an OAuth grant trusts `expires_at` blindly:

- `OAuthTokenManager.get_access_token_for_grant` refreshes only when
  `now` is within 60s of `expires_at` (`_needs_refresh`); otherwise it
  returns the cached token, even if the server has revoked it.
- `discover_tools_for_install` (also reached by the background
  tools-cache TTL refresh) catches the 401, persists it into
  `connector.last_error`, and the UI shows a sticky
  "Discovery error, HTTPStatusError: … 401 …" banner. The grant stays
  `valid`, so even a manual Retry re-sends the same dead token.
- The runtime tool loader (`_load_tools_for_specs`) bakes the resolved
  `Authorization` header into each tool's call closure at turn start;
  every tool call in the turn then fails with 401.

Nothing in the system reacts to a 401. The connector stays broken until
the wall clock finally passes `expires_at` (hours later), and the error
banner stays up even after that.

## Approaches considered

1. **401-triggered forced refresh at the consumption points (chosen).**
   Add a `force_refresh` mode to `OAuthTokenManager`; on a 401, the
   discovery path and the tool-call path force one refresh and retry
   once. Recovers within the same operation; refresh cost is paid only
   when a server actually rejects a token.
2. **Mark the grant expired on 401 and let the existing time-based
   machinery refresh on next use.** Simpler, but the current operation
   still fails, the user sees one error per revocation, and the runtime
   currently *falls back to the cached token* when a grant is expired-
   but-refreshable — so the retry semantics would stay muddy.
3. **Stop trusting long `expires_in` values (cap the recorded lifetime
   at e.g. 15 minutes).** Refreshes far more often for all providers,
   still cannot handle mid-lifetime revocation, and burns refresh-token
   rotations for nothing.

## Design

### 1. `OAuthTokenManager` grows a forced-refresh mode

`get_access_token_for_grant(..., force_refresh: bool = False)`.

- `force_refresh=False`: behavior unchanged (time-based check).
- `force_refresh=True`: skip the `_needs_refresh` early return and go
  straight to the locked refresh path.
- Double-rotation guard: the caller's `grant.expires_at` snapshot is
  compared with the re-read row inside the redis lock. If they differ,
  another worker already rotated the token since the caller observed the
  401 — return the current (fresh) token without refreshing again.
  The existing time-based guard inside the lock keeps working for the
  non-forced path.
- Failure semantics unchanged: `OAuthRefreshFailed` still flips
  `grant_status = "expired"` and propagates.

### 2. Shared 401 detection helper

`cubeplex/mcp/exceptions.py` gains
`is_unauthorized_error(exc: BaseException) -> bool`: unwraps any layers
of `BaseExceptionGroup` (the MCP SDK wraps transport errors in TaskGroup
groups) and returns True when an inner `httpx.HTTPStatusError` has
status 401. Used by both the discovery and the runtime paths.

### 3. Discovery: refresh-and-retry once

In `discover_tools_for_install`, when `_list_raw_mcp_tools` fails:

- If the failure is a 401 **and** the resolved grant is an OAuth grant
  with a `refresh_credential_id`: call the token manager with
  `force_refresh=True`, rebuild the `Authorization` header, retry
  `_list_raw_mcp_tools` once.
  - Refresh raises `OAuthRefreshFailed` → the grant is already marked
    expired by the manager; persist `discovery_status='error'` with a
    `last_error` that names the real state
    (`oauth_reauthorization_required: <formatted cause>`).
  - Retry succeeds → normal success path (cache updated, error cleared).
  - Retry still 401s → the server rejects even a brand-new token, so
    reauthorization is needed: set `grant_status='expired'` (best
    effort) and persist the discovery error.
- Any other failure, or a non-refreshable grant (static / none auth, no
  refresh credential): behavior unchanged.

This automatically covers the background tools-cache TTL refresh and the
manual Retry button — both call `discover_tools_for_install`.

### 4. Runtime tool calls: refresh-and-retry once

The tool-call closures built by `_build_tools_from_cache` (and the live
`load_mcp_tools_http` fallback right next to it) currently capture a
fixed `headers` dict. Change:

- `_load_tools_for_specs` builds, for each OAuth spec whose grant has a
  `refresh_credential_id`, an async `refresh_auth` callback and threads
  it into `_build_tools_from_cache`.
- The callback opens its **own** short-lived session via
  `async_session_maker` (the loader's session is closed by the time
  tool calls run — same reason `_load_tools_for_specs_deferred`
  exists), rebuilds the org-scoped grant/credential repos, and calls the
  token manager with `force_refresh=True`. To reuse the app-level
  singletons (http client, redis, encryption backend, metadata cache)
  without reaching into privates, `OAuthTokenManager` gains
  `with_credential_repo(repo) -> OAuthTokenManager` returning a clone
  bound to the new session's credential repo.
- `_call_remote` wraps the session-open/initialize/call block: on a 401
  (`is_unauthorized_error`) with a callback available, invoke it, merge
  the returned `Authorization` header into the **shared** per-spec
  headers dict (so later calls in the same turn reuse the fresh token),
  and retry the call once. A second 401 propagates to the agent as
  today, after best-effort flipping the grant to `expired`.
- Refresh callback failures never mask the original 401: on any
  exception from the callback, log and re-raise the original error.
- Concurrency: two tool calls hitting 401 at once both invoke the
  callback; the token manager's redis lock plus the double-rotation
  guard collapse them to a single refresh.

### 5. What is NOT changing

- `_resolve_auth_from_spec`'s turn-start behavior (including the
  fall-back-to-cached-token on refresh failure) stays as is; the
  call-time retry now provides the recovery that fallback was papering
  over.
- The `expires_at`-based proactive refresh stays the primary mechanism;
  the 401 path is the corrective for providers that lie.

## Out of scope

- UI changes. The existing banner / Reconnect affordances already key
  off `discovery_status` and `grant_status`; this change only makes
  those fields truthful.
- Auto-clearing a stale "Discovery error" banner without a discovery
  run (the background TTL refresh + retry-on-401 already shrinks the
  window this matters in).
- The workspace "Try It" tool-invoke route (`ws_mcp.py`) — a failed
  manual invocation is immediately visible and re-clickable; can adopt
  the same helper later if it proves annoying.

## Success criteria

1. Discovery against a server that 401s the cached token but accepts a
   refreshed one → `discovery_status='ok'`, `last_error` cleared, grant
   `valid` with advanced `expires_at`. No user action needed.
2. Discovery where the refresh grant is rejected by the AS →
   `grant_status='expired'`, `last_error` starts with
   `oauth_reauthorization_required:`, UI shows Reconnect.
3. Discovery where even the refreshed token gets 401 →
   `grant_status='expired'`.
4. A tool call in an agent turn whose baked token has been revoked
   succeeds transparently after one forced refresh; subsequent tool
   calls in the same turn send the new token without another refresh.
5. Static-token and no-auth connectors: byte-for-byte unchanged
   behavior on 401 (no retry loop).
6. Forced refresh never double-rotates when two workers race (redis
   lock + expires_at snapshot guard).
