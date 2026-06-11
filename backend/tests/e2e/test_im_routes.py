"""E2E for the workspace + admin IM connector routes (Task 15)."""

from __future__ import annotations

import secrets as _secrets
from typing import Any
from unittest.mock import patch

import httpx
import pytest


def _unique_app_id(tag: str) -> str:
    return f"cli_{tag}_{_secrets.token_hex(4)}"


pytestmark = pytest.mark.asyncio


@patch("cubebox.services.im_connector.IMConnectorService._hydrate_bot_open_id")
async def test_workspace_connect_list_delete_feishu_account(
    mock_hydrate: Any,
    async_client: httpx.AsyncClient,
) -> None:
    """A workspace member can connect, list, and disconnect their Feishu bot.

    The /bot/v3/info hydration call is mocked so the test stays hermetic;
    we still exercise the credential store + the IMConnectorService end to end.
    """

    async def _fake_hydrate(app_id: str, app_secret: str, domain: str) -> str:
        return "ou_hydrated_bot"

    mock_hydrate.side_effect = _fake_hydrate

    # Resolve the default workspace from the auto-login fixture.
    from tests.e2e.conftest import DEFAULT_WS_ID

    app_id = _unique_app_id("route")
    create = await async_client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/im/accounts",
        json={
            "platform": "feishu",
            "app_id": app_id,
            "app_secret": "secret",
            "encrypt_key": "ek",
            "verification_token": "vt",
            "domain": "feishu",
            "delivery_mode": "long_connection",
            "acting_user_id": "self",
        },
    )
    assert create.status_code == 201, create.text
    account = create.json()
    assert account["id"].startswith("imac-")
    assert account["platform"] == "feishu"
    assert account["external_account_id"] == app_id
    assert account["enabled"] is True

    listed = await async_client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/im/accounts")
    assert listed.status_code == 200
    assert any(a["id"] == account["id"] for a in listed.json()["accounts"])
    # Secrets must not leak in the list response.
    assert "app_secret" not in listed.text
    assert "encrypt_key" not in listed.text

    deleted = await async_client.delete(f"/api/v1/ws/{DEFAULT_WS_ID}/im/accounts/{account['id']}")
    assert deleted.status_code == 204

    listed_after = await async_client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/im/accounts")
    assert not any(a["id"] == account["id"] for a in listed_after.json()["accounts"])


@patch("cubebox.services.im_connector.IMConnectorService._hydrate_bot_open_id")
async def test_admin_can_list_and_toggle_enabled(
    mock_hydrate: Any,
    async_client: httpx.AsyncClient,
) -> None:
    """The default test user is an org admin and a workspace admin — they can
    drive both the workspace POST/DELETE and the admin list/enable/disable
    routes from the same client."""

    async def _fake_hydrate(app_id: str, app_secret: str, domain: str) -> str:
        return "ou_hydrated_admin"

    mock_hydrate.side_effect = _fake_hydrate

    from tests.e2e.conftest import DEFAULT_WS_ID

    app_id = _unique_app_id("admin")
    create = await async_client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/im/accounts",
        json={
            "platform": "feishu",
            "app_id": app_id,
            "app_secret": "secret",
            "encrypt_key": "ek",
            "verification_token": "vt",
            "domain": "feishu",
            "delivery_mode": "long_connection",
            "acting_user_id": "self",
        },
    )
    assert create.status_code == 201, create.text
    account_id = create.json()["id"]

    listed = await async_client.get("/api/v1/admin/im/accounts")
    assert listed.status_code == 200, listed.text
    assert any(a["id"] == account_id for a in listed.json()["accounts"])

    disabled = await async_client.post(f"/api/v1/admin/im/accounts/{account_id}/disable")
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False

    enabled = await async_client.post(f"/api/v1/admin/im/accounts/{account_id}/enable")
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True

    # Clean up so subsequent tests don't see a stray account.
    await async_client.delete(f"/api/v1/ws/{DEFAULT_WS_ID}/im/accounts/{account_id}")


@patch("cubebox.services.im_connector.IMConnectorService._hydrate_bot_open_id")
async def test_workspace_delete_refuses_account_from_sibling_workspace(
    mock_hydrate: Any,
    async_client: httpx.AsyncClient,
) -> None:
    """Cross-workspace authz: an account created with workspace_id=A must NOT
    be deletable by a request whose ctx.workspace_id=A but where the account
    actually lives in workspace_id=B (different workspace, same org).
    Without scoping the service by workspace_id, a member could delete a
    sibling workspace's connector inside their org.
    """

    async def _fake_hydrate(app_id: str, app_secret: str, domain: str) -> str:
        return "ou_isolation"

    mock_hydrate.side_effect = _fake_hydrate

    from sqlalchemy import text

    from cubebox.db.engine import async_session_maker
    from tests.e2e.conftest import DEFAULT_WS_ID

    app_id = _unique_app_id("isol")
    create = await async_client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/im/accounts",
        json={
            "platform": "feishu",
            "app_id": app_id,
            "app_secret": "secret",
            "encrypt_key": "ek",
            "verification_token": "vt",
            "domain": "feishu",
            "delivery_mode": "long_connection",
            "acting_user_id": "self",
        },
    )
    assert create.status_code == 201, create.text
    account_id = create.json()["id"]

    # Move the account into a different (fake) workspace inside the same org
    # to simulate the cross-workspace scenario. We do this via direct SQL
    # because the public API doesn't allow moving accounts.
    fake_ws_id = "ws-isolation-other"
    async with async_session_maker() as s:
        await s.execute(
            text(
                "INSERT INTO workspaces (id, org_id, name, created_at)"
                " SELECT :ws, org_id, 'other-ws', NOW() FROM workspaces WHERE id = :src"
                " ON CONFLICT (id) DO NOTHING"
            ),
            {"ws": fake_ws_id, "src": DEFAULT_WS_ID},
        )
        await s.execute(
            text("UPDATE im_connector_accounts SET workspace_id = :ws WHERE id = :id"),
            {"ws": fake_ws_id, "id": account_id},
        )
        await s.commit()

    try:
        # This member belongs to DEFAULT_WS_ID, not fake_ws_id. The DELETE
        # call uses DEFAULT_WS_ID in the URL — and must NOT delete an
        # account that lives in fake_ws_id.
        deleted = await async_client.delete(f"/api/v1/ws/{DEFAULT_WS_ID}/im/accounts/{account_id}")
        # Route returns 204 whether or not it found anything (delete is
        # idempotent). What matters is the row still exists.
        assert deleted.status_code == 204

        async with async_session_maker() as s:
            count = (
                await s.execute(
                    text("SELECT COUNT(*) FROM im_connector_accounts WHERE id = :id"),
                    {"id": account_id},
                )
            ).scalar()
            assert count == 1, "cross-workspace delete must NOT remove the row"
    finally:
        async with async_session_maker() as s:
            await s.execute(
                text("DELETE FROM im_connector_accounts WHERE id = :id"),
                {"id": account_id},
            )
            await s.execute(text("DELETE FROM workspaces WHERE id = :id"), {"id": fake_ws_id})
            await s.commit()


async def test_anonymous_cannot_reach_admin_route() -> None:
    """Anonymous (no auth cookie) callers must not get past require_org_admin."""
    import httpx as _httpx

    from tests.e2e.conftest import _lifespan_context, _make_test_app

    app = _make_test_app()
    app.state.deployment_mode = "multi_tenant"
    async with _lifespan_context(app):
        transport = _httpx.ASGITransport(app=app)
        async with _httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/v1/admin/im/accounts")
            assert resp.status_code in (401, 403)
