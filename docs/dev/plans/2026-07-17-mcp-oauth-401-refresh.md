# Plan: MCP OAuth 401 → forced refresh + retry

Spec: [2026-07-17-mcp-oauth-401-refresh-design.md](../specs/2026-07-17-mcp-oauth-401-refresh-design.md)

**Goal.** On a 401 from an MCP server against a supposedly-valid OAuth
token, force one refresh and retry; on refresh failure or a repeat 401,
mark the grant expired so Reconnect surfaces.

**Architecture.** One new capability on `OAuthTokenManager`
(`force_refresh`) consumed by the two places that talk to MCP servers
with an OAuth bearer: the discovery service and the runtime tool-call
closures. A shared `is_unauthorized_error` helper classifies the
exception-group-wrapped 401s both places see. The runtime path opens its
own short-lived DB session at retry time (loader session is closed by
then), mirroring `_load_tools_for_specs_deferred`.

**Tech stack.** Existing: httpx, redis lock, SQLAlchemy async, pytest.
No new dependencies.

---

## Unit 1 — token manager: forced refresh

**Files.** `backend/cubeplex/mcp/oauth/token_manager.py`

**Interfaces.**

- `get_access_token_for_grant(..., force_refresh: bool = False) -> str`
- `with_credential_repo(credential_repo: CredentialRepository) -> OAuthTokenManager`
  — clone bound to a different (fresh-session) credential repo; shares
  http client, redis, encryption backend, metadata cache, buffers.

**Core logic.** `force_refresh=True` skips the `_needs_refresh` early
return before the lock. Inside the lock, `_refresh_grant` gains the
caller's `expires_at` snapshot: if the re-read row's `expires_at`
differs from the snapshot, another worker already rotated — return the
current token without refreshing. The non-forced path keeps the
existing time-based in-lock guard. `OAuthRefreshFailed` semantics
(flip `grant_status='expired'`, propagate) unchanged.

**Tests.** New `backend/tests/unit/mcp/test_oauth_token_manager.py`
(pure in-process: stub redis with an in-memory fake, stub repos and
encryption; httpx via `httpx.MockTransport`). Invariants:

- forced refresh rotates even when `expires_at` is far in the future;
- forced refresh with a changed `expires_at` snapshot does NOT hit the
  token endpoint (double-rotation guard);
- refresh failure under force still flips grant to expired and raises;
- `force_refresh=False` keeps the time-based behavior.

## Unit 2 — 401 classifier

**Files.** `backend/cubeplex/mcp/exceptions.py`

**Interfaces.** `is_unauthorized_error(exc: BaseException) -> bool`.

**Core logic.** Iteratively unwrap `BaseExceptionGroup` (first
non-group leaf, matching `_format_discovery_error`'s walk), return True
iff the leaf is `httpx.HTTPStatusError` with `response.status_code ==
401`. Walk ALL leaves of a group, not just the first — httpx task
groups can put connection-cleanup noise first.

**Tests.** In Unit 1's file or a small
`tests/unit/mcp/test_exceptions.py`: bare 401, group-wrapped 401,
nested groups, non-401 HTTPStatusError, unrelated exception.

## Unit 3 — discovery retry

**Files.** `backend/cubeplex/services/mcp_discovery.py`

**Core logic.** In `discover_tools_for_install`, the
`_list_raw_mcp_tools` except-block branches:

1. `is_unauthorized_error(exc)` and grant is OAuth with
   `refresh_credential_id` →
   `token_mgr.get_access_token_for_grant(force_refresh=True)`.
   - `OAuthRefreshFailed` → persist error with
     `last_error = "oauth_reauthorization_required: " + formatted cause`
     (grant already expired by the manager). Return error result.
   - Success → set `headers["Authorization"] = "Bearer " + token`,
     retry `_list_raw_mcp_tools` once. Second failure of any kind →
     if it is again a 401, best-effort `grant.grant_status = "expired"`
     + `grant_repo.update(grant)`; persist error as today.
2. Anything else → existing persist-error path, byte-identical.

Grant availability: the org-scope and workspace-scope branches both
already produce `grant`; the retry only activates when
`grant.auth_method == "oauth"` and `grant.refresh_credential_id` is set.

**Tests.** e2e (opens AsyncSession) alongside
`backend/tests/e2e/test_mcp_restore_lost_ui.py` conventions: stub
`_list_raw_mcp_tools` (monkeypatch) with scripted outcomes and a stub
token manager. Invariants = spec success criteria 1–3:

- 401 then success after forced refresh → status ok, last_error None,
  header carried the refreshed token on the retry;
- refresh failure → error persisted with
  `oauth_reauthorization_required:` prefix, grant expired;
- 401 twice → grant expired, error persisted;
- non-OAuth install 401 → no token manager call, error persisted as
  before.

## Unit 4 — runtime tool-call retry

**Files.** `backend/cubeplex/mcp/cubepi_runtime.py`

**Interfaces.**

- `_build_tools_from_cache(*, spec, headers, server_url, refresh_auth:
  Callable[[], Awaitable[str | None]] | None = None)` — callback
  returns a fresh access token or None (not refreshable / failed).
- `_make_refresh_auth_callback(spec, token_manager) -> callback | None`
  in the same module; returns None unless
  `spec.auth_method == "oauth"` and `spec.grant.refresh_credential_id`
  is set.

**Core logic.**

- Callback body: `async with async_session_maker()` → org-scoped
  `MCPCredentialGrantRepository` + `CredentialRepository` →
  `token_manager.with_credential_repo(...)` →
  `get_access_token_for_grant(grant=spec.grant, force_refresh=True)` →
  commit → return token. Any exception → log warning, return None.
- `_call_remote`: wrap the open/initialize/call block in try/except; on
  `is_unauthorized_error` with a callback, invoke it; on a token,
  mutate the shared `headers` dict's `Authorization` (later calls in
  the turn reuse it) and retry the block once. Retry 401 again →
  best-effort flip grant to expired (own short session, suppressed
  errors) and re-raise. Callback returned None → re-raise original.
- The live-loader fallback (`load_mcp_tools_http` inside
  `_load_tools_for_specs`) gets the same single-retry treatment: on
  401 + callback, refresh, rebuild header, one more attempt, else skip
  the spec as today.
- Cache discipline note: headers never enter the LLM byte stream; tool
  list and ordering are untouched.

**Tests.** Extend
`backend/tests/unit/mcp/test_runtime_tools_cache.py` (already stubs
cubepi session internals). Invariants = spec criteria 4–5:

- call 401s once, callback yields token → call retried with new
  Authorization header and succeeds; shared headers dict updated;
- second call after a refresh does not invoke the callback again;
- callback None (static auth) → 401 propagates, no retry;
- callback raises → original 401 propagates.

## Unit 5 — docs

**Files.** `backend/docs/mcp_catalog_oauth.md` ("Token refresh" +
"Troubleshooting" sections).

State the new behavior: 401 with a valid-looking grant triggers one
forced refresh + retry; repeat-401/refresh-failure flips the grant to
expired and surfaces Reconnect; `oauth_reauthorization_required:` is
the last_error marker. No `docs/site` page documents token-refresh
internals (user-visible flow — the Reconnect prompt — already
documented), so no site page change.

---

## Plan self-review notes

- Spec §1→U1, §2→U2, §3→U3, §4→U4, §5/out-of-scope→U5 + untouched
  paths. Covered.
- Interface names consistent: `force_refresh`, `is_unauthorized_error`,
  `with_credential_repo`, `refresh_auth` used identically across units.
- Deliberate exclusions (Try It route, banner auto-clear,
  `_resolve_auth_from_spec` fallback) are in the spec's out-of-scope.
