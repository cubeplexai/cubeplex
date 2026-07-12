# MCP Catalog + OAuth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace dev-style "fill in URL" MCP onboarding with system-seeded catalog + OAuth (DCR + static client) and static-token paths. Drop stdio + legacy `config.yaml mcp.servers`. No data-compat migration.

**Spec:** `docs/superpowers/specs/2026-05-08-mcp-catalog-oauth-design.md`

**Tech Stack:** SQLModel + Alembic + FastAPI + cryptography (Fernet) + httpx + PyJWT + langchain-mcp-adapters + Next.js + shadcn/ui + Zustand + Playwright (E2E) + pytest (unit + E2E).

**Working directory:** Run inside a dedicated worktree:

```bash
./scripts/new-worktree feat/m2-mcp-catalog-oauth
cd ../<allocated-worktree>
cat .worktree.env
./scripts/worktree-env doctor
cp /path/to/main/backend/.env backend/.env
cp /path/to/main/backend/config.development.local.yaml \
   backend/config.development.local.yaml
```

---

## Phase 0 — Cleanup (breaking, no compat)

- [ ] **0.1** Remove stdio support
  - [ ] `backend/cubeplex/services/mcp.py`: drop `stdio` from `_VALID_TRANSPORTS`; remove `_stdio_params` from `mcp/connection_params.py`
  - [ ] `backend/cubeplex/api/schemas/mcp.py`: `transport: Literal["streamable_http", "sse"]`
  - [ ] `frontend/packages/core/src/types/mcp.ts`: drop `stdio`
  - [ ] `frontend/packages/web/components/mcp/MCPServerForm.tsx`: drop stdio option
  - [ ] alembic revision: delete any rows with `transport='stdio'` (DDL-only, no data preservation)
- [ ] **0.2** Remove legacy `config.yaml mcp.servers` startup loader
  - [ ] `backend/cubeplex/api/app.py:119`: drop `MCPManager` global registry initialization
  - [ ] `backend/cubeplex/mcp/client.py`: remove or deprecate the legacy global manager
  - [ ] `backend/config.yaml` / `config.development.yaml` / `config.production.yaml`: remove `mcp.servers` blocks
- [ ] **0.3** Drop `workspace_mcp_bindings` table (replaced by overrides)
  - [ ] alembic revision drop table

## Phase 1 — Schema

- [ ] **1.1** Add `mcp_catalog_connectors` model + repository (per spec §4.1)
- [ ] **1.2** Add `workspace_mcp_overrides` model + repository (per spec §4.3)
- [ ] **1.3** Add `mcp_servers.catalog_connector_id` + partial unique index `uq_mcp_install_per_catalog`
- [ ] **1.4** Extend `Credential.kind` enum strings: `mcp_oauth_access_token`, `mcp_oauth_refresh_token`, `mcp_oauth_client_secret`
- [ ] **1.5** Single alembic revision `--autogenerate`; verify diff matches expectation; manual edits as needed
- [ ] **1.6** Update `MCPServerRepository.list_for_workspace` per spec §4.5

## Phase 2 — Catalog Service + Seeder

- [ ] **2.1** `backend/cubeplex/services/mcp_catalog.py`: list / get / install (orchestrates `mcp_servers` create + credential write + tools refresh)
- [ ] **2.2** `backend/cubeplex/mcp/catalog_seed.py`: pure-Python catalog list (per spec §7) + upsert by slug + deprecated marker for removed entries
- [ ] **2.3** Static OAuth client secret: read `CUBEPLEX_MCP_OAUTH__<SLUG>__CLIENT_ID` / `__CLIENT_SECRET` env, upsert system-level credential row, link to catalog
- [ ] **2.4** CLI command `python -m cubeplex.cli seed-mcp-catalog`
- [ ] **2.5** Wire seeder into deploy docs (NOT into app startup)

## Phase 3 — Catalog API

- [ ] **3.1** `GET /api/v1/mcp/catalog` (member-readable, with org/ws/user install status)
- [ ] **3.2** `POST /api/v1/admin/mcp/catalog/{catalog_id}/install` (org admin, scope=org|user, auth_method static|oauth|none)
- [ ] **3.3** `DELETE /api/v1/admin/mcp/installs/{install_id}` (soft disable, vault cleanup, OAuth revoke best-effort)
- [ ] **3.4** `PATCH /api/v1/admin/mcp/installs/{install_id}` (switch auth_method = re-key)
- [ ] **3.5** `POST /api/v1/ws/{ws_id}/mcp/catalog/{catalog_id}/install` (workspace user, scope=user forced)
- [ ] **3.6** `PATCH /api/v1/ws/{ws_id}/mcp/org-installs/{install_id}/override` (workspace disable inheritance)
- [ ] **3.7** `DELETE /api/v1/ws/{ws_id}/mcp/installs/{install_id}` (delete user-scope install)
- [ ] **3.8** Weaken handcrafted `POST /api/v1/admin/mcp/servers` — keep for advanced/debug, remove from main UI

## Phase 4 — OAuth Module

Path: `backend/cubeplex/mcp/oauth/`

- [ ] **4.1** `state.py` — HMAC-SHA256 over `(install_id, actor_user_id, ts, nonce)` with `CSRF_SECRET`-derived key; redis one-shot store
- [ ] **4.2** `pkce.py` — `code_verifier` (43–128 chars, URL-safe) + S256 challenge
- [ ] **4.3** `metadata.py` — fetch `/.well-known/oauth-protected-resource` then AS metadata (RFC 8414); cache by AS issuer
- [ ] **4.4** `dcr.py` — RFC 7591 client registration; map response into `oauth_client_config` + write secret to vault
- [ ] **4.5** `token_manager.py` — `get_valid_access_token`; refresh on `<60s` remaining; redis lock `mcp_oauth_refresh:{cred_id}` TTL 5s; rotation; mark unauthed on failure
- [ ] **4.6** `callback.py` — token exchange logic (called by route)
- [ ] **4.7** `__init__.py` — header comment "why no E2E here" (quoting spec §11.3)
- [ ] **4.8** Drop `MCPOAuthNotImplemented` raise in `services/mcp.py:545`

## Phase 5 — OAuth Routes

- [ ] **5.1** `POST /api/v1/admin/mcp/installs/{install_id}/oauth/start` — issues authorize_url + sets callback ticket cookie
- [ ] **5.2** `POST /api/v1/ws/{ws_id}/mcp/installs/{install_id}/oauth/start` — same for user-scope
- [ ] **5.3** `GET /api/v1/oauth/mcp/callback` — verify state + ticket, exchange code, write tokens, refresh tools, 302 to `/oauth/mcp/return`
- [ ] **5.4** Add CSRF exception for `/oauth/mcp/callback` (it's a GET cross-origin redirect target)

## Phase 6 — Frontend

- [ ] **6.1** `@cubeplex/core` types: `MCPCatalogConnector`, `MCPInstallStatus`, request/response shapes
- [ ] **6.2** `@cubeplex/core` API client methods for catalog + install + override + oauth start
- [ ] **6.3** Zustand store: catalog list + install actions + optimistic state for OAuth pending
- [ ] **6.4** `<MCPCatalogGrid>` component
- [ ] **6.5** `<MCPInstallDrawer>` with segmented auth-method control
- [ ] **6.6** `<MCPStaticForm>` driven by `static_form_fields` metadata
- [ ] **6.7** Page `/admin/mcp` (org admin)
- [ ] **6.8** Page `/w/[wsId]/settings/mcp` (workspace member)
- [ ] **6.9** Page `/oauth/mcp/return` (toast + redirect to sessionStorage origin)
- [ ] **6.10** Remove stdio + frontend "Custom connector" from main UI (collapse into advanced settings)

## Phase 7 — Tests

- [ ] **7.1** Unit tests per spec §11.1 (state, pkce, metadata, dcr, token_manager, catalog_seed, static_auth_header)
- [ ] **7.2** E2E tests per spec §11.2 (static install, catalog listing) — real DB + real vault + existing mock MCP test server
- [ ] **7.3** `backend/tests/e2e/mcp/README.md`: explain why no OAuth E2E (quote spec §11.3)
- [ ] **7.4** Run `make check` clean

## Phase 8 — Docs + Deploy

- [ ] **8.1** Update `backend/docs/` MCP architecture doc to reflect catalog model
- [ ] **8.2** Document required env vars in `backend/.env.example`:
  - `CUBEPLEX_MCP_OAUTH__GITHUB__CLIENT_ID` / `__CLIENT_SECRET`
  - `CUBEPLEX_MCP_OAUTH__SLACK__CLIENT_ID` / `__CLIENT_SECRET`
  - `CUBEPLEX_MCP_OAUTH__GWS__CLIENT_ID` / `__CLIENT_SECRET`
- [ ] **8.3** Document deploy flow: `alembic upgrade head` then `seed-mcp-catalog`
- [ ] **8.4** Staging manual test plan for OAuth (real Notion / GitHub / Linear / Asana / Atlassian / Sentry / Intercom / Cloudflare / Slack / GWS) — recorded but not auto-run

---

## Sequencing Notes

- Phase 0 (cleanup) and Phase 1 (schema) must land in a single PR + alembic revision to avoid intermediate broken state.
- Phase 2–3 (catalog service + non-OAuth API) and Phase 7.2 static-path E2E can land independently before OAuth lands → gives a usable static-only catalog ASAP.
- Phase 4–5 (OAuth) lands after Phase 2–3.
- Phase 6 (frontend) can develop in parallel against Phase 2–3 API; OAuth UI bits stub `requires_oauth` until backend lands.
- Staging OAuth manual test (Phase 8.4) is gating before declaring v1 done.

## Risk Register

- **DCR support drift**: real-world MCP servers' DCR endpoints may diverge from RFC 7591. Mitigation: catalog seed `oauth_dcr_supported=false` for any connector that fails DCR in staging; fall back to static client_id.
- **Refresh token rotation race**: even with redis lock, a long-running run may hold an old token. Mitigation: token_manager retries once with refresh on first 401 from MCP server.
- **Org-wide OAuth admin departure**: documented as known limitation in UI tooltip; no v1 mitigation.
- **Cloudflare's "multiple sub-products"** may require per-product MCP endpoints; catalog rows per sub-product. Decide final list during Phase 2.
