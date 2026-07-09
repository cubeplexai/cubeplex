"""E2E coverage for the lost-UI restoration features."""

from __future__ import annotations

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


@pytest_asyncio.fixture
async def seeded_static_org_install_with_tools_cache(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session_maker: async_sessionmaker[AsyncSession],
) -> str:
    """Org-scope static install pre-populated with two fake tools and a
    citation mapping for one of them."""
    client, ws_id = admin_client
    org_id, user_id = await _resolve_org_user_for_client(client, ws_id)
    async with db_session_maker() as session:
        from cubebox.mcp._constants import server_url_hash
        from cubebox.models.mcp import MCPConnector

        install = MCPConnector(
            org_id=org_id,
            template_id=None,
            install_scope="org",
            workspace_id=None,
            name="seeded-with-tools",
            server_url="https://seeded.example.com/mcp",
            server_url_hash=server_url_hash("https://seeded.example.com/mcp"),
            transport="streamable_http",
            auth_method="static",
            default_credential_policy="org",
            auth_status="pending",
            install_state="active",
            tools_cache=[
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
            ],
            tool_citations={
                "ping": {
                    "content_type": "json",
                    "source_type": "api",
                    "content_field": None,
                    "mapping": {"snippet": ""},
                }
            },
            created_by_user_id=user_id,
        )
        session.add(install)
        await session.commit()
        await session.refresh(install)
        return install.id


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
    client, ws_id = admin_client
    org_id, user_id = await _resolve_org_user_for_client(client, ws_id)

    async with db_session_maker() as session:
        from cubebox.mcp._constants import server_url_hash
        from cubebox.models.mcp import MCPConnector

        install = MCPConnector(
            org_id=org_id,
            template_id=None,
            install_scope="org",
            workspace_id=None,
            name="disc-test",
            server_url="https://disc.example.com/mcp",
            server_url_hash=server_url_hash("https://disc.example.com/mcp"),
            transport="streamable_http",
            auth_method="none",
            default_credential_policy="none",
            auth_status="not_required",
            install_state="active",
            tools_cache=[],
            tool_citations={},
            created_by_user_id=user_id,
        )
        session.add(install)
        await session.commit()
        await session.refresh(install)
        install_id = install.id

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
            install_id=install_id,
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
    client, ws_id = admin_client
    org_id, user_id = await _resolve_org_user_for_client(client, ws_id)

    async with db_session_maker() as session:
        from cubebox.mcp._constants import server_url_hash
        from cubebox.models.mcp import MCPConnector

        install = MCPConnector(
            org_id=org_id,
            template_id=None,
            install_scope="org",
            workspace_id=None,
            name="disc-icons",
            server_url="https://icons.example.com/mcp",
            server_url_hash=server_url_hash("https://icons.example.com/mcp"),
            transport="streamable_http",
            auth_method="none",
            default_credential_policy="none",
            auth_status="not_required",
            install_state="active",
            tools_cache=[],
            tool_citations={},
            created_by_user_id=user_id,
        )
        session.add(install)
        await session.commit()
        await session.refresh(install)
        install_id = install.id

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
            install_id=install_id,
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
        refreshed = await repo.get(install_id)
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


async def test_ws_active_tools_returns_namespaced_tools_with_icons(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """``GET /ws/{wsId}/mcp/active-tools`` flattens usable installs into
    one tool entry per cached tool with namespaced_name + server name +
    server / tool icons sourced from ``discovery_metadata``.

    The namespaced_name must match what the runtime exposes to the LLM
    (``{slug}__{tool_name}`` capped at 64 chars), so the frontend can key
    its tool registry by the ``tool_call.name`` it sees on SSE.
    """
    client, ws_id = admin_client
    org_id, user_id = await _resolve_org_user_for_client(client, ws_id)

    async with db_session_maker() as session:
        from cubebox.mcp._constants import server_url_hash
        from cubebox.models.mcp import MCPConnector

        # Use an auth_method='none' org install — automatically usable for
        # any workspace in this org per the effective service rules
        # (no grant required).
        install = MCPConnector(
            org_id=org_id,
            template_id=None,
            install_scope="org",
            workspace_id=None,
            name="Linear",
            server_url="https://active-tools.example.com/mcp",
            server_url_hash=server_url_hash("https://active-tools.example.com/mcp"),
            transport="streamable_http",
            auth_method="none",
            default_credential_policy="none",
            auth_status="not_required",
            install_state="active",
            tools_cache=[
                {"name": "create_issue", "description": "Create an issue", "input_schema": {}},
                {"name": "list_issues", "description": "List issues", "input_schema": {}},
            ],
            tool_citations={},
            discovery_metadata={
                "server": {
                    "name": "Linear",
                    "version": "1.4.2",
                    "website_url": "https://linear.app",
                    "icons": [
                        {
                            "src": "https://linear.app/favicon.svg",
                            "mime_type": "image/svg+xml",
                            "sizes": None,
                            "theme": None,
                        }
                    ],
                },
                "tool_icons": {
                    "create_issue": [
                        {
                            "src": "data:image/svg+xml;base64,abc",
                            "mime_type": "image/svg+xml",
                            "sizes": None,
                            "theme": None,
                        }
                    ]
                },
            },
            created_by_user_id=user_id,
        )
        session.add(install)
        await session.commit()
        await session.refresh(install)
        # Org installs are invisible to a workspace unless that workspace
        # has an MCPWorkspaceConnectorState row — the effective service
        # uses it to scope the workspace lens.
        from cubebox.models.mcp import MCPWorkspaceConnectorState

        session.add(
            MCPWorkspaceConnectorState(
                org_id=org_id,
                workspace_id=ws_id,
                install_id=install.id,
                enabled=True,
                credential_policy="none",
                enablement_source="auto",
                updated_by_user_id=user_id,
            )
        )
        await session.commit()

    res = await client.get(f"/api/v1/ws/{ws_id}/mcp/active-tools")
    assert res.status_code == 200, res.text
    body = res.json()
    items = body["items"]
    by_namespaced = {it["namespaced_name"]: it for it in items}

    assert "Linear__create_issue" in by_namespaced
    assert "Linear__list_issues" in by_namespaced

    create = by_namespaced["Linear__create_issue"]
    assert create["bare_name"] == "create_issue"
    assert create["server_name"] == "Linear"
    assert create["server_icons"] == [
        {
            "src": "https://linear.app/favicon.svg",
            "mime_type": "image/svg+xml",
            "sizes": None,
            "theme": None,
        }
    ]
    assert create["tool_icons"] == [
        {
            "src": "data:image/svg+xml;base64,abc",
            "mime_type": "image/svg+xml",
            "sizes": None,
            "theme": None,
        }
    ]

    listt = by_namespaced["Linear__list_issues"]
    assert listt["tool_icons"] == []
    assert listt["server_icons"] == create["server_icons"]


def _test_fernet_key() -> str:
    """Return a deterministic Fernet key for tests."""
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


@pytest_asyncio.fixture
async def seeded_static_org_install_no_grant(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session_maker: async_sessionmaker[AsyncSession],
) -> str:
    """Org-scope ``auth_method='none'`` install — usable without any grant."""
    client, ws_id = admin_client
    org_id, user_id = await _resolve_org_user_for_client(client, ws_id)
    async with db_session_maker() as session:
        from cubebox.mcp._constants import server_url_hash
        from cubebox.models.mcp import MCPConnector

        url = f"https://no-grant-{ws_id}.example.com/mcp"
        install = MCPConnector(
            org_id=org_id,
            template_id=None,
            install_scope="org",
            workspace_id=None,
            name="no-grant",
            server_url=url,
            server_url_hash=server_url_hash(url),
            transport="streamable_http",
            auth_method="none",
            default_credential_policy="none",
            auth_status="not_required",
            install_state="active",
            tools_cache=[],
            tool_citations={},
            created_by_user_id=user_id,
        )
        session.add(install)
        await session.commit()
        await session.refresh(install)
        return install.id


@pytest_asyncio.fixture
async def seeded_oauth_user_policy_install(
    admin_client: tuple[httpx.AsyncClient, str],
    db_session_maker: async_sessionmaker[AsyncSession],
) -> str:
    """Org-scope OAuth install with ``default_credential_policy='user'`` —
    refresh-discovery against this requires ``workspace_id`` because the
    grant is per-user-per-workspace."""
    client, ws_id = admin_client
    org_id, user_id = await _resolve_org_user_for_client(client, ws_id)
    async with db_session_maker() as session:
        from cubebox.mcp._constants import server_url_hash
        from cubebox.models.mcp import MCPConnector

        url = f"https://oauth-user-{ws_id}.example.com/mcp"
        install = MCPConnector(
            org_id=org_id,
            template_id=None,
            install_scope="org",
            workspace_id=None,
            name="oauth-user",
            server_url=url,
            server_url_hash=server_url_hash(url),
            transport="streamable_http",
            auth_method="oauth",
            default_credential_policy="user",
            auth_status="pending",
            install_state="active",
            tools_cache=[],
            tool_citations={},
            created_by_user_id=user_id,
        )
        session.add(install)
        await session.commit()
        await session.refresh(install)
        return install.id


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
# Task 5 — Custom connector creation.
# ---------------------------------------------------------------------------


async def test_admin_create_custom_install_for_org(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ws = admin_client
    res = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": None,
            "install_scope": "org",
            "name": "My internal MCP",
            "server_url": "https://internal.corp/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
            "auto_enable": {"mode": "none"},
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["template_id"] is None
    assert body["name"] == "My internal MCP"
    assert body["install_scope"] == "org"


async def test_admin_create_custom_install_rejects_credential_plaintext_with_scoped_policy(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ws = admin_client
    res = await client.post(
        "/api/v1/admin/mcp/installs",
        json={
            "template_id": None,
            "install_scope": "org",
            "name": "scoped-fail",
            "server_url": "https://scoped-fail.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "static",
            "default_credential_policy": "user",
            "auto_enable": {"mode": "none"},
            "credential_plaintext": "should-fail",
        },
    )
    assert res.status_code == 422, res.text
    assert "credential_plaintext_only_valid_for_org_policy" in res.text


# ---------------------------------------------------------------------------
# Task 6 — Promote ws → org.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seeded_workspace_install_with_state(
    admin_client: tuple[httpx.AsyncClient, str],
    noauth_template_id: str,
) -> tuple[str, str, str]:
    """Admin installs a no-auth template into their workspace.

    Returns ``(install_id, source_workspace_id, source_policy)``.
    """
    client, workspace_id = admin_client
    res = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/installs",
        json={
            "template_id": noauth_template_id,
            "install_scope": "workspace",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    return body["connector_id"], workspace_id, body["default_credential_policy"]


@pytest_asyncio.fixture
async def extra_workspace_id(admin_client: tuple[httpx.AsyncClient, str]) -> str:
    """Create a second workspace inside the admin's org and return its id."""
    client, ws_id = admin_client
    org_id, _user_id = await _resolve_org_user_for_client(client, ws_id)
    res = await client.post(
        "/api/v1/workspaces",
        json={"name": "promote-extra", "org_id": org_id},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_promote_install_writes_org_scope_and_excludes_source(
    admin_client: tuple[httpx.AsyncClient, str],
    seeded_workspace_install_with_state: tuple[str, str, str],
    extra_workspace_id: str,
) -> None:
    """Promote a workspace install to org with mode='all' must:

    * flip ``install_scope`` to ``'org'``
    * clear ``install.workspace_id``
    * upsert state rows in OTHER workspaces
    * NOT overwrite the source workspace's existing state row
    * set ``auto_enroll_new_workspaces=true``
    """
    client, _ws = admin_client
    install_id, source_ws, source_state_policy = seeded_workspace_install_with_state
    other_ws = extra_workspace_id

    res = await client.post(
        f"/api/v1/admin/mcp/installs/{install_id}/promote-to-org",
        json={"distribution": {"mode": "all"}},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["install_scope"] == "org"
    assert body["workspace_id"] is None
    assert body["auto_enroll_new_workspaces"] is True

    # Source workspace's state row preserved untouched.
    state_res = await client.get(f"/api/v1/ws/{source_ws}/mcp/connectors")
    assert state_res.status_code == 200, state_res.text
    sources = [c for c in state_res.json()["items"] if c["install"]["connector_id"] == install_id]
    assert len(sources) == 1
    assert sources[0]["workspace_state"]["credential_policy"] == source_state_policy

    # Other workspace got a state row.
    other_res = await client.get(f"/api/v1/ws/{other_ws}/mcp/connectors")
    assert other_res.status_code == 200, other_res.text
    others = [c for c in other_res.json()["items"] if c["install"]["connector_id"] == install_id]
    assert len(others) == 1


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
# Task 8 — Try It routes (admin + ws).
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seeded_none_auth_install_with_state(
    admin_client: tuple[httpx.AsyncClient, str],
    noauth_template_id: str,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> tuple[str, str]:
    client, workspace_id = admin_client
    res = await client.post(
        f"/api/v1/ws/{workspace_id}/mcp/installs",
        json={
            "template_id": noauth_template_id,
            "install_scope": "workspace",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert res.status_code == 201, res.text
    install_id = res.json()["connector_id"]
    async with db_session_maker() as session:
        from cubebox.models.mcp import MCPConnector

        install = await session.get(MCPConnector, install_id)
        assert install is not None
        install.tools_cache = [
            {"name": "ping", "description": "say hi", "input_schema": {"type": "object"}}
        ]
        await session.commit()
    return install_id, workspace_id


async def test_ws_invoke_tool_returns_result(
    admin_client: tuple[httpx.AsyncClient, str],
    seeded_none_auth_install_with_state: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ws = admin_client
    install_id, ws_id = seeded_none_auth_install_with_state

    async def fake_invoke(server_url, tool_name, arguments, *, headers, timeout, transport):
        return {"echo": arguments, "tool": tool_name}

    monkeypatch.setattr("cubebox.api.routes.v1.ws_mcp._invoke_tool_via_cubepi", fake_invoke)

    res = await client.post(
        f"/api/v1/ws/{ws_id}/mcp/installs/{install_id}/tools/ping/invoke",
        json={"arguments": {"x": 1}},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["result"]["echo"] == {"x": 1}
    assert "duration_ms" in body


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
