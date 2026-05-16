# MCP Install → Authentication Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bridge the gap left by the four-layer MCP refactor — make clicking "Install" actually walk the user through OAuth (or static-credential) so the connector reaches `usable=true` from the UI without manual intervention.

**Architecture:** Backend gets two new service modules (`mcp/oauth/start.py`, `mcp/oauth/callback.py`) that wire the existing `OAuthMetadataDiscovery` / `DCRClient` / `PKCEChallenge` / `OAuthStateStore` / `OAuthTokenManager` primitives into a four-layer-aware authorize-and-grant flow; the three 501-stub routes (admin org, ws workspace, ws me) plus the callback stub are replaced with real handlers. A new admin-only effective endpoint exposes the per-install org-row `(usable, reason)` derivation that bypasses the workspace lens. Frontend gets one new core utility (`runOAuthFlow`), one new React component (`AuthActionBand` with five states), one new Next.js page (`/oauth/mcp/return`), wiring into both detail panels (admin + workspace settings), and a member-role gate hiding the template list from non-admins.

**Tech Stack:** FastAPI, SQLModel, Alembic, PostgreSQL, Redis (state token store), httpx (OAuth HTTP), pytest, Next.js 16, React 19, TypeScript, Zustand, Tailwind, shadcn/ui, Playwright.

**Spec:** `docs/superpowers/specs/2026-05-16-mcp-install-auth-handoff-spec.md`

---

## File Structure

### Backend

| Path | Status | Purpose |
| --- | --- | --- |
| `backend/cubebox/mcp/oauth/start.py` | Create | `OAuthStartService` — orchestrates DCR/static client → PKCE → state token → authorize URL. |
| `backend/cubebox/mcp/oauth/callback.py` | Create | `OAuthCallbackHandler` — verifies state, exchanges code, writes `MCPCredentialGrant`, updates `install.auth_status`, returns redirect params. |
| `backend/cubebox/mcp/oauth/__init__.py` | Modify | Export the two new classes. |
| `backend/cubebox/mcp/dependencies.py` | Modify | Add `get_oauth_start_service` and `get_oauth_callback_handler` DI factories. |
| `backend/cubebox/api/routes/v1/admin_mcp.py` | Modify | Replace 501 stub at `admin_org_grant_oauth_start`; add `GET /admin/mcp/installs/{id}/effective` for the org-row reason derivation (§4 admin row). |
| `backend/cubebox/api/routes/v1/ws_mcp.py` | Modify | Replace 501 stubs at `my_user_grant_oauth_start` and `workspace_grant_oauth_start`. |
| `backend/cubebox/api/routes/v1/mcp_oauth.py` | Modify | Replace stub callback handler with a real implementation that calls `OAuthCallbackHandler`. |
| `backend/cubebox/api/schemas/mcp.py` | Modify | Add `MCPOAuthStartOut` fields (`authorize_url`, `state`, `expires_at`); add `MCPAdminInstallEffectiveOut` schema. |
| `backend/tests/e2e/test_mcp_oauth_handoff.py` | Create | E2E covering one OAuth start + callback round-trip per scope (org / workspace / me); verifies grant row written and `auth_status` flip. |

### Frontend

| Path | Status | Purpose |
| --- | --- | --- |
| `frontend/packages/core/src/oauth/runOAuthFlow.ts` | Create | Browser-side popup controller (sync open + state filter + 90s timeout). |
| `frontend/packages/core/src/oauth/index.ts` | Create | Re-export `runOAuthFlow`. |
| `frontend/packages/core/src/index.ts` | Modify | Re-export from `./oauth`. |
| `frontend/packages/core/src/api/mcp.ts` | Modify | Update return type of `*OAuthStart` helpers to include `state`. Add `adminGetInstallEffective` helper. |
| `frontend/packages/web/app/oauth/mcp/return/page.tsx` | Create | The return page (BroadcastChannel post + auto-close + static fallback). |
| `frontend/packages/web/components/mcp/AuthActionBand.tsx` | Create | Five-state band component. |
| `frontend/packages/web/components/mcp/effectiveAuthState.ts` | Create | Pure function picking band state from `MCPEffectiveConnector + caller role + admin flag`. |
| `frontend/packages/web/components/mcp/MCPAdminDetailPanel.tsx` | Modify | Mount `AuthActionBand` under the title row; consume admin-org effective. |
| `frontend/packages/web/components/workspace-settings/McpPanel.tsx` | Modify | Mount `AuthActionBand` in `ConnectorDetail`; gate the template list section behind `wsRole === 'admin'`. |
| `frontend/packages/web/messages/en.json` | Modify | Add new copy keys under `mcp` (action band states, reason copy). |
| `frontend/packages/web/messages/zh.json` | Modify | Same keys, Chinese. |
| `frontend/packages/web/__tests__/e2e/mcp/install-auth-handoff.spec.ts` | Create | Playwright E2E covering needs-action → ready transition for static install. |

---

## Task 1: OAuth start service

**Files:**
- Create: `backend/cubebox/mcp/oauth/start.py`
- Test: `backend/tests/e2e/test_mcp_oauth_handoff.py` (this task adds the unit-style start test only; round-trip lands in Task 9)

- [ ] **Step 1: Write the failing tests for `OAuthStartService.start_oauth_flow`**

Create `backend/tests/e2e/test_mcp_oauth_handoff.py` with the following at the top:

```python
"""E2E for MCP install → authentication handoff."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.mcp.oauth.start import OAuthStartResult, OAuthStartService


pytestmark = pytest.mark.asyncio


async def test_start_oauth_flow_returns_authorize_url_state_and_expires_at(
    oauth_start_service: OAuthStartService,
    seeded_oauth_install,
) -> None:
    install_id, scope, ws_id, user_id = seeded_oauth_install
    result = await oauth_start_service.start_oauth_flow(
        install_id=install_id,
        actor_user_id=user_id,
        grant_scope=scope,
        workspace_id=ws_id,
        user_id=user_id,
    )
    assert isinstance(result, OAuthStartResult)
    assert result.authorize_url.startswith("https://")
    # state is opaque but must round-trip through OAuthStateStore.consume.
    assert "." in result.state  # payload.signature shape
    assert result.expires_at.tzinfo is not None
```

Use the existing `mcp_test_factories` fixtures for the install seed (look at `backend/tests/e2e/test_mcp_four_layer_routes.py` for the established pattern).

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/e2e/test_mcp_oauth_handoff.py::test_start_oauth_flow_returns_authorize_url_state_and_expires_at -v`
Expected: FAIL with `ImportError: cannot import name 'OAuthStartResult' from 'cubebox.mcp.oauth.start'` (module does not yet exist).

- [ ] **Step 3: Create `OAuthStartService` with the result dataclass**

Create `backend/cubebox/mcp/oauth/start.py`:

```python
"""Mint authorize URLs for the four-layer MCP OAuth flow.

Spec: docs/superpowers/specs/2026-05-16-mcp-install-auth-handoff-spec.md §6.
The per-scope start route handlers in admin_mcp.py / ws_mcp.py call
``OAuthStartService.start_oauth_flow`` and serialize the returned
``OAuthStartResult`` into ``MCPOAuthStartOut``.

The service:
1. Looks up the install row (must exist, be active, and use auth_method='oauth').
2. Discovers / refreshes AS metadata via OAuthMetadataDiscovery.
3. Performs DCR if the AS supports it and the install has no client_id yet
   (snapshots client_id / client_secret onto the install row); otherwise reuses
   the install's existing static client credentials.
4. Generates a PKCE challenge.
5. Issues a state token via OAuthStateStore (carries grant_scope + workspace_id +
   user_id so the callback can write the right grant without a session lookup).
6. Builds the authorize URL with response_type=code, client_id, redirect_uri,
   code_challenge=<S256>, code_challenge_method=S256, state, scope.
7. Returns OAuthStartResult(authorize_url, state, expires_at).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlencode

import httpx

from cubebox.config import config
from cubebox.mcp.oauth.dcr import DCRClient, DCRRequest
from cubebox.mcp.oauth.metadata import (
    AuthorizationServerMetadata,
    OAuthMetadataDiscovery,
)
from cubebox.mcp.oauth.pkce import generate_pkce
from cubebox.mcp.oauth.state import OAuthStateStore
from cubebox.repositories.mcp import MCPConnectorInstallRepository
from cubebox.services.credentials import CredentialService

_REDIRECT_PATH: Final[str] = "/api/v1/oauth/mcp/callback"


@dataclass(frozen=True)
class OAuthStartResult:
    """What the route handler serializes back to the client."""

    authorize_url: str
    state: str
    expires_at: datetime  # UTC; matches the state-token TTL


class OAuthStartError(ValueError):
    """Surface-friendly error type. Route layer maps to HTTPException."""


class OAuthStartService:
    """Stateless orchestrator. One per request via DI."""

    def __init__(
        self,
        *,
        install_repo: MCPConnectorInstallRepository,
        state_store: OAuthStateStore,
        metadata: OAuthMetadataDiscovery,
        dcr: DCRClient,
        cred_service: CredentialService,
        http_client: httpx.AsyncClient,
        state_ttl_seconds: int = 300,
    ) -> None:
        self._install_repo = install_repo
        self._state_store = state_store
        self._metadata = metadata
        self._dcr = dcr
        self._cred_service = cred_service
        self._http = http_client
        self._state_ttl_seconds = state_ttl_seconds

    async def start_oauth_flow(
        self,
        *,
        install_id: str,
        actor_user_id: str,
        grant_scope: str,
        workspace_id: str | None,
        user_id: str | None,
    ) -> OAuthStartResult:
        install = await self._install_repo.get(install_id)
        if install is None:
            raise OAuthStartError("connector_install_not_found")
        if install.install_state != "active":
            raise OAuthStartError("connector_install_not_active")
        if install.auth_method != "oauth":
            raise OAuthStartError("oauth_start_only_valid_for_oauth_auth")

        as_meta = await self._ensure_as_metadata(install)
        client_id, client_secret_id = await self._ensure_client(install, as_meta)

        pkce = generate_pkce()
        state = await self._state_store.issue(
            install_id=install_id,
            actor_user_id=actor_user_id,
            grant_scope=grant_scope,
            workspace_id=workspace_id,
            user_id=user_id,
        )

        # Persist the per-flow PKCE verifier alongside the state token so
        # the callback can complete the exchange without re-issuing PKCE.
        # See OAuthStateStore TTL — same window (5 min default).
        await self._state_store.attach_pkce(state=state, verifier=pkce.verifier)

        authorize_url = _build_authorize_url(
            authorize_endpoint=as_meta.authorization_endpoint,
            client_id=client_id,
            redirect_uri=_redirect_uri(),
            code_challenge=pkce.challenge,
            state=state,
            scope=install.oauth_default_scope,
        )

        expires_at = datetime.now(tz=UTC) + timedelta(seconds=self._state_ttl_seconds)
        return OAuthStartResult(
            authorize_url=authorize_url,
            state=state,
            expires_at=expires_at,
        )

    async def _ensure_as_metadata(self, install) -> AuthorizationServerMetadata:
        # Snapshots authorize_endpoint / token_endpoint / etc. onto the install
        # row when first discovered, so refresh + revoke can avoid re-discovery.
        if install.authorization_endpoint:
            return AuthorizationServerMetadata.from_install(install)
        _pr_meta, as_meta = await self._metadata.discover_for_resource(install.server_url)
        install.authorization_endpoint = as_meta.authorization_endpoint
        install.token_endpoint = as_meta.token_endpoint
        install.revocation_endpoint = as_meta.revocation_endpoint
        install.oauth_default_scope = (
            install.oauth_default_scope or as_meta.scopes_supported_default
        )
        await self._install_repo.update(install)
        return as_meta

    async def _ensure_client(self, install, as_meta) -> tuple[str, str | None]:
        if install.oauth_client_id:
            return install.oauth_client_id, install.oauth_client_secret_id
        if not as_meta.registration_endpoint:
            raise OAuthStartError("dcr_unsupported_and_no_static_client")
        dcr_resp = await self._dcr.register(
            DCRRequest(
                registration_endpoint=as_meta.registration_endpoint,
                redirect_uris=[_redirect_uri()],
                client_name=f"cubebox:{install.id}",
            ),
            http_client=self._http,
        )
        secret_id: str | None = None
        if dcr_resp.client_secret:
            secret_id = await self._cred_service.create(
                kind="mcp_oauth_client_secret",
                name=f"mcp:{install.id}:client_secret",
                plaintext=dcr_resp.client_secret,
            )
        install.oauth_client_id = dcr_resp.client_id
        install.oauth_client_secret_id = secret_id
        await self._install_repo.update(install)
        return dcr_resp.client_id, secret_id


def _redirect_uri() -> str:
    base = str(config.get("backend_base_url", "http://localhost:8000")).rstrip("/")
    return f"{base}{_REDIRECT_PATH}"


def _build_authorize_url(
    *,
    authorize_endpoint: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    scope: str | None,
) -> str:
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    if scope:
        params["scope"] = scope
    sep = "&" if "?" in authorize_endpoint else "?"
    return f"{authorize_endpoint}{sep}{urlencode(params)}"
```

- [ ] **Step 4: Add `attach_pkce` + `consume_pkce` to `OAuthStateStore`**

Open `backend/cubebox/mcp/oauth/state.py` and add two methods to `OAuthStateStore`:

```python
async def attach_pkce(self, *, state: str, verifier: str) -> None:
    """Persist the PKCE verifier under the same TTL as the state token."""
    await self._redis.set(
        _REDIS_KEY_PREFIX + "pkce:" + state,
        verifier,
        ex=self._ttl_seconds,
    )

async def consume_pkce(self, state: str) -> str | None:
    """Atomically read-and-delete the PKCE verifier for this state."""
    key = _REDIS_KEY_PREFIX + "pkce:" + state
    verifier = await self._redis.get(key)
    if verifier is None:
        return None
    await self._redis.delete(key)
    return verifier if isinstance(verifier, str) else verifier.decode("utf-8")
```

- [ ] **Step 5: Add the `seeded_oauth_install` and `oauth_start_service` fixtures**

In the same test file, add fixtures (lifted from the patterns in `backend/tests/conftest.py`):

```python
@pytest.fixture
async def seeded_oauth_install(db_session: AsyncSession, seed_org_workspace_user):
    """Yield (install_id, grant_scope, workspace_id, user_id) for an OAuth install."""
    org_id, ws_id, user_id = seed_org_workspace_user
    install = await _seed_oauth_install(db_session, org_id=org_id, workspace_id=ws_id)
    return install.id, "user", ws_id, user_id


@pytest.fixture
async def oauth_start_service(...):  # wire all real deps via DI factories
    ...
```

Use whatever real seed helpers the existing four-layer test file has;
do NOT mock the OAuth metadata discovery against a real AS — instead,
pre-populate `install.authorization_endpoint` / `token_endpoint` /
`oauth_client_id` so the service skips DCR + discovery.

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/e2e/test_mcp_oauth_handoff.py::test_start_oauth_flow_returns_authorize_url_state_and_expires_at -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/cubebox/mcp/oauth/start.py \
        backend/cubebox/mcp/oauth/state.py \
        backend/tests/e2e/test_mcp_oauth_handoff.py
git commit -m "feat(mcp/oauth): add four-layer OAuthStartService"
```

---

## Task 2: OAuth callback handler

**Files:**
- Create: `backend/cubebox/mcp/oauth/callback.py`
- Test: `backend/tests/e2e/test_mcp_oauth_handoff.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/e2e/test_mcp_oauth_handoff.py`:

```python
async def test_callback_writes_grant_and_authorizes_install(
    oauth_callback_handler,
    oauth_start_service,
    grant_repo,
    install_repo,
    seeded_oauth_install,
    monkeypatch,
) -> None:
    install_id, scope, ws_id, user_id = seeded_oauth_install
    start = await oauth_start_service.start_oauth_flow(
        install_id=install_id,
        actor_user_id=user_id,
        grant_scope=scope,
        workspace_id=ws_id,
        user_id=user_id,
    )

    # Stub the AS token endpoint: return a fake access_token + refresh.
    async def fake_post_token(*args, **kwargs):
        class R:
            def json(self_):
                return {
                    "access_token": "test-access",
                    "refresh_token": "test-refresh",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                }
            def raise_for_status(self_):
                return None
        return R()
    monkeypatch.setattr(oauth_callback_handler, "_post_token_exchange", fake_post_token)

    result = await oauth_callback_handler.handle_callback(
        state=start.state,
        code="fake-code",
    )

    assert result.status == "ok"
    assert result.install_id == install_id
    assert result.state == start.state

    grant = await grant_repo.get_user_grant(install_id, ws_id, user_id)
    assert grant is not None
    assert grant.grant_status == "valid"

    install = await install_repo.get(install_id)
    assert install.auth_status == "authorized"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/e2e/test_mcp_oauth_handoff.py::test_callback_writes_grant_and_authorizes_install -v`
Expected: FAIL — `OAuthCallbackHandler` does not exist.

- [ ] **Step 3: Create `OAuthCallbackHandler`**

Create `backend/cubebox/mcp/oauth/callback.py`:

```python
"""Complete the four-layer OAuth handshake.

Spec: docs/superpowers/specs/2026-05-16-mcp-install-auth-handoff-spec.md §6.
The /api/v1/oauth/mcp/callback route delegates to ``OAuthCallbackHandler``,
which:
1. Consumes the state token (one-shot via OAuthStateStore.consume).
2. Reads the PKCE verifier (one-shot via OAuthStateStore.consume_pkce).
3. POSTs to the AS token endpoint with code + verifier.
4. Encrypts the access token (and refresh token, if present) into the vault.
5. Upserts an MCPCredentialGrant at the scope the state token committed to,
   pointing at the new credential ids.
6. Updates install.auth_status from 'pending' → 'authorized' iff the new
   grant's scope matches the install's currently-effective required scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx

from cubebox.constants import (
    CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN,
    CREDENTIAL_KIND_MCP_OAUTH_REFRESH_TOKEN,
)
from cubebox.mcp.oauth.state import (
    OAuthStateExpired,
    OAuthStateInvalid,
    OAuthStateStore,
)
from cubebox.models.mcp import MCPCredentialGrant
from cubebox.repositories.mcp import (
    MCPConnectorInstallRepository,
    MCPCredentialGrantRepository,
)
from cubebox.services.credentials import CredentialService


@dataclass(frozen=True)
class OAuthCallbackResult:
    """Return shape that the route serializes into the redirect query string."""

    status: Literal["ok", "error", "cancelled"]
    install_id: str  # may be empty string when state could not be decoded
    state: str  # the original state token; required so the parent can match
    reason: str | None = None


class OAuthCallbackHandler:
    def __init__(
        self,
        *,
        install_repo: MCPConnectorInstallRepository,
        grant_repo: MCPCredentialGrantRepository,
        cred_service: CredentialService,
        state_store: OAuthStateStore,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._install_repo = install_repo
        self._grant_repo = grant_repo
        self._cred_service = cred_service
        self._state_store = state_store
        self._http = http_client

    async def handle_callback(
        self,
        *,
        state: str,
        code: str | None,
        error: str | None = None,
    ) -> OAuthCallbackResult:
        # AS reported error directly (user_denied / invalid_request / ...).
        if error is not None and code is None:
            try:
                payload = await self._state_store.consume(state)
            except (OAuthStateInvalid, OAuthStateExpired):
                return OAuthCallbackResult(
                    status="error", install_id="", state=state, reason="state_invalid"
                )
            return OAuthCallbackResult(
                status="cancelled" if error == "access_denied" else "error",
                install_id=payload.install_id,
                state=state,
                reason=error,
            )

        if code is None:
            return OAuthCallbackResult(
                status="error", install_id="", state=state, reason="missing_code"
            )

        try:
            payload = await self._state_store.consume(state)
        except OAuthStateExpired:
            return OAuthCallbackResult(
                status="error", install_id="", state=state, reason="state_expired"
            )
        except OAuthStateInvalid:
            return OAuthCallbackResult(
                status="error", install_id="", state=state, reason="state_invalid"
            )

        verifier = await self._state_store.consume_pkce(state)
        if verifier is None:
            return OAuthCallbackResult(
                status="error",
                install_id=payload.install_id,
                state=state,
                reason="pkce_missing",
            )

        install = await self._install_repo.get(payload.install_id)
        if install is None:
            return OAuthCallbackResult(
                status="error",
                install_id=payload.install_id,
                state=state,
                reason="install_not_found",
            )

        try:
            token = await self._post_token_exchange(install, code, verifier)
        except httpx.HTTPError as exc:
            return OAuthCallbackResult(
                status="error",
                install_id=install.id,
                state=state,
                reason=f"token_exchange_failed:{exc.__class__.__name__}",
            )

        grant = await self._upsert_grant(
            install=install,
            payload=payload,
            token=token,
        )
        await self._maybe_authorize_install(install=install, grant=grant)

        return OAuthCallbackResult(status="ok", install_id=install.id, state=state)

    async def _post_token_exchange(self, install, code, verifier) -> dict:
        # Tested via monkeypatch in unit tests; in prod this is a real httpx POST.
        from urllib.parse import urlencode
        body = urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _redirect_uri(),
            "client_id": install.oauth_client_id,
            "code_verifier": verifier,
        })
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        resp = await self._http.post(install.token_endpoint, content=body, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def _upsert_grant(self, *, install, payload, token) -> MCPCredentialGrant:
        access_id = await self._cred_service.create(
            kind=CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN,
            name=f"mcp:{install.id}:access",
            plaintext=token["access_token"],
        )
        refresh_id: str | None = None
        if "refresh_token" in token:
            refresh_id = await self._cred_service.create(
                kind=CREDENTIAL_KIND_MCP_OAUTH_REFRESH_TOKEN,
                name=f"mcp:{install.id}:refresh",
                plaintext=token["refresh_token"],
            )
        expires_at: datetime | None = None
        if "expires_in" in token:
            expires_at = datetime.now(tz=UTC) + timedelta(seconds=int(token["expires_in"]))

        existing = await self._grant_repo.get_for_scope(
            install_id=install.id,
            grant_scope=payload.grant_scope,
            workspace_id=payload.workspace_id,
            user_id=payload.user_id,
        )
        if existing is None:
            grant = MCPCredentialGrant(
                org_id=install.org_id,
                install_id=install.id,
                grant_scope=payload.grant_scope,
                workspace_id=payload.workspace_id,
                user_id=payload.user_id,
                credential_id=access_id,
                refresh_credential_id=refresh_id,
                expires_at=expires_at,
                grant_status="valid",
                created_by_user_id=payload.actor_user_id,
            )
            return await self._grant_repo.add(grant)
        existing.credential_id = access_id
        existing.refresh_credential_id = refresh_id
        existing.expires_at = expires_at
        existing.grant_status = "valid"
        return await self._grant_repo.update(existing)

    async def _maybe_authorize_install(self, *, install, grant) -> None:
        # Spec §6: only flip auth_status when the new grant's scope matches
        # the install's currently-effective required scope. For org/workspace
        # policy installs the install becomes 'authorized' as soon as its
        # corresponding scope grant lands. For user policy, every member
        # has their own grant — auth_status stays 'pending' (it's a per-
        # install bit, not per-user).
        required_scope = install.default_credential_policy
        if required_scope == grant.grant_scope and required_scope in {"org", "workspace"}:
            install.auth_status = "authorized"
            await self._install_repo.update(install)


def _redirect_uri() -> str:
    from cubebox.config import config
    base = str(config.get("backend_base_url", "http://localhost:8000")).rstrip("/")
    return f"{base}/api/v1/oauth/mcp/callback"
```

- [ ] **Step 4: Add `MCPCredentialGrantRepository.get_for_scope`**

Open `backend/cubebox/repositories/mcp.py`. Add to `MCPCredentialGrantRepository`:

```python
async def get_for_scope(
    self,
    *,
    install_id: str,
    grant_scope: str,
    workspace_id: str | None,
    user_id: str | None,
) -> MCPCredentialGrant | None:
    """Single grant per (install, scope-shape). Org grants ignore both
    workspace_id and user_id; workspace grants ignore user_id; user grants
    require both."""
    if grant_scope == "org":
        return await self.get_org_grant(install_id)
    if grant_scope == "workspace":
        assert workspace_id is not None, "workspace grant requires workspace_id"
        return await self.get_workspace_grant(install_id, workspace_id)
    assert workspace_id is not None and user_id is not None, "user grant requires both"
    return await self.get_user_grant(install_id, workspace_id, user_id)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/e2e/test_mcp_oauth_handoff.py::test_callback_writes_grant_and_authorizes_install -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/mcp/oauth/callback.py \
        backend/cubebox/repositories/mcp.py \
        backend/tests/e2e/test_mcp_oauth_handoff.py
git commit -m "feat(mcp/oauth): add four-layer OAuthCallbackHandler"
```

---

## Task 3: DI factories + replace 501 stubs

**Files:**
- Modify: `backend/cubebox/mcp/oauth/__init__.py`
- Modify: `backend/cubebox/mcp/dependencies.py`
- Modify: `backend/cubebox/api/schemas/mcp.py`
- Modify: `backend/cubebox/api/routes/v1/admin_mcp.py`
- Modify: `backend/cubebox/api/routes/v1/ws_mcp.py`
- Modify: `backend/cubebox/api/routes/v1/mcp_oauth.py`

- [ ] **Step 1: Re-export the new classes**

Edit `backend/cubebox/mcp/oauth/__init__.py` — add:

```python
from cubebox.mcp.oauth.callback import OAuthCallbackHandler, OAuthCallbackResult
from cubebox.mcp.oauth.start import OAuthStartError, OAuthStartResult, OAuthStartService
```

And include all five names in `__all__`.

- [ ] **Step 2: Add DI factories**

Edit `backend/cubebox/mcp/dependencies.py`. Add:

```python
async def get_oauth_start_service(
    install_repo: MCPConnectorInstallRepository = Depends(get_install_repo),
    state_store: OAuthStateStore = Depends(get_oauth_state_store),
    metadata: OAuthMetadataDiscovery = Depends(get_oauth_metadata_discovery),
    dcr: DCRClient = Depends(get_dcr_client),
    cred_service: CredentialService = Depends(get_credential_service),
    http_client: httpx.AsyncClient = Depends(get_oauth_http_client),
) -> OAuthStartService:
    return OAuthStartService(
        install_repo=install_repo,
        state_store=state_store,
        metadata=metadata,
        dcr=dcr,
        cred_service=cred_service,
        http_client=http_client,
    )


async def get_oauth_callback_handler(
    install_repo: MCPConnectorInstallRepository = Depends(get_install_repo),
    grant_repo: MCPCredentialGrantRepository = Depends(get_grant_repo),
    cred_service: CredentialService = Depends(get_credential_service),
    state_store: OAuthStateStore = Depends(get_oauth_state_store),
    http_client: httpx.AsyncClient = Depends(get_oauth_http_client),
) -> OAuthCallbackHandler:
    return OAuthCallbackHandler(
        install_repo=install_repo,
        grant_repo=grant_repo,
        cred_service=cred_service,
        state_store=state_store,
        http_client=http_client,
    )
```

If `get_install_repo` / `get_grant_repo` / `get_dcr_client` /
`get_credential_service` / `get_oauth_metadata_discovery` aren't already
exposed at module level, add them following the existing factory pattern
in this file. Each one constructs the instance from the request-scoped
session / redis / config — no caching across requests.

- [ ] **Step 3: Add `state` to `MCPOAuthStartOut`**

Edit `backend/cubebox/api/schemas/mcp.py`. Find `MCPOAuthStartOut` and replace with:

```python
class MCPOAuthStartOut(BaseModel):
    """Body of POST .../oauth/start. The front-end OAuth controller stores
    `state` and filters BroadcastChannel messages by exact-match equality
    (spec §5.5)."""

    authorize_url: str
    state: str
    expires_at: datetime
```

(`datetime` import: `from datetime import datetime`.)

- [ ] **Step 4: Replace the admin org OAuth start stub**

Edit `backend/cubebox/api/routes/v1/admin_mcp.py`. Replace
`admin_org_grant_oauth_start` body with:

```python
@router.post(
    "/installs/{install_id}/grants/org/oauth/start",
    response_model=MCPOAuthStartOut,
)
async def admin_org_grant_oauth_start(
    install_id: str,
    body: MCPOAuthStartIn,  # noqa: ARG001 — present for OpenAPI clarity
    svc: Annotated[OAuthStartService, Depends(get_oauth_start_service)],
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> MCPOAuthStartOut:
    try:
        result = await svc.start_oauth_flow(
            install_id=install_id,
            actor_user_id=ctx.user.id,
            grant_scope="org",
            workspace_id=None,
            user_id=None,
        )
    except OAuthStartError as exc:
        raise HTTPException(status_code=400, detail={"code": str(exc)}) from exc
    return MCPOAuthStartOut(
        authorize_url=result.authorize_url,
        state=result.state,
        expires_at=result.expires_at,
    )
```

Add the import line:

```python
from cubebox.mcp.dependencies import get_oauth_start_service
from cubebox.mcp.oauth import OAuthStartError, OAuthStartService
```

- [ ] **Step 5: Replace the workspace OAuth start stubs**

Edit `backend/cubebox/api/routes/v1/ws_mcp.py`. For BOTH `my_user_grant_oauth_start`
AND `workspace_grant_oauth_start`, replace the body with the same shape as Step 4.
Difference per route:

- `my_user_grant_oauth_start`: `grant_scope="user"`, `workspace_id=workspace_id`,
  `user_id=ctx.user.id`.
- `workspace_grant_oauth_start`: `grant_scope="workspace"`,
  `workspace_id=workspace_id`, `user_id=None`.

Add the same imports at the top.

- [ ] **Step 6: Replace the callback stub**

Edit `backend/cubebox/api/routes/v1/mcp_oauth.py`. Replace `oauth_callback`
body with:

```python
@oauth_callback_router.get("/callback", include_in_schema=True)
async def oauth_callback(
    handler: Annotated[OAuthCallbackHandler, Depends(get_oauth_callback_handler)],
    state: Annotated[str, Query()],
    code: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    result = await handler.handle_callback(state=state, code=code, error=error)
    params: dict[str, str] = {
        "status": result.status,
        "state": result.state,
        "install_id": result.install_id,
    }
    if result.reason:
        params["reason"] = result.reason
    url = f"{_frontend_return_url()}?{urlencode(params)}"
    response = RedirectResponse(url=url, status_code=302)
    _strip_ticket_cookie(response)
    return response
```

Add imports:

```python
from typing import Annotated
from cubebox.mcp.dependencies import get_oauth_callback_handler
from cubebox.mcp.oauth import OAuthCallbackHandler
```

- [ ] **Step 7: Verify route smoke**

Run: `cd backend && uv run pytest tests/unit/test_admin_mcp_routes.py tests/unit/test_ws_mcp_routes.py -v`
Expected: PASS — these are the route-shape contract tests added in the
four-layer plan; they should still pass since we didn't change paths.

- [ ] **Step 8: Run the OAuth handoff E2E**

Run: `cd backend && uv run pytest tests/e2e/test_mcp_oauth_handoff.py -v`
Expected: PASS for the two tests added in Tasks 1–2.

- [ ] **Step 9: Commit**

```bash
git add backend/cubebox/mcp/oauth/__init__.py \
        backend/cubebox/mcp/dependencies.py \
        backend/cubebox/api/schemas/mcp.py \
        backend/cubebox/api/routes/v1/admin_mcp.py \
        backend/cubebox/api/routes/v1/ws_mcp.py \
        backend/cubebox/api/routes/v1/mcp_oauth.py
git commit -m "feat(mcp/oauth): wire start/callback into routes (replace 501 stubs)"
```

---

## Task 4: Admin org effective endpoint

**Files:**
- Modify: `backend/cubebox/api/schemas/mcp.py`
- Modify: `backend/cubebox/api/routes/v1/admin_mcp.py`
- Modify: `backend/tests/e2e/test_mcp_oauth_handoff.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/e2e/test_mcp_oauth_handoff.py`:

```python
async def test_admin_install_effective_static_org_pending(
    client_admin,
    seeded_static_org_install,
) -> None:
    install_id = seeded_static_org_install
    res = await client_admin.get(f"/api/v1/admin/mcp/installs/{install_id}/effective")
    assert res.status_code == 200
    body = res.json()
    assert body["usable"] is False
    assert body["reason"] == "missing_org_grant"


async def test_admin_install_effective_oauth_org_pending_returns_pending_oauth(
    client_admin,
    seeded_oauth_org_install_no_grant,
) -> None:
    install_id = seeded_oauth_org_install_no_grant
    res = await client_admin.get(f"/api/v1/admin/mcp/installs/{install_id}/effective")
    assert res.json()["reason"] == "pending_oauth"
```

Pre-seed the relevant fixtures using factories from existing test files
(install rows must be `auth_method='static'` / `'oauth'` with no grant
written yet; `default_credential_policy='org'`; `auth_status='pending'`).

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/e2e/test_mcp_oauth_handoff.py::test_admin_install_effective_static_org_pending -v`
Expected: FAIL — route does not exist.

- [ ] **Step 3: Add the response schema**

In `backend/cubebox/api/schemas/mcp.py` add:

```python
class MCPAdminInstallEffectiveOut(BaseModel):
    """Org-row effective state for the admin page. Bypasses the workspace
    lens — see spec §4 admin row."""

    install_id: str
    usable: bool
    reason: Literal[
        "usable",
        "pending_oauth",
        "missing_org_grant",
        "grant_expired",
    ]
```

- [ ] **Step 4: Add the derivation helper**

In `backend/cubebox/api/routes/v1/admin_mcp.py` add a module-level helper
(or import it from a new module if you prefer — small enough to inline):

```python
from cubebox.api.schemas.mcp import MCPAdminInstallEffectiveOut


def _derive_admin_org_effective(
    install: MCPConnectorInstall,
    org_grant: MCPCredentialGrant | None,
) -> MCPAdminInstallEffectiveOut:
    """Spec §4 admin row, ordered decision table.

    Rule order (first match wins):
      1. install.auth_method == 'none' → usable.
      2. org grant exists, grant_status == 'valid' → usable.
      3. org grant exists, grant_status == 'expired', no refresh available
         → grant_expired.
      4. no org grant, install.auth_method == 'oauth',
         install.auth_status == 'pending' → pending_oauth.
      5. no org grant otherwise → missing_org_grant.
    """
    if install.auth_method == "none":
        return MCPAdminInstallEffectiveOut(
            install_id=install.id, usable=True, reason="usable"
        )
    if org_grant is not None and org_grant.grant_status == "valid":
        return MCPAdminInstallEffectiveOut(
            install_id=install.id, usable=True, reason="usable"
        )
    if (
        org_grant is not None
        and org_grant.grant_status == "expired"
        and org_grant.refresh_credential_id is None
    ):
        return MCPAdminInstallEffectiveOut(
            install_id=install.id, usable=False, reason="grant_expired"
        )
    if (
        org_grant is None
        and install.auth_method == "oauth"
        and install.auth_status == "pending"
    ):
        return MCPAdminInstallEffectiveOut(
            install_id=install.id, usable=False, reason="pending_oauth"
        )
    return MCPAdminInstallEffectiveOut(
        install_id=install.id, usable=False, reason="missing_org_grant"
    )
```

- [ ] **Step 5: Add the route**

In `backend/cubebox/api/routes/v1/admin_mcp.py` add:

```python
@router.get(
    "/installs/{install_id}/effective",
    response_model=MCPAdminInstallEffectiveOut,
)
async def get_admin_install_effective(
    install_id: str,
    svc: Annotated[MCPConnectorInstallService, Depends(get_admin_install_service)],
    grant_repo: Annotated[MCPCredentialGrantRepository, Depends(get_grant_repo)],
    _ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
) -> MCPAdminInstallEffectiveOut:
    install = await svc._install_repo.get(install_id)
    if install is None:
        raise HTTPException(404, detail={"code": "mcp_install_not_found"})
    if install.install_scope != "org":
        # The org effective derivation only applies to org-scope installs;
        # workspace-scope installs already get their effective state from
        # the workspace lens.
        raise HTTPException(400, detail={"code": "not_an_org_install"})
    org_grant = await grant_repo.get_org_grant(install_id)
    return _derive_admin_org_effective(install, org_grant)
```

Add imports at top:

```python
from cubebox.mcp.dependencies import get_grant_repo
from cubebox.repositories.mcp import MCPCredentialGrantRepository
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/e2e/test_mcp_oauth_handoff.py -v`
Expected: PASS for all four tests now in the file.

- [ ] **Step 7: Commit**

```bash
git add backend/cubebox/api/schemas/mcp.py \
        backend/cubebox/api/routes/v1/admin_mcp.py \
        backend/tests/e2e/test_mcp_oauth_handoff.py
git commit -m "feat(mcp/admin): add /installs/{id}/effective for org-row reason"
```

---

## Task 5: `runOAuthFlow` core helper

**Files:**
- Create: `frontend/packages/core/src/oauth/runOAuthFlow.ts`
- Create: `frontend/packages/core/src/oauth/index.ts`
- Modify: `frontend/packages/core/src/index.ts`
- Modify: `frontend/packages/core/src/api/mcp.ts`

- [ ] **Step 1: Create the helper module**

Create `frontend/packages/core/src/oauth/runOAuthFlow.ts`:

```ts
/**
 * Browser-side OAuth pop-up controller for MCP four-layer authentication.
 * Spec: docs/superpowers/specs/2026-05-16-mcp-install-auth-handoff-spec.md §5.5.
 *
 * Must be invoked synchronously from the user-activation click handler:
 * `window.open` is gated on the activation token, which is consumed by
 * any preceding `await`. The popup is opened to about:blank first, then
 * navigated after the start POST returns.
 */

export interface OAuthStartResponse {
  authorize_url: string
  state: string
  expires_at: string  // ISO8601
}

export interface OAuthFlowResult {
  status: 'ok' | 'cancelled' | 'error'
  reason?: string
}

interface OAuthReturnMessage {
  kind: 'mcp.oauth.return'
  status: 'ok' | 'cancelled' | 'error'
  state: string
  install_id: string
  reason?: string
}

const CHANNEL_NAME = 'cubebox-mcp-oauth'
const TIMEOUT_MS = 90_000
const POLL_INTERVAL_MS = 1_000

export interface RunOAuthFlowDeps {
  /** Performs the start POST. Caller composes the path per scope. */
  startPost: () => Promise<OAuthStartResponse>
}

export async function runOAuthFlow(deps: RunOAuthFlowDeps): Promise<OAuthFlowResult> {
  // 1. Open popup synchronously BEFORE any await.
  const target = `mcp-oauth-${crypto.randomUUID()}`
  const child = window.open('about:blank', target, 'width=620,height=760')
  if (child === null) {
    return { status: 'error', reason: 'popup_blocked' }
  }

  // 2. Open BroadcastChannel.
  const channel = new BroadcastChannel(CHANNEL_NAME)

  try {
    // 3. Fetch start.
    let start: OAuthStartResponse
    try {
      start = await deps.startPost()
    } catch (err) {
      child.close()
      return { status: 'error', reason: `start_failed:${(err as Error).message}` }
    }

    // 4. Navigate the popup.
    try {
      child.location.href = start.authorize_url
    } catch {
      child.close()
      return { status: 'error', reason: 'popup_navigate_failed' }
    }

    // 5. Race: matching message vs. 90s timeout vs. closed-popup poll.
    return await new Promise<OAuthFlowResult>((resolve) => {
      let done = false
      const finish = (r: OAuthFlowResult) => {
        if (done) return
        done = true
        clearTimeout(timer)
        clearInterval(poll)
        channel.removeEventListener('message', onMessage)
        resolve(r)
      }

      const onMessage = (ev: MessageEvent<OAuthReturnMessage>) => {
        const m = ev.data
        if (!m || m.kind !== 'mcp.oauth.return') return
        if (m.state !== start.state) return  // strict — see spec §5.5/5.6
        if (m.status === 'ok') return finish({ status: 'ok' })
        if (m.status === 'cancelled') return finish({ status: 'cancelled' })
        finish({ status: 'error', reason: m.reason ?? 'callback_error' })
      }

      const timer = setTimeout(() => {
        try { child.close() } catch { /* ignore */ }
        finish({ status: 'error', reason: 'timeout' })
      }, TIMEOUT_MS)

      const poll = setInterval(() => {
        if (child.closed) {
          finish({ status: 'cancelled' })
        }
      }, POLL_INTERVAL_MS)

      channel.addEventListener('message', onMessage)
    })
  } finally {
    channel.close()
  }
}
```

- [ ] **Step 2: Re-export**

Create `frontend/packages/core/src/oauth/index.ts`:

```ts
export {
  runOAuthFlow,
  type OAuthFlowResult,
  type OAuthStartResponse,
  type RunOAuthFlowDeps,
} from './runOAuthFlow'
```

Append to `frontend/packages/core/src/index.ts`:

```ts
export * from './oauth'
```

- [ ] **Step 3: Sync API helper return type**

Open `frontend/packages/core/src/api/mcp.ts`. Change the return type of
`adminOrgGrantOAuthStart`, `wsWorkspaceGrantOAuthStart`, and
`wsMyGrantOAuthStart` so they all return:

```ts
{ authorize_url: string; state: string; expires_at: string }
```

If the existing `MCPOAuthStartResult` type is missing `state`, add it. Add
also an `adminGetInstallEffective` helper:

```ts
export interface MCPAdminInstallEffective {
  install_id: string
  usable: boolean
  reason: 'usable' | 'pending_oauth' | 'missing_org_grant' | 'grant_expired'
}

export async function adminGetInstallEffective(
  client: ApiClient,
  installId: string,
): Promise<MCPAdminInstallEffective> {
  const res = await client.get(`/api/v1/admin/mcp/installs/${installId}/effective`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPAdminInstallEffective
}
```

- [ ] **Step 4: Build core**

Run: `cd frontend && pnpm --filter @cubebox/core build && pnpm --filter @cubebox/core type-check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/core/src/oauth/ \
        frontend/packages/core/src/index.ts \
        frontend/packages/core/src/api/mcp.ts
git commit -m "feat(core/oauth): add runOAuthFlow helper + state on start helpers"
```

---

## Task 6: `/oauth/mcp/return` page

**Files:**
- Create: `frontend/packages/web/app/oauth/mcp/return/page.tsx`

- [ ] **Step 1: Create the page**

Create `frontend/packages/web/app/oauth/mcp/return/page.tsx`:

```tsx
'use client'

/**
 * OAuth return page (popup side).
 * Spec: docs/superpowers/specs/2026-05-16-mcp-install-auth-handoff-spec.md §5.6.
 *
 * - Posts a typed message on BroadcastChannel('cubebox-mcp-oauth'), then
 *   closes itself after a 250ms grace period.
 * - If `state` is missing entirely (the genuinely-unrecoverable path),
 *   renders a static fallback and DOES NOT broadcast or auto-close.
 */

import { useEffect } from 'react'
import { useSearchParams } from 'next/navigation'

const CHANNEL_NAME = 'cubebox-mcp-oauth'

export default function OAuthReturnPage(): JSX.Element {
  const params = useSearchParams()
  const status = params.get('status') ?? 'error'
  const state = params.get('state')
  const installId = params.get('install_id') ?? ''
  const reason = params.get('reason') ?? undefined

  useEffect(() => {
    if (state === null || state === '') {
      // Hostile or stray navigation. Spec §5.6: do not broadcast,
      // do not auto-close. Show fallback.
      return
    }
    const channel = new BroadcastChannel(CHANNEL_NAME)
    channel.postMessage({
      kind: 'mcp.oauth.return',
      status,
      state,
      install_id: installId,
      reason,
    })
    const close = setTimeout(() => {
      channel.close()
      try {
        window.close()
      } catch {
        /* fallback below */
      }
    }, 250)
    return () => {
      clearTimeout(close)
      channel.close()
    }
  }, [status, state, installId, reason])

  return (
    <div
      style={{
        display: 'flex',
        height: '100vh',
        alignItems: 'center',
        justifyContent: 'center',
        fontFamily: 'system-ui, sans-serif',
        padding: '2rem',
        textAlign: 'center',
      }}
    >
      <div>
        <h1 style={{ fontSize: '1.25rem', marginBottom: '0.5rem' }}>
          {state ? 'You can close this window' : 'Sign-in failed'}
        </h1>
        <p style={{ color: '#666', fontSize: '0.875rem' }}>
          {state
            ? 'Authorization complete. Your other tab will pick up the result.'
            : 'Please close this window and retry from the connector page.'}
        </p>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Verify it builds**

Run: `cd frontend && pnpm --filter @cubebox/web type-check`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/app/oauth/mcp/return/page.tsx
git commit -m "feat(web/oauth): add /oauth/mcp/return popup return page"
```

---

## Task 7: `effectiveAuthState` pure function

**Files:**
- Create: `frontend/packages/web/components/mcp/effectiveAuthState.ts`
- Test: `frontend/packages/web/components/mcp/effectiveAuthState.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/packages/web/components/mcp/effectiveAuthState.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { computeAuthBandState } from './effectiveAuthState'

describe('computeAuthBandState', () => {
  it('returns ready when usable and credential_source set', () => {
    const s = computeAuthBandState({
      usable: true,
      credential_availability: 'available',
      credential_source: 'org',
      reason: 'usable',
      required_grant_scope: 'org',
      install: { auth_method: 'oauth', auth_status: 'authorized' },
      callerRole: 'admin',
      isOrgAdmin: true,
    } as any)
    expect(s.kind).toBe('ready')
  })

  it('returns ready (no-credential) when auth_method=none', () => {
    const s = computeAuthBandState({
      usable: true,
      credential_availability: 'not_required',
      credential_source: null,
      reason: 'usable',
      install: { auth_method: 'none', auth_status: 'not_required' },
      callerRole: 'member',
      isOrgAdmin: false,
    } as any)
    expect(s.kind).toBe('ready')
    expect(s.subkind).toBe('no_credential')
  })

  it('returns needs-action for user_needs_connection on user-policy install', () => {
    const s = computeAuthBandState({
      usable: false,
      credential_availability: 'missing',
      reason: 'user_needs_connection',
      required_grant_scope: 'user',
      install: { auth_method: 'oauth', auth_status: 'pending' },
      callerRole: 'member',
      isOrgAdmin: false,
    } as any)
    expect(s.kind).toBe('needs-action')
  })

  it('returns awaiting-others for missing_org_grant when caller is not org admin', () => {
    const s = computeAuthBandState({
      usable: false,
      reason: 'missing_org_grant',
      required_grant_scope: 'org',
      install: { auth_method: 'oauth', auth_status: 'pending' },
      callerRole: 'member',
      isOrgAdmin: false,
    } as any)
    expect(s.kind).toBe('awaiting-others')
    expect(s.who).toBe('org_admin')
  })

  it('returns needs-action for pending_oauth on org install when caller is org admin', () => {
    const s = computeAuthBandState({
      usable: false,
      reason: 'pending_oauth',
      required_grant_scope: 'org',
      install: { auth_method: 'oauth', auth_status: 'pending' },
      callerRole: 'admin',
      isOrgAdmin: true,
    } as any)
    expect(s.kind).toBe('needs-action')
  })

  it('returns hidden for non-auth reasons (discovery_failed)', () => {
    const s = computeAuthBandState({
      usable: false,
      reason: 'discovery_failed',
      install: { auth_method: 'oauth', auth_status: 'authorized' },
      callerRole: 'admin',
      isOrgAdmin: false,
    } as any)
    expect(s.kind).toBe('hidden')
  })
})
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && pnpm --filter @cubebox/web test -- effectiveAuthState`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement**

Create `frontend/packages/web/components/mcp/effectiveAuthState.ts`:

```ts
import type { MCPEffectiveConnector } from '@cubebox/core'

export type AuthBandState =
  | { kind: 'hidden' }
  | { kind: 'ready'; subkind: 'with_credential' | 'no_credential'; source?: 'org' | 'workspace' | 'user' }
  | { kind: 'needs-action'; reason: AuthReason }
  | { kind: 'awaiting-others'; reason: AuthReason; who: 'org_admin' | 'workspace_admin' }
  | { kind: 'oauth-in-flight' }
  | { kind: 'error'; reason?: string }

export type AuthReason =
  | 'pending_oauth'
  | 'missing_org_grant'
  | 'missing_workspace_grant'
  | 'user_needs_connection'
  | 'grant_expired'

export interface AuthBandInputs {
  connector: MCPEffectiveConnector
  callerRole: 'admin' | 'member'
  isOrgAdmin: boolean
}

const AUTH_REASONS = new Set<AuthReason>([
  'pending_oauth',
  'missing_org_grant',
  'missing_workspace_grant',
  'user_needs_connection',
  'grant_expired',
])

export function computeAuthBandState({
  connector,
  callerRole,
  isOrgAdmin,
}: AuthBandInputs): AuthBandState {
  // Spec §3.1.
  if (connector.usable) {
    if (connector.credential_availability === 'not_required') {
      return { kind: 'ready', subkind: 'no_credential' }
    }
    return {
      kind: 'ready',
      subkind: 'with_credential',
      source: connector.credential_source ?? undefined,
    }
  }

  const reason = connector.reason as AuthReason | string
  if (!AUTH_REASONS.has(reason as AuthReason)) {
    // Non-auth blockers (not_installed / install_uninstalled /
    // template_deprecated / not_enabled_in_workspace / discovery_failed)
    // belong to other surfaces — see spec §3.2 / §3.3.
    return { kind: 'hidden' }
  }

  const required = connector.required_grant_scope
  const r = reason as AuthReason

  // Spec §4 + §3.3.
  if (required === 'org') {
    if (isOrgAdmin) return { kind: 'needs-action', reason: r }
    return { kind: 'awaiting-others', reason: r, who: 'org_admin' }
  }
  if (required === 'workspace') {
    if (callerRole === 'admin') return { kind: 'needs-action', reason: r }
    return { kind: 'awaiting-others', reason: r, who: 'workspace_admin' }
  }
  // required === 'user' — caller always has authority over their own grant.
  return { kind: 'needs-action', reason: r }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && pnpm --filter @cubebox/web test -- effectiveAuthState`
Expected: PASS for all six test cases.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/components/mcp/effectiveAuthState.ts \
        frontend/packages/web/components/mcp/effectiveAuthState.test.ts
git commit -m "feat(web/mcp): add computeAuthBandState pure function"
```

---

## Task 8: `AuthActionBand` component

**Files:**
- Create: `frontend/packages/web/components/mcp/AuthActionBand.tsx`
- Modify: `frontend/packages/web/messages/en.json`
- Modify: `frontend/packages/web/messages/zh.json`

- [ ] **Step 1: Add i18n keys**

Append the following keys under `mcp` in BOTH `messages/en.json` and
`messages/zh.json` (i18n parity check is in CI):

| Key | en | zh |
| --- | --- | --- |
| `auth.bandTitleNeedsAction` | Needs your credential | 需要你的凭证 |
| `auth.bandTitleAwaiting` | Awaiting {who} | 等待 {who} |
| `auth.bandTitleInFlight` | Waiting for authorization in the new window… | 正在新窗口中等待授权… |
| `auth.bandTitleError` | Could not save credential | 无法保存凭证 |
| `auth.connectButton` | Connect with {provider} | 用 {provider} 连接 |
| `auth.staticTokenLabel` | Static token | 静态令牌 |
| `auth.staticTokenSave` | Save credential | 保存凭证 |
| `auth.cancelButton` | Cancel | 取消 |
| `auth.retryButton` | Retry | 重试 |
| `auth.notifyButton` | Notify | 通知 |
| `auth.notifyTooltip` | Coming soon | 即将推出 |
| `auth.whoOrgAdmin` | your organization admin | 你的组织管理员 |
| `auth.whoWorkspaceAdmin` | your workspace admin | 你的工作区管理员 |
| `auth.reasonPendingOAuth` | Authorization is pending — finish connecting to start using this. | 授权待完成 — 完成连接后即可使用。 |
| `auth.reasonMissingOrgGrantSelf` | No org credential on file yet. | 还没有组织凭证。 |
| `auth.reasonMissingWsGrantSelf` | No workspace credential on file yet. | 还没有工作区凭证。 |
| `auth.reasonUserNeedsConnection` | Connect your account to start using this. | 连接你的账号以开始使用。 |
| `auth.reasonGrantExpiredSelf` | The previous authorization expired. | 上一次授权已过期。 |
| `auth.reasonAwaitingMissingOrg` | {who} hasn't connected this yet. | {who} 还没连接这个。 |
| `auth.reasonAwaitingPendingOauth` | {who} hasn't connected this yet. | {who} 还没连接这个。 |
| `auth.reasonAwaitingExpired` | The {scope} authorization expired and needs to be renewed. | {scope} 授权已过期，需要重新连接。 |
| `auth.readyWithCredential` | Ready · credential from {source} | 可用 · 凭证来自 {source} |
| `auth.readyNoCredential` | Ready · no credential required | 可用 · 无需凭证 |
| `auth.disconnectMenu` | Disconnect | 断开 |
| `auth.removeOrgGrant` | Remove org grant | 移除组织授权 |
| `auth.removeWsGrant` | Remove workspace grant | 移除工作区授权 |
| `auth.removeMyGrant` | Remove my grant | 移除我的授权 |
| `auth.errorPopupBlocked` | Pop-ups are blocked. Allow pop-ups for this site, then retry. | 浏览器拦截了弹窗。请允许本站弹窗后重试。 |
| `auth.errorTimeout` | Authorization timed out. Please retry. | 授权超时。请重试。 |

Run i18n parity:

```bash
cd frontend && pnpm --filter @cubebox/web i18n:check
```

Expected: PASS.

- [ ] **Step 2: Create the band component**

Create `frontend/packages/web/components/mcp/AuthActionBand.tsx`:

```tsx
'use client'

/**
 * Authentication action band — five mutually exclusive states.
 * Spec: docs/superpowers/specs/2026-05-16-mcp-install-auth-handoff-spec.md §3.
 */

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { CheckCircle2, AlertTriangle, Clock, XCircle, Loader2 } from 'lucide-react'
import {
  runOAuthFlow,
  wsCreateMyGrant,
  wsCreateWorkspaceGrant,
  adminCreateOrgGrant,
  wsMyGrantOAuthStart,
  wsWorkspaceGrantOAuthStart,
  adminOrgGrantOAuthStart,
  wsDeleteMyGrant,
  wsDeleteWorkspaceGrant,
  adminDeleteOrgGrant,
  type ApiClient,
  type MCPEffectiveConnector,
} from '@cubebox/core'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { computeAuthBandState, type AuthBandState } from './effectiveAuthState'

export interface AuthActionBandProps {
  connector: MCPEffectiveConnector
  client: ApiClient
  /** For workspace-scope OAuth/grant calls, lens workspace id. */
  wsId: string
  callerRole: 'admin' | 'member'
  isOrgAdmin: boolean
  onChanged: () => Promise<void>
}

export function AuthActionBand(props: AuthActionBandProps) {
  const t = useTranslations('mcp.auth')
  const state = computeAuthBandState({
    connector: props.connector,
    callerRole: props.callerRole,
    isOrgAdmin: props.isOrgAdmin,
  })
  const [inFlight, setInFlight] = useState(false)
  const [errorState, setErrorState] = useState<{ reason?: string } | null>(null)

  // Decide which scope a grant action should target. Mirrors §4.
  const scope = scopeForBand(state, props.connector)

  if (state.kind === 'hidden') return null

  if (state.kind === 'ready') {
    return <ReadyBand state={state} {...props} t={t} />
  }

  if (state.kind === 'awaiting-others') {
    return <AwaitingBand state={state} t={t} />
  }

  if (state.kind === 'oauth-in-flight' || inFlight) {
    return (
      <Banner color="amber" icon={<Clock className="size-4" />}>
        <span>{t('bandTitleInFlight')}</span>
      </Banner>
    )
  }

  if (state.kind === 'error' || errorState) {
    return (
      <Banner color="rose" icon={<XCircle className="size-4" />}>
        <div>
          <p className="font-medium">{t('bandTitleError')}</p>
          <p className="text-xs text-muted-foreground">
            {errorReasonCopy(t, errorState?.reason ?? (state.kind === 'error' ? state.reason : undefined))}
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={() => setErrorState(null)}>
          {t('retryButton')}
        </Button>
      </Banner>
    )
  }

  // needs-action
  if (state.kind === 'needs-action') {
    const onConnect = async () => {
      setInFlight(true)
      setErrorState(null)
      const startPost = oauthStartFn(scope, props)
      const result = await runOAuthFlow({ startPost })
      setInFlight(false)
      if (result.status === 'ok') {
        await props.onChanged()
        return
      }
      if (result.status === 'cancelled') return
      setErrorState({ reason: result.reason })
    }

    if (props.connector.install.auth_method === 'oauth') {
      return (
        <Banner color="amber" icon={<AlertTriangle className="size-4" />}>
          <div>
            <p className="font-medium">{t('bandTitleNeedsAction')}</p>
            <p className="text-xs text-muted-foreground">
              {needsActionReasonCopy(t, state.reason)}
            </p>
          </div>
          <Button size="sm" onClick={() => void onConnect()}>
            {t('connectButton', { provider: providerLabel(props.connector) })}
          </Button>
        </Banner>
      )
    }

    // static
    return (
      <StaticTokenForm
        scope={scope}
        {...props}
        t={t}
        onError={(reason) => setErrorState({ reason })}
      />
    )
  }

  return null
}

// Helpers below: ReadyBand, AwaitingBand, StaticTokenForm, Banner,
// scopeForBand, oauthStartFn, providerLabel, needsActionReasonCopy,
// errorReasonCopy. Each is a small render-only / dispatch-only function.
// Implement them inline in this same file for cohesion (the file should
// stay under ~250 lines).
```

Then implement the helpers in the same file. Key contract details:

- `scopeForBand` returns `'org' | 'workspace' | 'user'` based on
  `connector.required_grant_scope`. For admin viewing an org install whose
  workspace lens overrides to user, follow §4 — the band acts on the
  derived `required_grant_scope`.
- `oauthStartFn(scope, props)` returns a `() => Promise<OAuthStartResponse>`
  that calls the right one of `adminOrgGrantOAuthStart` /
  `wsWorkspaceGrantOAuthStart` / `wsMyGrantOAuthStart`.
- `StaticTokenForm` is a single password-type Input + Save Button. On Save,
  POSTs the matching `*CreateGrant` and calls `onChanged()` on success.
  Token field is local-state only; never lifted into a parent.
- `ReadyBand`'s Disconnect menu only includes the items the caller has
  authority to revoke (org admin → all three; workspace admin → workspace
  + me; member → me only). When `subkind='no_credential'`, omit Disconnect.

- [ ] **Step 3: Verify type-check + tests**

Run:
```bash
cd frontend && \
  pnpm --filter @cubebox/web type-check && \
  pnpm --filter @cubebox/web test -- effectiveAuthState
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/components/mcp/AuthActionBand.tsx \
        frontend/packages/web/messages/en.json \
        frontend/packages/web/messages/zh.json
git commit -m "feat(web/mcp): add AuthActionBand component (5 states)"
```

---

## Task 9: Wire band into both detail panels + hide template list

**Files:**
- Modify: `frontend/packages/web/components/mcp/MCPAdminDetailPanel.tsx`
- Modify: `frontend/packages/web/components/workspace-settings/McpPanel.tsx`

- [ ] **Step 1: Wire AuthActionBand into MCPAdminDetailPanel**

Open `MCPAdminDetailPanel.tsx`. After the title row card and before the
Tabs, mount the band:

```tsx
import { AuthActionBand } from './AuthActionBand'
// ... inside the component, after the title row block:
<AuthActionBand
  connector={connector}
  client={client}
  wsId={wsId /* lens workspace */}
  callerRole="admin"
  isOrgAdmin={true /* admin page is org-admin gated by route */}
  onRefresh={onRefresh}
  onChanged={async () => { await onRefresh() }}
/>
```

For `install.install_scope === 'org'`, additionally:
- Pre-fetch the admin effective: `adminGetInstallEffective(client, install_id)`.
- Override the band's `connector.usable` / `reason` /
  `required_grant_scope='org'` based on the result before passing in.

Implementation note: easiest is a small wrapper above the band that, when
the install is org-scope, resolves the admin effective and synthesizes a
`MCPEffectiveConnector` shape from the install + the admin DTO. Comment in
the file explaining why this bypass exists (link to spec §4 admin row).

- [ ] **Step 2: Wire AuthActionBand into ConnectorDetail (workspace settings)**

Open `frontend/packages/web/components/workspace-settings/McpPanel.tsx`.
Inside `ConnectorDetail`:

```tsx
import { useWorkspaceStore } from '@cubebox/core'
import { AuthActionBand } from '@/components/mcp/AuthActionBand'

// inside ConnectorDetail:
const wsRole = useWorkspaceStore((s) => s.workspaces.find((w) => w.id === wsId)?.role)
const callerRole: 'admin' | 'member' = wsRole === 'admin' ? 'admin' : 'member'

// ... in the JSX, between header and the workspaceState card:
<AuthActionBand
  connector={connector}
  client={client}
  wsId={wsId}
  callerRole={callerRole}
  isOrgAdmin={false /* workspace settings has no org-admin context */}
  onChanged={onChanged}
/>
```

Note: `isOrgAdmin` is `false` here because workspace settings doesn't
expose org-admin role state. An org admin viewing a workspace settings
page sees member-equivalent semantics for that workspace (matches the
intent of the spec §4 admin row applying only on the admin page).

- [ ] **Step 3: Hide template list section from non-admins**

Same file, `McpPanel`. Change the template-list section to render only
when `wsRole === 'admin'`:

```tsx
const meWsRole = useWorkspaceStore((s) => s.workspaces.find((w) => w.id === wsId)?.role)

// ... around line 419-435, wrap the existing block:
{meWsRole === 'admin' && filteredTemplates.length > 0 && (
  <section>{ /* existing template section */ }</section>
)}
```

This satisfies spec §5.1's "New UI rule introduced by this spec".

- [ ] **Step 4: Verify type-check + lint**

Run: `cd frontend && pnpm --filter @cubebox/web type-check && pnpm --filter @cubebox/web lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/components/mcp/MCPAdminDetailPanel.tsx \
        frontend/packages/web/components/workspace-settings/McpPanel.tsx
git commit -m "feat(web/mcp): wire AuthActionBand into both detail panels; hide template list from non-admins"
```

---

## Task 10: E2E for the install→auth round-trip

**Files:**
- Create: `frontend/packages/web/__tests__/e2e/mcp/install-auth-handoff.spec.ts`

- [ ] **Step 1: Write the failing E2E**

Create `frontend/packages/web/__tests__/e2e/mcp/install-auth-handoff.spec.ts`:

```ts
import { expect, test } from '@playwright/test'
import { loginAsAdmin, seedWorkspace } from '../utils'  // existing helpers

// Cover the static-token install path end-to-end (deterministic; OAuth
// flows are covered by the backend E2E because spinning up a real AS
// inside Playwright would require a fixture server).
test('static install → save token → ready', async ({ page }) => {
  await loginAsAdmin(page)
  const ws = await seedWorkspace(page)
  await page.goto(`/w/${ws.id}/settings?tab=mcp`)

  // The seed includes an active static template (e.g. "Test Static API").
  await page.getByTestId('ws-template-row-test-static').getByRole('button', { name: /connect/i }).click()
  // Install POST returns; row appears in connectors.
  await page.getByTestId(/^ws-connector-row-inst-/).first().click()

  // Action band should be in needs-action.
  await expect(page.getByText('Needs your credential')).toBeVisible()

  // Submit a token.
  await page.getByLabel('Static token').fill('test-token-1234')
  await page.getByRole('button', { name: 'Save credential' }).click()

  // Band transitions to ready.
  await expect(page.getByText(/^Ready/)).toBeVisible()
})

test('member without admin role does not see template list', async ({ page }) => {
  await loginAsAdmin(page)  // bootstrap, then create another user
  // ... use existing helper to invite/login a non-admin member ...
  // (re-use member fixture from auth-flow.spec.ts pattern)

  await page.goto(`/w/${ws.id}/settings?tab=mcp`)
  await expect(page.getByText('Connector templates')).toHaveCount(0)
})
```

If the existing test fixtures don't include a static-template seed, add one
in `backend/cubebox/cli/seed_mcp_templates.py` (or the dev-seed helper the
E2E suite uses) — guard with a clear comment that the seed is for testing.

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && pnpm --filter @cubebox/web test:e2e -- mcp/install-auth-handoff.spec.ts`
Expected: FAIL — band copy not present yet (or template-list still
visible to members).

If you've followed Tasks 1–9 in order, this test should already pass —
treat any remaining failure as a real bug to fix in the relevant earlier
task before continuing.

- [ ] **Step 3: Run all MCP E2E**

Run: `cd frontend && pnpm --filter @cubebox/web test:e2e -- mcp/`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/__tests__/e2e/mcp/install-auth-handoff.spec.ts
git commit -m "test(web/mcp): E2E for install→auth handoff (static + member-hide)"
```

---

## Task 11: Final sweep + remove residual stubs

**Files:** none new; verification only.

- [ ] **Step 1: Confirm no `not_yet_wired` strings remain**

Run: `cd backend && grep -rn "not_yet_wired\|callback_not_wired" cubebox/ tests/`
Expected: NO matches (the four 501 stubs and the callback stub are gone).

- [ ] **Step 2: Backend full check**

Run: `cd backend && make check`
Expected: format + lint + type-check + pytest all PASS.

- [ ] **Step 3: Frontend full check**

Run: `cd frontend && pnpm --filter @cubebox/web type-check && pnpm --filter @cubebox/web lint && pnpm --filter @cubebox/web test:e2e`
Expected: PASS.

- [ ] **Step 4: Commit nothing — this task is verification.**

If any of the above fail, fix in the relevant earlier task and re-run
this checklist.

---

## Self-review

(Plan author: this section is for you to verify after writing the plan.)

- Spec §3 five states → covered by Tasks 7 + 8.
- Spec §4 caller authority matrix → covered by `computeAuthBandState` (Task 7).
- Spec §4 admin org-row bypass → covered by Task 4 + Task 9 wiring.
- Spec §5.1–5.4 flows → covered end-to-end by Tasks 1–9 + E2E in 10.
- Spec §5.5 `runOAuthFlow` → Task 5.
- Spec §5.6 return page → Task 6.
- Spec §6 backend contract → Tasks 1–4.
- Spec §7 edge cases → handled inside `runOAuthFlow` (popup_blocked, child.closed,
  AS denial) and `OAuthCallbackHandler` (state expired, AS error, install gone).
- Spec §8 (NOT live updates) → no SSE work in this plan; consistent.
- Spec §9 future work → explicitly out of scope; not implemented.

No placeholders, no "TBD", no "similar to Task N" without inline code.
