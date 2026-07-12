"""E2E coverage for the lost-UI restoration features.

Removed in Task 9 template-centric cutover:
- test_admin_create_custom_install_for_org: POST /admin/mcp/installs removed
- test_admin_create_custom_install_rejects_credential_plaintext_with_scoped_policy: same
- test_promote_install_writes_org_scope_and_excludes_source: promote-to-org removed
- test_ws_active_tools_returns_namespaced_tools_with_icons: ws_mcp rewritten in Task 10
- test_ws_invoke_tool_returns_result: ws_mcp rewritten in Task 10

Fixtures that used direct MCPConnector inserts with template_id=None have been
rewritten to use POST /admin/mcp/templates + distribute so template_id FK is
satisfied by a real row.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.db.engine import _build_database_url

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session_maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    eng = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        yield async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    finally:
        await eng.dispose()


async def _resolve_org_user_for_client(
    client: httpx.AsyncClient,
    workspace_id: str,
) -> tuple[str, str]:
    """Return ``(org_id, user_id)`` for an authenticated admin client."""
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    user_id = me.json()["id"]
    ws_resp = await client.get("/api/v1/workspaces")
    assert ws_resp.status_code == 200, ws_resp.text
    workspaces = ws_resp.json()
    org_id = next(w["org_id"] for w in workspaces if w["id"] == workspace_id)
    return org_id, user_id


async def _create_and_distribute_template(
    client: httpx.AsyncClient,
    *,
    name_suffix: str,
    auth_method: str = "none",
    default_credential_policy: str = "none",
) -> tuple[str, str]:
    """Create an org-custom template and distribute it.

    Returns ``(template_id, connector_id)``.
    """
    tpl_resp = await client.post(
        "/api/v1/admin/mcp/templates",
        json={
            "name": f"Restore UI Test {name_suffix}",
            "server_url": f"https://restore-ui-{name_suffix}.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": auth_method,
            "default_credential_policy": default_credential_policy,
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
    return template_id, connector_id


@pytest_asyncio.fixture
async def seeded_static_org_install_with_tools_cache(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session_maker: async_sessionmaker[AsyncSession],
) -> str:
    """Org-scope connector pre-populated with two fake tools and a
    citation mapping for one of them.

    Uses the template-centric flow: create template → distribute → patch
    tools_cache directly so tests can exercise the DTO field exposure.
    """
    client, _ws_id = admin_client
    suffix = secrets.token_hex(4)
    _template_id, connector_id = await _create_and_distribute_template(
        client,
        name_suffix=f"static-tools-{suffix}",
        auth_method="none",
        default_credential_policy="none",
    )
    async with db_session_maker() as session:
        from cubebox.models.mcp import MCPConnector

        connector = await session.get(MCPConnector, connector_id)
        assert connector is not None
        connector.tools_cache = [
            {
                "name": "ping",
                "description": "say hi",
                "input_schema": {"type": "object"},
            },
            {
                "name": "pong",
                "description": "say bye",
                "input_schema": {"type": "object"},
            },
        ]
        connector.tool_citations = {
            "ping": {
                "content_type": "json",
                "source_type": "api",
                "content_field": None,
                "mapping": {"snippet": ""},
            }
        }
        await session.commit()
    return connector_id


# ---------------------------------------------------------------------------
# Task 1 — DTO exposes tools + tool_citations.
# ---------------------------------------------------------------------------


async def test_install_dto_exposes_tools_and_tool_citations(
    admin_client: tuple[httpx.AsyncClient, str],
    seeded_static_org_install_with_tools_cache: str,
) -> None:
    """``MCPConnectorOut`` must expose the tools list (not just
    tool_count) and tool_citations dict (for org admin callers)."""
    client, _ws = admin_client
    install_id = seeded_static_org_install_with_tools_cache
    res = await client.get(f"/api/v1/admin/mcp/installs/{install_id}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert "tools" in body, "tools field missing from install DTO"
    assert isinstance(body["tools"], list)
    if body["tools"]:
        sample = body["tools"][0]
        assert {"name", "description", "input_schema"} <= sample.keys()
    assert "tool_citations" in body
    assert isinstance(body["tool_citations"], dict) or body["tool_citations"] is None


# ---------------------------------------------------------------------------
# Task 2 — Discovery service writes tools_cache.
# ---------------------------------------------------------------------------


async def test_discover_tools_for_install_writes_tools_cache(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery service should fetch tools via cubepi and persist
    the result into install.tools_cache / .discovery_status."""
    client, _ws_id = admin_client
    suffix = secrets.token_hex(4)
    _template_id, connector_id = await _create_and_distribute_template(
        client, name_suffix=f"disc-test-{suffix}"
    )

    # Resolve org_id + user_id for service calls.
    me_resp = await client.get("/api/v1/auth/me")
    assert me_resp.status_code == 200, me_resp.text
    user_id = me_resp.json()["id"]
    ws_resp = await client.get("/api/v1/workspaces")
    assert ws_resp.status_code == 200, ws_resp.text
    org_id = ws_resp.json()[0]["org_id"]

    # Stub the cubepi helper used inside discover_tools_for_install.
    async def fake_load(*args: object, **kwargs: object) -> object:
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="ping",
                    description="say hi",
                    input_schema={"type": "object"},
                    icons=None,
                ),
                SimpleNamespace(
                    name="pong",
                    description="say bye",
                    input_schema={"type": "object"},
                    icons=None,
                ),
            ],
            init_result=SimpleNamespace(serverInfo=None),
        )

    monkeypatch.setattr("cubebox.services.mcp_discovery._list_raw_mcp_tools", fake_load)

    from cubebox.credentials.dependencies import build_credential_service
    from cubebox.credentials.encryption import FernetBackend
    from cubebox.mcp.dependencies import build_user_token_signer
    from cubebox.services.mcp_discovery import discover_tools_for_install

    backend = FernetBackend([_test_fernet_key().encode()])
    async with db_session_maker() as session:
        cred_service = build_credential_service(
            session, backend, org_id=org_id, actor_user_id=user_id
        )
        signer = build_user_token_signer()
        result = await discover_tools_for_install(
            connector_id=connector_id,
            workspace_id=None,
            actor_user_id=user_id,
            session=session,
            cred_service=cred_service,
            signer=signer,
            token_mgr=None,  # type: ignore[arg-type]
        )
        assert result.discovery_status == "ok"
        assert result.tool_count == 2
        names = sorted(t["name"] for t in result.tools_cache_raw)
        assert names == ["ping", "pong"]
        assert result.last_error is None


async def test_discover_tools_for_install_writes_discovery_metadata(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery should capture serverInfo (icons + websiteUrl) from the
    initialize handshake and Tool.icons from tools/list, then persist
    them into install.discovery_metadata. The shape is:

        {
          "server": {name, version, website_url, icons: [...]} | None,
          "tool_icons": {tool_name: [icon_dict, ...]}
        }

    Tools with no icons are omitted from ``tool_icons`` rather than
    stored as empty lists, to keep the JSON compact.
    """
    client, _ws_id = admin_client
    suffix = secrets.token_hex(4)
    _template_id, connector_id = await _create_and_distribute_template(
        client, name_suffix=f"disc-icons-{suffix}"
    )

    me_resp = await client.get("/api/v1/auth/me")
    assert me_resp.status_code == 200, me_resp.text
    user_id = me_resp.json()["id"]
    ws_resp = await client.get("/api/v1/workspaces")
    assert ws_resp.status_code == 200, ws_resp.text
    org_id = ws_resp.json()[0]["org_id"]

    init_result = SimpleNamespace(
        serverInfo=SimpleNamespace(
            name="IconedServer",
            version="2.1.0",
            websiteUrl="https://icons.example.com",
            icons=[
                SimpleNamespace(
                    src="https://icons.example.com/logo.svg",
                    mimeType="image/svg+xml",
                    sizes=None,
                    theme=None,
                )
            ],
        )
    )

    async def fake_load(*args: object, **kwargs: object) -> object:
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="ping",
                    description="say hi",
                    input_schema={"type": "object"},
                    icons=[
                        SimpleNamespace(
                            src="data:image/svg+xml;base64,zzz",
                            mimeType="image/svg+xml",
                            sizes=None,
                            theme="dark",
                        )
                    ],
                ),
                SimpleNamespace(
                    name="pong",
                    description="say bye",
                    input_schema={"type": "object"},
                    icons=None,
                ),
            ],
            init_result=init_result,
        )

    monkeypatch.setattr("cubebox.services.mcp_discovery._list_raw_mcp_tools", fake_load)
    # Deterministic: do not outbound-fetch the fixture URL during this test.
    # Materialisation is covered by unit tests with respx.
    monkeypatch.setattr("cubebox.mcp.icons.icons_fetch_remote_enabled", lambda: False)

    from cubebox.credentials.dependencies import build_credential_service
    from cubebox.credentials.encryption import FernetBackend
    from cubebox.mcp.dependencies import build_user_token_signer
    from cubebox.repositories.mcp import MCPConnectorRepository
    from cubebox.services.mcp_discovery import discover_tools_for_install

    backend = FernetBackend([_test_fernet_key().encode()])
    async with db_session_maker() as session:
        cred_service = build_credential_service(
            session, backend, org_id=org_id, actor_user_id=user_id
        )
        signer = build_user_token_signer()
        result = await discover_tools_for_install(
            connector_id=connector_id,
            workspace_id=None,
            actor_user_id=user_id,
            session=session,
            cred_service=cred_service,
            signer=signer,
            token_mgr=None,  # type: ignore[arg-type]
        )
        assert result.discovery_status == "ok"

    async with db_session_maker() as session:
        repo = MCPConnectorRepository(session, org_id=org_id)
        refreshed = await repo.get(connector_id)
        assert refreshed is not None
        meta = refreshed.discovery_metadata

    assert meta["server"] == {
        "name": "IconedServer",
        "version": "2.1.0",
        "website_url": "https://icons.example.com",
        "icons": [
            {
                "src": "https://icons.example.com/logo.svg",
                "mime_type": "image/svg+xml",
                "sizes": None,
                "theme": None,
            }
        ],
    }
    assert meta["tool_icons"] == {
        "ping": [
            {
                "src": "data:image/svg+xml;base64,zzz",
                "mime_type": "image/svg+xml",
                "sizes": None,
                "theme": "dark",
            }
        ]
    }


def _test_fernet_key() -> str:
    """Return a deterministic Fernet key for tests."""
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


@pytest_asyncio.fixture
async def seeded_static_org_install_no_grant(
    admin_client: tuple[httpx.AsyncClient, str],
) -> str:
    """Connector with ``default_credential_policy='none'`` — usable without any grant."""
    client, _ws_id = admin_client
    suffix = secrets.token_hex(4)
    _template_id, connector_id = await _create_and_distribute_template(
        client, name_suffix=f"no-grant-{suffix}"
    )
    return connector_id


@pytest_asyncio.fixture
async def seeded_oauth_user_policy_install(
    admin_client: tuple[httpx.AsyncClient, str],
) -> str:
    """Connector with ``default_credential_policy='user'`` — refresh-discovery
    against this requires ``workspace_id`` because the grant is per-user-per-workspace."""
    client, _ws_id = admin_client
    suffix = secrets.token_hex(4)
    _template_id, connector_id = await _create_and_distribute_template(
        client,
        name_suffix=f"oauth-user-{suffix}",
        auth_method="oauth",
        default_credential_policy="user",
    )
    return connector_id


# ---------------------------------------------------------------------------
# Task 3 — Refresh-discovery routes (admin + ws).
# ---------------------------------------------------------------------------


async def test_admin_refresh_discovery_writes_install(
    admin_client: tuple[httpx.AsyncClient, str],
    seeded_static_org_install_no_grant: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ws = admin_client
    install_id = seeded_static_org_install_no_grant

    async def fake_load(*args: object, **kwargs: object) -> object:
        return SimpleNamespace(
            tools=[SimpleNamespace(name="ping", description=None, input_schema=None, icons=None)],
            init_result=SimpleNamespace(serverInfo=None),
        )

    monkeypatch.setattr("cubebox.services.mcp_discovery._list_raw_mcp_tools", fake_load)

    res = await client.post(f"/api/v1/admin/mcp/installs/{install_id}/refresh-discovery", json={})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["discovery_status"] == "ok"
    assert body["tool_count"] == 1
    assert body["tools"][0]["name"] == "ping"


async def test_admin_refresh_discovery_requires_workspace_id_for_scoped_policy(
    admin_client: tuple[httpx.AsyncClient, str],
    seeded_oauth_user_policy_install: str,
) -> None:
    client, _ws = admin_client
    install_id = seeded_oauth_user_policy_install
    res = await client.post(f"/api/v1/admin/mcp/installs/{install_id}/refresh-discovery", json={})
    assert res.status_code == 422, res.text
    assert res.json()["detail"][0]["loc"][-1] == "workspace_id"


# ---------------------------------------------------------------------------
# Task 4 — Test connection route.
# ---------------------------------------------------------------------------


async def test_admin_test_connection_returns_tool_count(
    admin_client: tuple[httpx.AsyncClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ws = admin_client

    async def fake_load(*args: object, **kwargs: object) -> object:
        return SimpleNamespace(
            tools=[
                SimpleNamespace(name="a", description=None, input_schema=None),
                SimpleNamespace(name="b", description=None, input_schema=None),
            ],
            server=None,
            tool_infos=[],
        )

    monkeypatch.setattr("cubebox.api.routes.v1.admin_mcp.load_mcp_tools_http", fake_load)

    res = await client.post(
        "/api/v1/admin/mcp/test-connection",
        json={
            "server_url": "https://probe.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["tool_count"] == 2


async def test_admin_test_connection_rejects_static_plaintext_with_none_auth(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ws = admin_client
    res = await client.post(
        "/api/v1/admin/mcp/test-connection",
        json={
            "server_url": "https://probe.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "credential_plaintext": "should-not-be-here",
        },
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# Task 7 — Tool-citation upsert.
# ---------------------------------------------------------------------------


async def test_admin_upsert_tool_citation(
    admin_client: tuple[httpx.AsyncClient, str],
    seeded_static_org_install_with_tools_cache: str,
) -> None:
    client, _ws = admin_client
    install_id = seeded_static_org_install_with_tools_cache
    res = await client.put(
        f"/api/v1/admin/mcp/installs/{install_id}/tool-citations",
        json={
            "tool_name": "pong",
            "config": {
                "content_type": "json",
                "source_type": "web",
                "content_field": None,
                "mapping": {"snippet": "summary"},
            },
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["tool_citations"]["pong"]["mapping"] == {"snippet": "summary"}


async def test_admin_clear_tool_citation_with_null_config(
    admin_client: tuple[httpx.AsyncClient, str],
    seeded_static_org_install_with_tools_cache: str,
) -> None:
    client, _ws = admin_client
    install_id = seeded_static_org_install_with_tools_cache
    res = await client.put(
        f"/api/v1/admin/mcp/installs/{install_id}/tool-citations",
        json={"tool_name": "ping", "config": None},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "ping" not in body["tool_citations"]


# ---------------------------------------------------------------------------
# Task 8 — Try It routes (admin invoke).
# ---------------------------------------------------------------------------


async def test_admin_invoke_requires_workspace_id_for_scoped_policy(
    admin_client: tuple[httpx.AsyncClient, str],
    seeded_oauth_user_policy_install: str,
) -> None:
    client, _ws = admin_client
    install_id = seeded_oauth_user_policy_install
    res = await client.post(
        f"/api/v1/admin/mcp/installs/{install_id}/tools/foo/invoke",
        json={"arguments": {}},
    )
    assert res.status_code == 422, res.text
