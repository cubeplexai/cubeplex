"""E2E coverage for discovery's 401 → forced-refresh → retry behavior.

Business invariant (spec 2026-07-17-mcp-oauth-401-refresh): a provider
may revoke an access token before its reported ``expires_in`` elapses
(Cloudflare did). When discovery hits a 401 with an OAuth grant that
still has a refresh credential, it must force one token refresh and
retry — not persist a sticky "Discovery error … 401" while the grant
sits ``valid`` for hours. If the refresh fails, or the server rejects
even the refreshed token, the grant must flip to ``expired`` so the UI
surfaces the Reconnect prompt.

The MCP server round trip (``_list_raw_mcp_tools``) and the AS token
endpoint (via a scripted token-manager stand-in) are the two outermost
externals and are stubbed; everything else — routes, repos, grant rows,
Postgres — is real.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.db.engine import _build_database_url
from cubeplex.mcp.exceptions import OAuthRefreshFailed
from cubeplex.models import MCPCredentialGrant

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db_session_maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    eng = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        yield async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    finally:
        await eng.dispose()


def _tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, description=None, input_schema=None, icons=None)


def _unauthorized() -> BaseException:
    request = httpx.Request("POST", "https://mcp.example.com/mcp")
    response = httpx.Response(401, request=request)
    err = httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)
    return ExceptionGroup("unhandled errors in a TaskGroup", [err])


class _ScriptedTokenManager:
    """Stands in for OAuthTokenManager: non-forced reads return the stale
    token; forced refreshes follow the scripted outcome."""

    def __init__(self, *, refresh_fails: bool = False) -> None:
        self.refresh_fails = refresh_fails
        self.force_calls = 0

    async def get_access_token_for_grant(
        self,
        *,
        grant: MCPCredentialGrant,
        grant_repo: Any,
        server_url: str,
        oauth_client_config: dict[str, Any],
        force_refresh: bool = False,
    ) -> str:
        if not force_refresh:
            return "stale-token"
        self.force_calls += 1
        if self.refresh_fails:
            # Mirror the real manager's failure side effect.
            grant.grant_status = "expired"
            await grant_repo.update(grant)
            raise OAuthRefreshFailed(400, error="invalid_grant")
        return "fresh-token"


async def _seed_oauth_org_install(
    client: httpx.AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> tuple[str, str]:
    """Create an oauth/org-policy template+connector and a valid-looking
    org grant whose token the fake MCP server will reject.

    Returns ``(connector_id, grant_id)``.
    """
    suffix = secrets.token_hex(4)
    tpl_resp = await client.post(
        "/api/v1/admin/mcp/templates",
        json={
            "name": f"OAuth 401 Retry {suffix}",
            "server_url": f"https://oauth-401-{suffix}.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "oauth",
            "default_credential_policy": "org",
        },
    )
    assert tpl_resp.status_code == 201, tpl_resp.text
    template_id = tpl_resp.json()["template_id"]
    dist_resp = await client.post(
        f"/api/v1/admin/mcp/templates/{template_id}/distribute",
        json={"enable_existing": False, "auto_enroll": False},
    )
    assert dist_resp.status_code == 200, dist_resp.text
    connector_id = dist_resp.json()["connector"]["connector_id"]

    async with db_session_maker() as session:
        from cubeplex.models import Credential
        from cubeplex.models.mcp import MCPConnector

        connector = await session.get(MCPConnector, connector_id)
        assert connector is not None
        access = Credential(
            org_id=connector.org_id,
            kind="mcp_oauth_access_token",
            name=f"access-{suffix}",
            value_encrypted=b"opaque",
        )
        refresh = Credential(
            org_id=connector.org_id,
            kind="mcp_oauth_refresh_token",
            name=f"refresh-{suffix}",
            value_encrypted=b"opaque",
        )
        session.add(access)
        session.add(refresh)
        await session.flush()
        grant = MCPCredentialGrant(
            org_id=connector.org_id,
            connector_id=connector_id,
            grant_scope="org",
            auth_method="oauth",
            credential_id=access.id,
            refresh_credential_id=refresh.id,
            # The incident shape: recorded expiry is far away, token dead.
            expires_at=datetime.now(UTC) + timedelta(hours=10),
            grant_status="valid",
        )
        session.add(grant)
        await session.commit()
        grant_id = grant.id
    return connector_id, grant_id


async def _run_discovery(
    client: httpx.AsyncClient,
    session: AsyncSession,
    *,
    connector_id: str,
    token_mgr: _ScriptedTokenManager,
) -> Any:
    from cubeplex.credentials.dependencies import build_credential_service
    from cubeplex.credentials.encryption import FernetBackend
    from cubeplex.mcp.dependencies import build_user_token_signer
    from cubeplex.services.mcp_discovery import discover_tools_for_install

    me_resp = await client.get("/api/v1/auth/me")
    assert me_resp.status_code == 200, me_resp.text
    user_id = me_resp.json()["id"]
    ws_resp = await client.get("/api/v1/workspaces")
    assert ws_resp.status_code == 200, ws_resp.text
    org_id = ws_resp.json()[0]["org_id"]

    from cryptography.fernet import Fernet

    backend = FernetBackend([Fernet.generate_key()])
    cred_service = build_credential_service(session, backend, org_id=org_id, actor_user_id=user_id)
    return await discover_tools_for_install(
        connector_id=connector_id,
        workspace_id=None,
        actor_user_id=user_id,
        session=session,
        cred_service=cred_service,
        signer=build_user_token_signer(),
        token_mgr=token_mgr,  # type: ignore[arg-type]
    )


async def _grant_status(db_session_maker: async_sessionmaker[AsyncSession], grant_id: str) -> str:
    async with db_session_maker() as session:
        grant = await session.get(MCPCredentialGrant, grant_id)
        assert grant is not None
        return grant.grant_status


async def test_401_then_success_after_forced_refresh(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stale token 401s; the retry must carry the refreshed token and
    the run must end 'ok' with no persisted error."""
    client, _ws = admin_client
    connector_id, grant_id = await _seed_oauth_org_install(client, db_session_maker)
    token_mgr = _ScriptedTokenManager()
    seen_auth: list[str | None] = []

    async def fake_load(*args: object, **kwargs: object) -> object:
        headers = kwargs.get("headers") or {}
        auth = headers.get("Authorization") if isinstance(headers, dict) else None
        seen_auth.append(auth)
        if auth != "Bearer fresh-token":
            raise _unauthorized()
        return SimpleNamespace(tools=[_tool("ping")], init_result=SimpleNamespace(serverInfo=None))

    monkeypatch.setattr("cubeplex.services.mcp_discovery._list_raw_mcp_tools", fake_load)

    async with db_session_maker() as session:
        result = await _run_discovery(
            client, session, connector_id=connector_id, token_mgr=token_mgr
        )
        await session.commit()

    assert result.discovery_status == "ok"
    assert result.last_error is None
    assert result.tool_count == 1
    assert token_mgr.force_calls == 1
    assert seen_auth == ["Bearer stale-token", "Bearer fresh-token"]
    assert await _grant_status(db_session_maker, grant_id) == "valid"


async def test_401_and_refresh_failure_marks_reauthorization_required(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ws = admin_client
    connector_id, grant_id = await _seed_oauth_org_install(client, db_session_maker)
    token_mgr = _ScriptedTokenManager(refresh_fails=True)

    async def fake_load(*args: object, **kwargs: object) -> object:
        raise _unauthorized()

    monkeypatch.setattr("cubeplex.services.mcp_discovery._list_raw_mcp_tools", fake_load)

    async with db_session_maker() as session:
        result = await _run_discovery(
            client, session, connector_id=connector_id, token_mgr=token_mgr
        )
        await session.commit()

    assert result.discovery_status == "error"
    assert result.last_error is not None
    assert result.last_error.startswith("oauth_reauthorization_required:")
    assert await _grant_status(db_session_maker, grant_id) == "expired"


async def test_401_twice_marks_grant_expired(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The server rejecting even a freshly-refreshed token means
    reauthorization is needed — the grant must not stay 'valid'."""
    client, _ws = admin_client
    connector_id, grant_id = await _seed_oauth_org_install(client, db_session_maker)
    token_mgr = _ScriptedTokenManager()

    async def fake_load(*args: object, **kwargs: object) -> object:
        raise _unauthorized()

    monkeypatch.setattr("cubeplex.services.mcp_discovery._list_raw_mcp_tools", fake_load)

    async with db_session_maker() as session:
        result = await _run_discovery(
            client, session, connector_id=connector_id, token_mgr=token_mgr
        )
        await session.commit()

    assert result.discovery_status == "error"
    assert token_mgr.force_calls == 1
    assert await _grant_status(db_session_maker, grant_id) == "expired"


async def test_non_oauth_401_keeps_existing_behavior(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No-auth installs must not enter the refresh loop on a 401."""
    client, _ws = admin_client
    suffix = secrets.token_hex(4)
    tpl_resp = await client.post(
        "/api/v1/admin/mcp/templates",
        json={
            "name": f"No Auth 401 {suffix}",
            "server_url": f"https://noauth-401-{suffix}.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert tpl_resp.status_code == 201, tpl_resp.text
    dist_resp = await client.post(
        f"/api/v1/admin/mcp/templates/{tpl_resp.json()['template_id']}/distribute",
        json={"enable_existing": False, "auto_enroll": False},
    )
    assert dist_resp.status_code == 200, dist_resp.text
    connector_id = dist_resp.json()["connector"]["connector_id"]
    token_mgr = _ScriptedTokenManager()
    calls = 0

    async def fake_load(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise _unauthorized()

    monkeypatch.setattr("cubeplex.services.mcp_discovery._list_raw_mcp_tools", fake_load)

    async with db_session_maker() as session:
        result = await _run_discovery(
            client, session, connector_id=connector_id, token_mgr=token_mgr
        )
        await session.commit()

    assert result.discovery_status == "error"
    assert calls == 1
    assert token_mgr.force_calls == 0
